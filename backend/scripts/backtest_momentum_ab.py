# -*- coding: utf-8 -*-
"""
A/B Backtest: MOMENTUM_SCALPER V1 vs V2 (Bull/Bear Filter)
============================================================
เปรียบเทียบ:
  V1: Original (EMA 200 + regime filter)
  V2: + 3-Layer Bull/Bear Indicator (EMA200 + Supertrend + ADX/DI)

Usage:
  cd D:\VibeCode\Trade
  python backend/scripts/backtest_momentum_ab.py --symbol BTCUSDm --bars 8640
  python backend/scripts/backtest_momentum_ab.py --symbol XAUUSDc --bars 25920
"""
import sys
sys.path.insert(0, "d:/VibeCode/Trade")

import argparse
import json
import logging
import numpy as np
import pandas as pd
from datetime import datetime

from backend.trader.features.volatility import add_volatility_features
from backend.trader.features.structure import add_structure_features, detect_displacement
from backend.trader.features.institutional import add_institutional_features
from backend.trader.features.divergence import detect_rsi_divergence
from backend.trader.features.candle_patterns import detect_candle_patterns
from backend.trader.data.time_utils import time_utils
from backend.trader.regime.classifier import classify_regime
from backend.trader.strategy.momentum_scalper import signal_momentum_scalper
from backend.trader.strategy.momentum_scalper_v2 import signal_momentum_scalper_v2, classify_bull_bear

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("backtest_momentum_ab")

# --- Config ---
with open("d:/VibeCode/Trade/backend/trader/config/settings.json") as f:
    CONFIG = json.load(f)

# --- Symbol-specific contract specs (Exness Standard USD) ---
SYMBOL_SPECS = {
    "XAUUSD": {"point_value_per_lot": 1.0, "spread_points": 30, "contract_size": 100},
    "XAGUSD": {"point_value_per_lot": 0.5, "spread_points": 30, "contract_size": 5000},
    "BTCUSD": {"point_value_per_lot": 0.01, "spread_points": 500, "contract_size": 1},
}

REALISTIC_SPREAD_CAP = {
    "XAUUSD": 50,
    "XAGUSD": 80,
    "BTCUSD": 800,
}


def _get_specs(symbol: str) -> dict:
    sym = symbol.upper()
    for key in SYMBOL_SPECS:
        if key in sym:
            specs = SYMBOL_SPECS[key].copy()
            # Try MT5 live spread
            try:
                import MetaTrader5 as mt5
                from backend.trader.data.mapper import mapper
                if mt5.initialize():
                    broker_sym = mapper.to_broker(symbol)
                    info = mt5.symbol_info(broker_sym)
                    if info is not None:
                        cap = REALISTIC_SPREAD_CAP.get(key, 100)
                        real_spread = min(info.spread, cap)
                        specs["spread_points"] = real_spread
                        print(f"  [MT5] Spread for {broker_sym}: {real_spread} pts")
            except Exception:
                pass
            return specs
    return SYMBOL_SPECS["XAUUSD"].copy()


def load_mt5_data(symbol: str, bars: int) -> pd.DataFrame:
    import MetaTrader5 as mt5
    from backend.trader.data.mapper import mapper
    mt5.shutdown()
    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    broker_sym = mapper.to_broker(symbol)
    mt5.symbol_select(broker_sym, True)
    rates = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M5, 0, bars)
    if rates is None or len(rates) == 0:
        raise ValueError(f"MT5 returned no data for {broker_sym}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"  [DATA] Loaded {len(df)} M5 bars for {broker_sym}")
    return df


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all features needed by both V1 and V2."""
    df = time_utils.add_session_features(df)
    df = add_volatility_features(df)
    df = add_structure_features(df)
    df = detect_displacement(df)
    df = add_institutional_features(df)
    df = detect_rsi_divergence(df)
    df = detect_candle_patterns(df)
    return df


def simulate_trade(signal: dict, future_bars: pd.DataFrame, spread_cost: float) -> dict:
    """Simple SL/TP1 simulation with spread."""
    entry = signal["entry_price"]
    sl = signal["sl"]
    tp1 = signal["tp1"]
    side = signal["side"]

    for _, bar in future_bars.iterrows():
        if side == "BUY":
            eff_entry = entry + spread_cost / 2
            if bar["low"] <= sl:
                return {"result": "SL", "pnl": sl - eff_entry}
            if bar["high"] >= tp1:
                return {"result": "TP", "pnl": tp1 - eff_entry}
        else:
            eff_entry = entry - spread_cost / 2
            if bar["high"] >= sl:
                return {"result": "SL", "pnl": eff_entry - sl}
            if bar["low"] <= tp1:
                return {"result": "TP", "pnl": eff_entry - tp1}

    last_close = float(future_bars.iloc[-1]["close"])
    if side == "BUY":
        return {"result": "TIMEOUT", "pnl": last_close - (entry + spread_cost / 2)}
    else:
        return {"result": "TIMEOUT", "pnl": (entry - spread_cost / 2) - last_close}


def run_strategy_backtest(df: pd.DataFrame, symbol: str, signal_fn, version_label: str,
                          lookback: int = 100, hold_bars: int = 50,
                          initial_equity: float = 100.0) -> dict:
    """Walk-forward backtest using a specific signal function."""
    specs = _get_specs(symbol)
    spread_cost = specs["spread_points"] * 0.01
    contract_size = specs["contract_size"]
    regime_cfg = CONFIG.get("regime", {})

    equity = initial_equity
    peak_equity = initial_equity
    max_dd = 0.0
    trades = []
    cooldown_until = 0
    bull_bear_stats = {"BULL": 0, "BEAR": 0, "NEUTRAL": 0}

    for i in range(lookback, len(df) - hold_bars):
        if i < cooldown_until:
            continue

        window = df.iloc[i - lookback: i + 1]
        regime_res = classify_regime(window, regime_cfg)
        context = {"symbol": symbol, "regime_result": regime_res}

        # Track bull/bear state for V2
        latest = window.iloc[-1]
        close = float(latest['close'])
        from backend.trader.strategy.momentum_scalper_v2 import classify_bull_bear as _bb
        mkt_state, _, _, _ = _bb(latest, close, {"adx_threshold": 20})
        bull_bear_stats[mkt_state] += 1

        signal = signal_fn(window, context)
        if signal is None:
            continue

        # QC: V2 directional check
        if "V2" in version_label:
            if signal["side"] == "BUY" and mkt_state != "BULL":
                print(f"  [QC FAIL] V2 BUY in {mkt_state} at bar {i}!")
            if signal["side"] == "SELL" and mkt_state != "BEAR":
                print(f"  [QC FAIL] V2 SELL in {mkt_state} at bar {i}!")

        # Simple lot size (fixed 0.01 for fair comparison)
        lot = 0.01

        future = df.iloc[i + 1: i + 1 + hold_bars]
        if len(future) == 0:
            continue
        outcome = simulate_trade(signal, future, spread_cost)
        pnl_usd = outcome["pnl"] * lot * contract_size

        equity += pnl_usd
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        if dd > max_dd:
            max_dd = dd

        trades.append({
            "bar": i, "side": signal["side"], "result": outcome["result"],
            "pnl_usd": round(pnl_usd, 4), "equity": round(equity, 2),
            "dd_pct": round(dd, 2),
            "market_state": mkt_state,
            "confidence": signal.get("confidence", 0),
        })

        # Cooldown: 10 bars after trade
        cooldown_until = i + 10

    # Metrics
    if not trades:
        return {"version": version_label, "trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "pf": 0, "max_dd": 0, "net_pnl": 0,
                "final_equity": equity, "bull_bear_stats": bull_bear_stats}

    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    gross_profit = sum(t["pnl_usd"] for t in wins) if wins else 0
    gross_loss = abs(sum(t["pnl_usd"] for t in losses)) if losses else 1e-9

    return {
        "version": version_label,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "pf": round(gross_profit / gross_loss, 2),
        "max_dd": round(max_dd, 2),
        "net_pnl": round(sum(t["pnl_usd"] for t in trades), 4),
        "final_equity": round(equity, 2),
        "avg_conf": round(np.mean([t["confidence"] for t in trades]), 3) if trades else 0,
        "bull_bear_stats": bull_bear_stats,
        "per_side": {
            "BUY": len([t for t in trades if t["side"] == "BUY"]),
            "SELL": len([t for t in trades if t["side"] == "SELL"]),
        },
        "per_result": {
            "TP": len([t for t in trades if t["result"] == "TP"]),
            "SL": len([t for t in trades if t["result"] == "SL"]),
            "TIMEOUT": len([t for t in trades if t["result"] == "TIMEOUT"]),
        },
        "all_trades": trades,
    }


def print_comparison(v1: dict, v2: dict, symbol: str):
    """Pretty-print side-by-side comparison."""
    w = 28
    print(f"\n{'='*65}")
    print(f"  📊 A/B BACKTEST: MOMENTUM_SCALPER V1 vs V2 ({symbol})")
    print(f"{'='*65}")
    print(f"  {'Metric':<20} {'V1 (Original)':>{w}} {'V2 (Bull/Bear)':>{w}}")
    print(f"  {'-'*20} {'-'*w} {'-'*w}")

    rows = [
        ("Trades", v1["trades"], v2["trades"]),
        ("Wins", v1["wins"], v2["wins"]),
        ("Losses", v1["losses"], v2["losses"]),
        ("Win Rate", f"{v1['win_rate']}%", f"{v2['win_rate']}%"),
        ("Profit Factor", v1.get("pf", 0), v2.get("pf", 0)),
        ("Max Drawdown", f"{v1['max_dd']}%", f"{v2['max_dd']}%"),
        ("Net PnL", f"${v1['net_pnl']}", f"${v2['net_pnl']}"),
        ("Final Equity", f"${v1['final_equity']}", f"${v2['final_equity']}"),
        ("Avg Confidence", v1.get("avg_conf", "-"), v2.get("avg_conf", "-")),
    ]
    for label, val1, val2 in rows:
        print(f"  {label:<20} {str(val1):>{w}} {str(val2):>{w}}")

    # Direction breakdown
    if v1.get("per_side"):
        print(f"\n  {'Direction':<20} {'V1':>{w}} {'V2':>{w}}")
        print(f"  {'-'*20} {'-'*w} {'-'*w}")
        for side in ["BUY", "SELL"]:
            print(f"  {side:<20} {v1['per_side'].get(side, 0):>{w}} {v2['per_side'].get(side, 0):>{w}}")

    # Result breakdown
    if v1.get("per_result"):
        print(f"\n  {'Result':<20} {'V1':>{w}} {'V2':>{w}}")
        print(f"  {'-'*20} {'-'*w} {'-'*w}")
        for res in ["TP", "SL", "TIMEOUT"]:
            print(f"  {res:<20} {v1['per_result'].get(res, 0):>{w}} {v2['per_result'].get(res, 0):>{w}}")

    # Bull/Bear distribution
    print(f"\n  📈 Bull/Bear Market Distribution (bars):")
    for state in ["BULL", "BEAR", "NEUTRAL"]:
        v2_count = v2.get("bull_bear_stats", {}).get(state, 0)
        print(f"    {state}: {v2_count} bars")

    # Verdict
    print(f"\n  {'='*65}")
    v2_better = v2["net_pnl"] > v1["net_pnl"]
    v2_safer = v2["max_dd"] <= v1["max_dd"]
    v2_higher_wr = v2["win_rate"] > v1["win_rate"]

    verdict = []
    if v2_better: verdict.append("💰 PnL ดีกว่า")
    if v2_safer: verdict.append("🛡️ DD ต่ำกว่า")
    if v2_higher_wr: verdict.append("🎯 WR สูงกว่า")
    if v2["trades"] < v1["trades"]: verdict.append("⚡ เทรดน้อยลง (กรอง noise)")

    if v2_better and v2_safer:
        print(f"  🏆 VERDICT: V2 (Bull/Bear Filter) ชนะ! {' | '.join(verdict)}")
    elif v2_better:
        print(f"  ✅ VERDICT: V2 กำไรดีกว่า แต่ DD สูงขึ้นเล็กน้อย. {' | '.join(verdict)}")
    elif v2_safer:
        print(f"  🛡️ VERDICT: V2 ปลอดภัยกว่า (DD ต่ำ) แต่กำไรน้อยลง. {' | '.join(verdict)}")
    else:
        print(f"  ⚠️ VERDICT: V1 ยังดีกว่าในเงื่อนไขนี้. ต้อง tune params")
    print(f"  {'='*65}\n")


def main():
    parser = argparse.ArgumentParser(description="A/B Backtest: Momentum Scalper V1 vs V2")
    parser.add_argument("--symbol", default="BTCUSDm")
    parser.add_argument("--bars", type=int, default=8640, help="M5 bars (8640=30d, 25920=90d)")
    parser.add_argument("--equity", type=float, default=100.0)
    parser.add_argument("--lookback", type=int, default=100)
    parser.add_argument("--hold", type=int, default=50)
    args = parser.parse_args()

    print(f"\n{'#'*65}")
    print(f"  MOMENTUM SCALPER A/B TEST")
    print(f"  Symbol: {args.symbol} | Bars: {args.bars} | Equity: ${args.equity}")
    print(f"{'#'*65}")

    # Load data
    df = load_mt5_data(args.symbol, args.bars)
    print(f"  Computing features...")
    df = prepare_features(df)
    print(f"  Features ready. Starting backtest...\n")

    # Run V1
    print(f"  ▶ Running V1 (Original)...")
    v1_result = run_strategy_backtest(
        df, args.symbol, signal_momentum_scalper, "V1_Original",
        lookback=args.lookback, hold_bars=args.hold, initial_equity=args.equity
    )
    print(f"    V1: {v1_result['trades']} trades | WR={v1_result['win_rate']}% | PnL=${v1_result['net_pnl']}")

    # Run V2
    print(f"  ▶ Running V2 (Bull/Bear Filter)...")
    v2_result = run_strategy_backtest(
        df, args.symbol, signal_momentum_scalper_v2, "V2_BullBear",
        lookback=args.lookback, hold_bars=args.hold, initial_equity=args.equity
    )
    print(f"    V2: {v2_result['trades']} trades | WR={v2_result['win_rate']}% | PnL=${v2_result['net_pnl']}")

    # Print comparison
    print_comparison(v1_result, v2_result, args.symbol)

    # Save results
    results = {
        "symbol": args.symbol, "bars": args.bars, "timestamp": datetime.now().isoformat(),
        "v1": {k: v for k, v in v1_result.items() if k != "all_trades"},
        "v2": {k: v for k, v in v2_result.items() if k != "all_trades"},
    }
    out_path = f"d:/VibeCode/Trade/backend/trader/data/backtest_momentum_ab_{args.symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  💾 Results saved: {out_path}\n")


if __name__ == "__main__":
    main()
