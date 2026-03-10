# -*- coding: utf-8 -*-
"""
Backtest: SL Fix Comparison (V1 Tight SL vs V2 Widened SL)
===========================================================
เปรียบเทียบ Win Rate, PF, Max DD ระหว่าง SL เก่า (แคบ) vs SL ใหม่ (กว้าง)
สำหรับ BTC บน M3 timeframe (30 วัน)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import argparse
import json
import logging
import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from backend.trader.data.fetcher import fetcher
from backend.trader.features.volatility import add_volatility_features
from backend.trader.features.structure import add_structure_features

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("backtest_sl_fix")

# ── Contract Specs ─────────────────────────────────────────
SYMBOL_SPECS = {
    "XAUUSD": {"spread_points": 50, "contract_size": 100},
    "BTCUSD": {"spread_points": 500, "contract_size": 1},
    "XAGUSD": {"spread_points": 80, "contract_size": 5000},
}

TF_MAP = {
    "M3": mt5.TIMEFRAME_M3,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
}


def load_data(symbol: str, tf_str: str, bars: int) -> pd.DataFrame:
    tf = TF_MAP.get(tf_str, mt5.TIMEFRAME_M5)
    df = fetcher.get_rates(symbol, tf, bars)
    if df is None or len(df) < 200:
        return None
    df = add_volatility_features(df)
    df = add_structure_features(df)
    return df


def simulate_trade(signal: dict, future_bars: pd.DataFrame, spread_cost: float) -> dict:
    """Simple bar-by-bar SL/TP simulation with spread."""
    entry = signal['entry_price']
    sl = signal['sl']
    tp = signal['tp1']
    side = signal['side']

    for _, bar in future_bars.iterrows():
        if side == "BUY":
            # Check SL first (worst case)
            if bar['low'] <= sl:
                pnl = (sl - entry) - spread_cost
                return {"result": "SL", "pnl": pnl, "bars_held": _ + 1}
            # Check TP
            if bar['high'] >= tp:
                pnl = (tp - entry) - spread_cost
                return {"result": "TP", "pnl": pnl, "bars_held": _ + 1}
        else:
            if bar['high'] >= sl:
                pnl = (entry - sl) - spread_cost
                return {"result": "SL", "pnl": pnl, "bars_held": _ + 1}
            if bar['low'] <= tp:
                pnl = (entry - tp) - spread_cost
                return {"result": "TP", "pnl": pnl, "bars_held": _ + 1}

    # Expired (no SL/TP hit)
    last_close = future_bars.iloc[-1]['close']
    if side == "BUY":
        pnl = (last_close - entry) - spread_cost
    else:
        pnl = (entry - last_close) - spread_cost
    return {"result": "EXPIRE", "pnl": pnl, "bars_held": len(future_bars)}


def generate_signals_v1(df: pd.DataFrame, i: int, symbol: str) -> dict:
    """OLD tight SL logic (V1): 0.6x ATR SL, 1.5R TP."""
    latest = df.iloc[i]
    close = float(latest['close'])
    atr = float(latest.get('atr', close * 0.005))
    if atr <= 0 or np.isnan(atr):
        return None

    ema_200 = float(latest.get('ema_200', close))
    ema_fast = float(latest.get('ema_fast', close))

    sl_mult = 0.6   # OLD tight SL
    tp_rr = 1.5     # OLD TP

    # Simple momentum signal
    roc = float(latest.get('roc_5', 0))
    if abs(roc) < 0.05:
        return None

    if roc > 0 and close > ema_200:
        sl = close - (atr * sl_mult)
        risk = close - sl
        if risk <= 0: return None
        tp = close + risk * tp_rr
        return {"symbol": symbol, "side": "BUY", "entry_price": close,
                "sl": sl, "tp1": tp, "model": "V1_TIGHT_SL"}

    if roc < 0 and close < ema_200:
        sl = close + (atr * sl_mult)
        risk = sl - close
        if risk <= 0: return None
        tp = close - risk * tp_rr
        return {"symbol": symbol, "side": "SELL", "entry_price": close,
                "sl": sl, "tp1": tp, "model": "V1_TIGHT_SL"}

    return None


def generate_signals_v2(df: pd.DataFrame, i: int, symbol: str) -> dict:
    """NEW widened SL logic (V2): 1.2x ATR SL, 2.0R TP + SL Floor 1.5x ATR."""
    latest = df.iloc[i]
    close = float(latest['close'])
    atr = float(latest.get('atr', close * 0.005))
    if atr <= 0 or np.isnan(atr):
        return None

    ema_200 = float(latest.get('ema_200', close))
    ema_fast = float(latest.get('ema_fast', close))

    sl_mult = 1.2   # NEW wider SL
    tp_rr = 2.0     # NEW higher TP
    sl_floor = 1.5  # Minimum SL distance in ATR

    # Same momentum signal
    roc = float(latest.get('roc_5', 0))
    if abs(roc) < 0.05:
        return None

    if roc > 0 and close > ema_200:
        sl_dist = max(atr * sl_mult, atr * sl_floor)  # SL Floor enforcement
        sl = close - sl_dist
        risk = close - sl
        if risk <= 0: return None
        tp = close + risk * tp_rr
        return {"symbol": symbol, "side": "BUY", "entry_price": close,
                "sl": sl, "tp1": tp, "model": "V2_WIDE_SL"}

    if roc < 0 and close < ema_200:
        sl_dist = max(atr * sl_mult, atr * sl_floor)
        sl = close + sl_dist
        risk = sl - close
        if risk <= 0: return None
        tp = close - risk * tp_rr
        return {"symbol": symbol, "side": "SELL", "entry_price": close,
                "sl": sl, "tp1": tp, "model": "V2_WIDE_SL"}

    return None


def run_backtest(df: pd.DataFrame, symbol: str, signal_fn, label: str,
                 lookback: int = 100, hold_bars: int = 30,
                 initial_equity: float = 100.0) -> dict:
    """Walk-forward backtest."""
    specs = SYMBOL_SPECS.get(symbol, SYMBOL_SPECS["BTCUSD"])
    spread_cost = specs["spread_points"] * 0.01  # Convert to price

    results = []
    equity = initial_equity
    max_equity = initial_equity
    max_dd = 0.0
    cooldown = 0

    for i in range(lookback, len(df) - hold_bars - 1):
        if cooldown > 0:
            cooldown -= 1
            continue

        signal = signal_fn(df, i, symbol)
        if signal is None:
            continue

        future = df.iloc[i+1:i+1+hold_bars].reset_index(drop=True)
        if len(future) < 5:
            continue

        trade = simulate_trade(signal, future, spread_cost)

        # Risk-based PnL (simplified: 1% equity risk per trade)
        risk_dist = abs(signal['entry_price'] - signal['sl'])
        if risk_dist <= 0:
            continue
        risk_usd = equity * 0.02  # 2% risk
        lot = risk_usd / risk_dist
        pnl_usd = trade['pnl'] * lot

        equity += pnl_usd
        max_equity = max(max_equity, equity)
        dd = (max_equity - equity) / max_equity * 100
        max_dd = max(max_dd, dd)

        results.append({
            "result": trade['result'],
            "pnl_usd": pnl_usd,
            "bars_held": trade['bars_held'],
            "sl_dist": risk_dist,
        })

        cooldown = 3  # 3-bar cooldown

    return {
        "label": label,
        "total_trades": len(results),
        "wins": sum(1 for r in results if r['pnl_usd'] > 0),
        "losses": sum(1 for r in results if r['pnl_usd'] <= 0),
        "tp_hits": sum(1 for r in results if r['result'] == 'TP'),
        "sl_hits": sum(1 for r in results if r['result'] == 'SL'),
        "total_pnl": sum(r['pnl_usd'] for r in results),
        "avg_win": np.mean([r['pnl_usd'] for r in results if r['pnl_usd'] > 0]) if any(r['pnl_usd'] > 0 for r in results) else 0,
        "avg_loss": np.mean([r['pnl_usd'] for r in results if r['pnl_usd'] <= 0]) if any(r['pnl_usd'] <= 0 for r in results) else 0,
        "max_dd_pct": max_dd,
        "final_equity": equity,
        "avg_sl_dist": np.mean([r['sl_dist'] for r in results]) if results else 0,
        "win_rate": (sum(1 for r in results if r['pnl_usd'] > 0) / len(results) * 100) if results else 0,
        "profit_factor": (
            abs(sum(r['pnl_usd'] for r in results if r['pnl_usd'] > 0)) /
            abs(sum(r['pnl_usd'] for r in results if r['pnl_usd'] <= 0) + 1e-9)
        ) if results else 0,
    }


def print_comparison(v1: dict, v2: dict, symbol: str):
    """Pretty print comparison."""
    print(f"\n{'='*70}")
    print(f"  📊 SL FIX BACKTEST COMPARISON — {symbol}")
    print(f"{'='*70}")
    print(f"{'Metric':<25} {'V1 (Tight SL)':>20} {'V2 (Wide SL)':>20}")
    print(f"{'-'*65}")

    metrics = [
        ("Total Trades", f"{v1['total_trades']}", f"{v2['total_trades']}"),
        ("Win Rate", f"{v1['win_rate']:.1f}%", f"{v2['win_rate']:.1f}%"),
        ("TP Hits", f"{v1['tp_hits']}", f"{v2['tp_hits']}"),
        ("SL Hits", f"{v1['sl_hits']}", f"{v2['sl_hits']}"),
        ("Profit Factor", f"{v1['profit_factor']:.2f}", f"{v2['profit_factor']:.2f}"),
        ("Total PnL", f"${v1['total_pnl']:.2f}", f"${v2['total_pnl']:.2f}"),
        ("Avg Win", f"${v1['avg_win']:.2f}", f"${v2['avg_win']:.2f}"),
        ("Avg Loss", f"${v1['avg_loss']:.2f}", f"${v2['avg_loss']:.2f}"),
        ("Max Drawdown", f"{v1['max_dd_pct']:.1f}%", f"{v2['max_dd_pct']:.1f}%"),
        ("Final Equity", f"${v1['final_equity']:.2f}", f"${v2['final_equity']:.2f}"),
        ("Avg SL Distance", f"${v1['avg_sl_dist']:.2f}", f"${v2['avg_sl_dist']:.2f}"),
    ]

    for name, val1, val2 in metrics:
        # Color indicator
        print(f"  {name:<23} {val1:>20} {val2:>20}")

    # Verdict
    print(f"\n{'─'*65}")
    imp_wr = v2['win_rate'] - v1['win_rate']
    imp_pf = v2['profit_factor'] - v1['profit_factor']
    imp_dd = v1['max_dd_pct'] - v2['max_dd_pct']

    print(f"  📈 Win Rate Change:     {imp_wr:+.1f}%")
    print(f"  📈 PF Change:           {imp_pf:+.2f}")
    print(f"  📉 DD Improvement:      {imp_dd:+.1f}%")

    if v2['win_rate'] > v1['win_rate'] and v2['profit_factor'] > v1['profit_factor']:
        print(f"\n  ✅ V2 (Wide SL) WINS — Better Win Rate + Better Profit Factor")
    elif v2['win_rate'] > v1['win_rate']:
        print(f"\n  ✅ V2 (Wide SL) WINS — Better Win Rate (SL hit rate down)")
    elif v2['profit_factor'] > v1['profit_factor']:
        print(f"\n  ✅ V2 (Wide SL) WINS — Better Profit Factor")
    else:
        print(f"\n  ⚠️ V1 outperforms — review parameters")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="SL Fix Comparison Backtest")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--bars", type=int, default=8640)
    parser.add_argument("--tf", default="M3")
    args = parser.parse_args()

    if not fetcher.connect():
        print("❌ Cannot connect to MT5")
        return

    print(f"\n🔄 Loading {args.symbol} {args.tf} data ({args.bars} bars)...")
    df = load_data(args.symbol, args.tf, args.bars)

    if df is None:
        print(f"❌ No data for {args.symbol}")
        fetcher.disconnect()
        return

    print(f"✅ Loaded {len(df)} bars | Range: {df.index[0]} → {df.index[-1]}")

    print(f"\n🧪 Running V1 (Tight SL = 0.6x ATR)...")
    v1 = run_backtest(df, args.symbol, generate_signals_v1, "V1_TIGHT_SL")

    print(f"🧪 Running V2 (Wide SL = 1.2x ATR + Floor 1.5x)...")
    v2 = run_backtest(df, args.symbol, generate_signals_v2, "V2_WIDE_SL")

    print_comparison(v1, v2, args.symbol)

    fetcher.disconnect()


if __name__ == "__main__":
    main()
