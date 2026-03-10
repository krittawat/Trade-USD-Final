# -*- coding: utf-8 -*-
"""
USOIL Strategy Tournament - Per-strategy backtest on US Oil.

Usage:
  cd D:/VibeCode/Trade
  python backend/scripts/backtest_usoil_tournament.py --symbol USOILm --bars 10000
"""

import sys

sys.path.insert(0, "d:/VibeCode/Trade")

import argparse
import json
import logging
from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd

from backend.trader.features.volatility import add_volatility_features
from backend.trader.features.structure_usoil import add_structure_features_usoil, detect_displacement_usoil
from backend.trader.features.institutional import add_institutional_features
from backend.trader.features.divergence import detect_rsi_divergence
from backend.trader.features.candle_patterns import detect_candle_patterns
from backend.trader.data.time_utils import time_utils
from backend.trader.regime.classifier import classify_regime
from backend.trader.liquidity.detector import detect_liquidity_events
from backend.trader.storage.sqlite_db import db

from backend.trader.strategy.gold_elite import signal_gold_elite
from backend.trader.strategy.liquidity_hunter import signal_liquidity_hunter
from backend.trader.strategy.smc_metals import signal_smc_metals
from backend.trader.strategy.antigravity_alpha import signal_antigravity_alpha
from backend.trader.strategy.momentum_scalper_v2 import signal_momentum_scalper_v2
from backend.trader.strategy.momentum_rider import signal_momentum_rider
from backend.trader.strategy.trend_killer import signal_trend_killer
from backend.trader.strategy.sniper_pro import signal_sniper_pro
from backend.trader.strategy.easy_trend import signal_easy_trend
from backend.trader.strategy.fvg_logic import signal_fvg_logic
from backend.trader.strategy.predicta_v4 import signal_predicta_v4
from backend.trader.strategy.usoil_elite import signal_usoil_elite

logging.basicConfig(level=logging.WARNING)

with open("d:/VibeCode/Trade/backend/trader/config/settings.json", "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

# USOIL Specs Based on user provided MT5 Info:
# point: 0.001, spread: 18, contract_size: 1000.0
SYMBOL_SPECS = {
    "XAUUSD": {"point": 0.01, "spread_points": 50, "contract_size": 100.0, "spread_cap": 700},
    "XAGUSD": {"point": 0.001, "spread_points": 80, "contract_size": 5000.0, "spread_cap": 1200},
    "BTCUSD": {"point": 0.01, "spread_points": 800, "contract_size": 1.0, "spread_cap": 1500},
    "USOIL": {"point": 0.001, "spread_points": 25, "contract_size": 1000.0, "spread_cap": 50},
}

STRATEGIES = [
    ("GOLD_ELITE", signal_gold_elite),
    ("LIQUIDITY_HUNTER", signal_liquidity_hunter),
    ("SMC_METALS", signal_smc_metals),
    ("ANTIGRAVITY_ALPHA", signal_antigravity_alpha),
    ("MOMENTUM_SCALPER_V2", signal_momentum_scalper_v2),
    ("MOMENTUM_RIDER", signal_momentum_rider),
    ("TREND_KILLER", signal_trend_killer),
    ("SNIPER_PRO", signal_sniper_pro),
    ("EASY_TREND", signal_easy_trend),
    ("FVG_LOGIC", signal_fvg_logic),
    ("PREDICTA_V4", signal_predicta_v4),
    ("USOIL_ELITE", signal_usoil_elite),
]


def _get_specs(symbol: str) -> dict:
    sym = symbol.upper()
    for key, spec in SYMBOL_SPECS.items():
        if key in sym:
            return spec.copy()
    return SYMBOL_SPECS["USOIL"].copy()


def load_mt5_data(symbol: str, bars: int, specs: dict, timeframe: str = "M5") -> pd.DataFrame:
    from backend.trader.data.mapper import mapper

    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M2": mt5.TIMEFRAME_M2,
        "M3": mt5.TIMEFRAME_M3,
        "M4": mt5.TIMEFRAME_M4,
        "M5": mt5.TIMEFRAME_M5,
        "M6": mt5.TIMEFRAME_M6,
        "M10": mt5.TIMEFRAME_M10,
        "M12": mt5.TIMEFRAME_M12,
        "M15": mt5.TIMEFRAME_M15,
        "M20": mt5.TIMEFRAME_M20,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H2": mt5.TIMEFRAME_H2,
        "H3": mt5.TIMEFRAME_H3,
        "H4": mt5.TIMEFRAME_H4,
        "H6": mt5.TIMEFRAME_H6,
        "H8": mt5.TIMEFRAME_H8,
        "H12": mt5.TIMEFRAME_H12,
        "D1": mt5.TIMEFRAME_D1,
    }

    mt5_tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_M5)

    mt5.shutdown()
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")

    broker_sym = mapper.to_broker(symbol)
    mt5.symbol_select(broker_sym, True)

    info = mt5.symbol_info(broker_sym)
    if info is not None:
        specs["spread_points"] = min(int(info.spread), int(specs.get("spread_cap", info.spread)))
        print(f"  [MT5] Spread for {broker_sym}: {specs['spread_points']} pts")

    rates = mt5.copy_rates_from_pos(broker_sym, mt5_tf, 0, bars)
    if rates is None or len(rates) == 0:
        raise ValueError(f"No data for {broker_sym} ({timeframe})")

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"  [DATA] {len(df)} {timeframe} bars loaded")
    return df


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    df = time_utils.add_session_features(df)
    df = add_volatility_features(df)
    df = add_structure_features_usoil(df)
    df = detect_displacement_usoil(df)
    df = add_institutional_features(df)
    df = detect_rsi_divergence(df)
    df = detect_candle_patterns(df)
    return df


def simulate_trade(signal: dict, future_bars: pd.DataFrame, spread_cost: float) -> dict | None:
    side = signal.get("side")
    entry = float(signal.get("entry_price", 0))
    entry_type = signal.get("entry_type", "MARKET")
    sl = float(signal.get("sl", 0))
    tp1 = float(signal.get("tp1", 0))

    if side not in ("BUY", "SELL"):
        return None
    if entry <= 0 or sl <= 0 or tp1 <= 0:
        return None

    if side == "BUY" and not (sl < entry < tp1):
        return None
    if side == "SELL" and not (tp1 < entry < sl):
        return None

    filled = False
    effective_entry = 0.0

    for _, bar in future_bars.iterrows():
        if side == "BUY":
            if not filled:
                if entry_type == "LIMIT":
                    if bar["low"] <= entry:
                        filled = True
                        effective_entry = entry + spread_cost / 2
                        # Need to check if it immediately hit SL on the same bar it filled
                        if bar["low"] <= sl:
                            return {"result": "SL", "pnl": sl - effective_entry}
                    else:
                        continue # wait for fill
                else: # MARKET
                    filled = True
                    effective_entry = entry + spread_cost / 2

            if filled:
                # Check SL first for safety
                if bar["low"] <= sl:
                    return {"result": "SL", "pnl": sl - effective_entry}
                if bar["high"] >= tp1:
                    return {"result": "TP", "pnl": tp1 - effective_entry}

        else: # SELL
            if not filled:
                if entry_type == "LIMIT":
                    if bar["high"] >= entry:
                        filled = True
                        effective_entry = entry - spread_cost / 2
                        if bar["high"] >= sl:
                            return {"result": "SL", "pnl": effective_entry - sl}
                    else:
                        continue
                else: # MARKET
                    filled = True
                    effective_entry = entry - spread_cost / 2

            if filled:
                # Check SL first for safety
                if bar["high"] >= sl:
                    return {"result": "SL", "pnl": effective_entry - sl}
                if bar["low"] <= tp1:
                    return {"result": "TP", "pnl": effective_entry - tp1}

    if not filled:
        return {"result": "MISSED", "pnl": 0.0}

    last = float(future_bars.iloc[-1]["close"])
    if side == "BUY":
        return {"result": "TIMEOUT", "pnl": last - (entry + spread_cost / 2)}
    return {"result": "TIMEOUT", "pnl": (entry - spread_cost / 2) - last}


def _call_strategy(name: str, signal_fn, window: pd.DataFrame, events: list, context: dict):
    if name == "LIQUIDITY_HUNTER":
        return signal_fn(window, events, context)
    return signal_fn(window, context)


def test_strategy(
    df: pd.DataFrame,
    symbol: str,
    name: str,
    signal_fn,
    specs: dict,
    lookback: int = 220,
    hold: int = 60,
    equity: float = 100.0,
):
    spread_cost = float(specs["spread_points"]) * float(specs["point"])
    contract_size = float(specs["contract_size"])
    regime_cfg = CONFIG.get("regime", {})
    liq_cfg = CONFIG.get("liquidity", {})
    lot = 0.01

    eq = equity
    peak = equity
    max_dd = 0.0
    trades = []
    cooldown_until = 0

    for i in range(lookback, len(df) - hold):
        if i < cooldown_until:
            continue

        window = df.iloc[i - lookback : i + 1]
        regime_res = classify_regime(window, regime_cfg)
        events = detect_liquidity_events(window, liq_cfg)
        context = {"symbol": symbol, "regime_result": regime_res}

        try:
            sig = _call_strategy(name, signal_fn, window, events, context)
        except Exception:
            continue

        if sig is None:
            continue

        future = df.iloc[i + 1 : i + 1 + hold]
        if len(future) == 0:
            continue

        outcome = simulate_trade(sig, future, spread_cost)
        if outcome is None:
            continue

        pnl_usd = outcome["pnl"] * lot * contract_size
        eq += pnl_usd
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

        trades.append(
            {
                "result": outcome["result"],
                "pnl": round(pnl_usd, 4),
                "side": sig["side"],
            }
        )
        cooldown_until = i + 6

    if not trades:
        return {
            "name": name,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "wr": 0.0,
            "pf": 0.0,
            "max_dd": 0.0,
            "net": 0.0,
            "eq": round(equity, 2),
            "tp": 0,
            "sl": 0,
        }

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_profit = sum(t["pnl"] for t in wins) if wins else 0.0
    gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 1e-9

    return {
        "name": name,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "pf": round(gross_profit / gross_loss, 2),
        "max_dd": round(max_dd, 2),
        "net": round(sum(t["pnl"] for t in trades), 4),
        "eq": round(eq, 2),
        "buys": len([t for t in trades if t["side"] == "BUY"]),
        "sells": len([t for t in trades if t["side"] == "SELL"]),
        "tp": len([t for t in trades if t["result"] == "TP"]),
        "sl": len([t for t in trades if t["result"] == "SL"]),
    }


def main():
    parser = argparse.ArgumentParser(description="USOIL Strategy Tournament")
    parser.add_argument("--symbol", default="USOILm")
    parser.add_argument("--tf", "--timeframe", default="M5")
    parser.add_argument("--bars", type=int, default=10000)
    parser.add_argument("--equity", type=float, default=100.0)
    args = parser.parse_args()

    print("\n" + "#" * 72)
    print("  USOIL STRATEGY TOURNAMENT (PER-STRATEGY)")
    print(f"  Symbol: {args.symbol} | TF: {args.tf} | Bars: {args.bars} ")
    print("#" * 72)

    specs = _get_specs(args.symbol)
    df = load_mt5_data(args.symbol, args.bars, specs, args.tf)
    print("  Computing features...")
    df = prepare_features(df)
    print("  Features ready. Testing strategies...\n")

    results = []
    for name, fn in STRATEGIES:
        print(f"  - Testing {name}...", end=" ", flush=True)
        try:
            res = test_strategy(df, args.symbol, name, fn, specs, equity=args.equity)
            results.append(res)
            if res["trades"] > 0:
                sign = "PROFIT" if res["net"] > 0 else "LOSS"
                print(
                    f"{sign} trades={res['trades']} WR={res['wr']}% PF={res['pf']} Net=${res['net']}"
                )
            else:
                print("NO_TRADES")
        except Exception as exc:
            print(f"ERROR: {exc}")
            results.append(
                {
                    "name": name,
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "wr": 0.0,
                    "pf": 0.0,
                    "max_dd": 0.0,
                    "net": 0.0,
                    "eq": args.equity,
                    "error": str(exc),
                }
            )

    results.sort(key=lambda x: x.get("net", 0), reverse=True)

    print("\n" + "=" * 88)
    print(f"  USOIL TOURNAMENT RESULTS ({args.symbol}, {args.tf}, {args.bars} bars)")
    print("=" * 88)
    print(
        f"  {'#':<3} {'Strategy':<22} {'Trades':>7} {'WR%':>6} {'PF':>6} {'MaxDD':>7} {'SL':>5} {'Net PnL':>12} {'Verdict':>9}"
    )
    print(
        f"  {'-'*3} {'-'*22} {'-'*7} {'-'*6} {'-'*6} {'-'*7} {'-'*5} {'-'*12} {'-'*9}"
    )

    for idx, row in enumerate(results, start=1):
        if row["trades"] == 0:
            verdict = "SKIP"
        elif row["net"] > 0 and row["wr"] >= 50 and row["pf"] >= 1.3:
            verdict = "BEST"
        elif row["net"] > 0:
            verdict = "OK"
        elif row["max_dd"] > 10:
            verdict = "KILL"
        else:
            verdict = "BAD"

        print(
            f"  {idx:<3} {row['name']:<22} {row['trades']:>7} {row.get('wr',0):>5.1f}% "
            f"{row.get('pf',0):>5.2f} {row.get('max_dd',0):>6.2f}% {row.get('sl',0):>5} "
            f"{row.get('net',0):>+11.2f}$ {verdict:>9}"
        )

    profitable = [r for r in results if r.get("net", 0) > 0 and r["trades"] > 0]
    losing = [r for r in results if r.get("net", 0) <= 0 and r["trades"] > 0]
    inactive = [r for r in results if r["trades"] == 0]

    print("\n" + "=" * 88)
    print("  SUMMARY")
    print(f"    Profitable: {len(profitable)}")
    print(f"    Losing:     {len(losing)}")
    print(f"    Inactive:   {len(inactive)}")

    if profitable:
        best = profitable[0]
        print(
            f"  CHAMPION: {best['name']} | Net ${best['net']} | WR={best['wr']}% | PF={best['pf']}"
        )

    # Save to SQLite DB
    for res in results:
        # Ignore complete skips or errors
        if "error" in res or res.get("trades", 0) == 0:
            continue
        db.record_tournament_result(args.symbol, args.tf, res)

    out_path = (
        "d:/VibeCode/Trade/backend/trader/data/"
        f"usoil_tournament_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "symbol": args.symbol,
                "timeframe": args.tf,
                "bars": args.bars,
                "results": results,
                "timestamp": datetime.now().isoformat(),
            },
            f,
            indent=2,
            default=str,
        )
    print(f"  Saved DB and JSON: {out_path}\n")


if __name__ == "__main__":
    main()
