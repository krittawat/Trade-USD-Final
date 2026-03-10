"""
Gold WR60 Final Optimization — Fine-tune around WIDE_SL_TIGHT_TP winner.
=========================================================================
We found: SL=2.0, TP=1.0, ST(7,2.0), MIN_SL=3.0 → WR=67.5%, PF=1.0, $35.78
Now we micro-tune to maximize PF and P&L while keeping WR ≥ 60%.

Usage:
    python backend/scripts/backtest_gold_final.py
"""
print("🥇 Gold FINAL Optimization — Starting...", flush=True)

import sys
import os
import time
import random
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Tuple

logging.disable(logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

from app.core.config import get_settings
from app.execution.backtester import BacktestTrade, BacktestResult
from scripts.backtest_full import FullFeatureBacktester, _get_sl_tp

settings = get_settings()

SYMBOL = "XAUUSDc"
CONTRACT_SIZE = 100.0
POINT = 0.01
DIGITS = 2
DAYS = 200


# ─── Fine-tune grid around the winner ─── 
PARAM_GRID = [
    # Base winner
    {"label": "WINNER_BASE",         "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 1.0, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 3.0},
    # Tiny TP bump (1.1)
    {"label": "TP_1.1",              "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 1.1, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 3.0},
    # Tiny TP bump (1.15)
    {"label": "TP_1.15",             "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 1.15, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 3.0},
    # SL tighter (1.8), TP 1.0
    {"label": "SL_1.8_TP_1.0",      "SL_ATR_MULT": 1.8, "TP_ATR_MULT": 1.0, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 3.0},
    # SL wider (2.2), TP 1.0
    {"label": "SL_2.2_TP_1.0",      "SL_ATR_MULT": 2.2, "TP_ATR_MULT": 1.0, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 3.0},
    # SL wider (2.5), TP 1.0 — max protection
    {"label": "SL_2.5_TP_1.0",      "SL_ATR_MULT": 2.5, "TP_ATR_MULT": 1.0, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 3.5},
    # SL 2.0, TP 1.1, ST(10,2.5) — different ST
    {"label": "ST10_SL2_TP1.1",     "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 1.1, "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5, "MIN_SL_DISTANCE": 3.0},
    # SL 2.0, TP 1.0, MIN_SL=2.5
    {"label": "MIN_SL_2.5",         "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 1.0, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 2.5},
    # SL 2.0, TP 0.9 (even tighter TP)
    {"label": "TP_0.9",             "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 0.9, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 3.0},
    # SL 2.5, TP 1.2 — wider both 
    {"label": "SL_2.5_TP_1.2",      "SL_ATR_MULT": 2.5, "TP_ATR_MULT": 1.2, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 3.0},
    # EMA speed test: 14/34
    {"label": "EMA_14_34",          "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 1.0, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 3.0, "EMA_FAST": 14, "EMA_SLOW": 34},
    # ADX 20 (stricter trend)
    {"label": "ADX_20",             "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 1.0, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 3.0, "ADX_PERIOD": 20},
]


def apply_params(strategy, params: dict):
    for key, val in params.items():
        if key == "label": continue
        if hasattr(strategy, key):
            setattr(strategy, key, val)


def run_backtest(candles, params, initial_equity=10000.0):
    random.seed(42)
    np.random.seed(42)
    from app.strategy.templates.gold_scalp_wr60 import GoldScalpWR60Strategy
    strategy = GoldScalpWR60Strategy()
    apply_params(strategy, params)
    bt = FullFeatureBacktester(strategy, initial_equity=initial_equity)
    result = bt.run(candles, SYMBOL, contract_size=CONTRACT_SIZE, point=POINT, digits=DIGITS)
    return result


def main():
    print("=" * 100)
    print("🥇 GOLD WR60 — FINAL FINE-TUNE OPTIMIZATION")
    print(f"   Symbol: {SYMBOL} | Days: {DAYS} | Configs: {len(PARAM_GRID)}")
    print("=" * 100)

    if not mt5.initialize():
        print("❌ MT5 Init failed"); return

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, utc_from, utc_to)

    if rates is None or len(rates) == 0:
        print(f"❌ No data for {SYMBOL}"); mt5.shutdown(); return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"✅ Data: {len(df)} bars ({df['time'].iloc[0]} → {df['time'].iloc[-1]})\n")

    results = []
    for idx, params in enumerate(PARAM_GRID, 1):
        label = params.get("label", f"SET_{idx}")
        print(f"[{idx}/{len(PARAM_GRID)}] {label}...", end=" ", flush=True)
        t0 = time.monotonic()
        result = run_backtest(df, params)
        elapsed = time.monotonic() - t0
        results.append((params, result))
        print(f"Done ({elapsed:.1f}s) | WR={result.win_rate}% PF={result.profit_factor} P&L=${result.total_profit_usd:.2f}")

    # ── Summary ──
    print("\n" + "=" * 115)
    print(f"    {'Config':<22s} | {'Trades':>6} | {'W':>4} | {'L':>4} | {'WR%':>6} | {'PF':>5} | {'P&L':>11} | {'DD%':>5} | {'Exp$/T':>7} |")
    print("-" * 115)

    sorted_results = sorted(results, key=lambda x: (
        1 if x[1].win_rate >= 60 and x[1].total_profit_usd > 0 else 0,
        x[1].total_profit_usd
    ), reverse=True)

    best = None
    for params, r in sorted_results:
        label = params.get("label", "?")
        exp = round(r.total_profit_usd / r.total_trades, 2) if r.total_trades > 0 else 0
        meets = r.win_rate >= 60 and r.total_profit_usd > 0
        marker = "🏆" if meets and r.profit_factor >= 1.2 else ("✅" if meets else ("⭐" if r.win_rate >= 60 else "❌"))
        prefix = ">>> " if meets and best is None else "    "
        if meets and best is None:
            best = (params, r)
        print(f"{prefix}{label:<18s} | {r.total_trades:>6} | {r.winning_trades:>4} | {r.losing_trades:>4} | {r.win_rate:>5.1f}% | {r.profit_factor:>5.2f} | ${r.total_profit_usd:>+9.2f} | {r.max_drawdown_pct:>4.1f}% | ${exp:>+5.2f} | {marker}")

    print("=" * 115)

    if best:
        bp, br = best
        print(f"\n🏆 BEST CONFIG: {bp.get('label', '?')}")
        print(f"   WR={br.win_rate}% | PF={br.profit_factor} | P&L=${br.total_profit_usd:.2f} | DD={br.max_drawdown_pct}% | {br.total_trades} trades")
        print(f"\n   Production Parameters (for gold_scalp_wr60.py):")
        for k, v in bp.items():
            if k == "label": continue
            print(f"     {k} = {v}")

        if br.per_regime:
            print(f"\n   Per-Regime:")
            for regime, stats in br.per_regime.items():
                total_r = stats.get('wins', 0) + stats.get('losses', 0)
                wr_r = round(stats['wins'] / total_r * 100, 1) if total_r > 0 else 0
                print(f"     {regime:<20s}: WR={wr_r:>5.1f}% | N={total_r:>3} | P&L=${stats.get('pnl', 0):>+.2f}")

    mt5.shutdown()

    # ── Save to SQLite DB ──
    print("\n💾 Saving results to SQLite DB...")
    try:
        from app.core.config import get_settings
        from app.db.sqlite import SQLiteStore
        db = SQLiteStore(get_settings())
        db.connect()

        # Save ALL results as audit trail
        for params, r in results:
            exp = round(r.total_profit_usd / r.total_trades, 2) if r.total_trades > 0 else 0
            db.save_backtest_result(
                symbol=SYMBOL,
                strategy_name="GOLD_SCALP_WR60",
                label=params.get("label", ""),
                params={k: v for k, v in params.items() if k != "label"},
                win_rate=r.win_rate,
                profit_factor=r.profit_factor,
                total_pnl=r.total_profit_usd,
                max_drawdown_pct=r.max_drawdown_pct,
                total_trades=r.total_trades,
                winning_trades=r.winning_trades,
                losing_trades=r.losing_trades,
                backtest_days=DAYS,
                expectancy=exp,
                per_regime=r.per_regime or {},
            )
        print(f"   ✅ Saved {len(results)} backtest results to audit trail")

        # Save BEST config as active strategy params
        if best:
            bp, br = best
            clean_params = {k: v for k, v in bp.items() if k != "label"}
            db.save_strategy_params(
                symbol=SYMBOL,
                strategy_name="GOLD_SCALP_WR60",
                params=clean_params,
                win_rate=br.win_rate,
                profit_factor=br.profit_factor,
                total_pnl=br.total_profit_usd,
                max_drawdown_pct=br.max_drawdown_pct,
                total_trades=br.total_trades,
                backtest_days=DAYS,
                label=bp.get("label", ""),
            )
            print(f"   ✅ Saved winning config '{bp.get('label','')}' to strategy_params")
            print(f"      → Use: db.get_strategy_params('{SYMBOL}', 'GOLD_SCALP_WR60')")

        db.disconnect()
    except Exception as e:
        print(f"   ⚠️ DB save failed: {e}")

    print("\n✅ Final Optimization Complete!")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
