"""
Gold WR60 Strategy Backtest & Tuning
=====================================
Backtest the new GoldScalpWR60 strategy over 200 days.
Grid search with multiple TP/SL/score configurations to hit >60% WR.

Usage:
    python backend/scripts/backtest_gold_wr60.py
"""
print("🥇 Gold WR60 Tuning — Starting...", flush=True)

import sys
import os
import time
import random
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Tuple
from copy import deepcopy
from itertools import product

logging.disable(logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

from app.core.config import get_settings
from app.execution.backtester import BacktestTrade, BacktestResult
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile, AccountState
from app.brain.regime import classify_regime
from app.risk.sizing import calculate_lot_size
from app.risk.trailing import TrailingManager
from scripts.backtest_full import FullFeatureBacktester, _get_sl_tp

settings = get_settings()

SYMBOL = "XAUUSDc"
CONTRACT_SIZE = 100.0
POINT = 0.01
DIGITS = 2
DAYS = 200


# ─── Parameter Configurations ───
# Focus on tight TP to boost WR
PARAM_GRID = [
    # v1: Ultra-tight TP (1.0 ATR) — max WR
    {"label": "ULTRA_TIGHT",  "SL_ATR_MULT": 1.5, "TP_ATR_MULT": 1.0, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 2.0},
    # v2: Tight (1.2 ATR) — balanced
    {"label": "TIGHT_1.2",    "SL_ATR_MULT": 1.5, "TP_ATR_MULT": 1.2, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 2.0},
    # v3: Medium (1.5 ATR) — moderate WR
    {"label": "MEDIUM_1.5",   "SL_ATR_MULT": 1.5, "TP_ATR_MULT": 1.5, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 2.0},
    # v4: RR 1:1 (SL=TP)
    {"label": "RR_1to1",      "SL_ATR_MULT": 1.5, "TP_ATR_MULT": 1.5, "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 3.0, "MIN_SL_DISTANCE": 2.5},
    # v5: Micro scalp (0.8 ATR TP) — highest WR possible
    {"label": "MICRO_SCALP",  "SL_ATR_MULT": 1.2, "TP_ATR_MULT": 0.8, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 2.0},
    # v6: EMA Tight (fast EMA, tight TP)
    {"label": "EMA_TIGHT",    "SL_ATR_MULT": 1.5, "TP_ATR_MULT": 1.0, "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5, "EMA_FAST": 14, "EMA_SLOW": 34, "MIN_SL_DISTANCE": 2.0},
    # v7: Conservative (wider SL, tight TP)
    {"label": "WIDE_SL_TIGHT_TP", "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 1.0, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 3.0},
    # v8: Balanced Pro v2
    {"label": "BALANCED_V2",  "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 1.2, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 2.5},
    # v9: RSI Focused (tighter RSI)
    {"label": "RSI_FOCUS",    "SL_ATR_MULT": 1.5, "TP_ATR_MULT": 1.0, "RSI_PERIOD": 10, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 2.0},
    # v10: ADX Strict (only strong trends)
    {"label": "ADX_STRICT",   "SL_ATR_MULT": 1.5, "TP_ATR_MULT": 1.2, "ADX_PERIOD": 20, "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0, "MIN_SL_DISTANCE": 2.0},
]


def apply_params(strategy, params: dict):
    """Apply parameter overrides to strategy."""
    for key, val in params.items():
        if key == "label":
            continue
        if hasattr(strategy, key):
            setattr(strategy, key, val)


def run_backtest(candles: pd.DataFrame, params: dict, initial_equity: float = 10000.0):
    """Run a single Gold WR60 backtest."""
    random.seed(42)
    np.random.seed(42)

    from app.strategy.templates.gold_scalp_wr60 import GoldScalpWR60Strategy
    strategy = GoldScalpWR60Strategy()
    apply_params(strategy, params)

    bt = FullFeatureBacktester(strategy, initial_equity=initial_equity)
    result = bt.run(candles, SYMBOL, contract_size=CONTRACT_SIZE, point=POINT, digits=DIGITS)
    return result


def main():
    print("=" * 90)
    print("🥇 GOLD WR60 STRATEGY — PARAMETER TUNING")
    print(f"   Symbol: {SYMBOL} | Days: {DAYS} | TF: M5 | Configs: {len(PARAM_GRID)}")
    print("=" * 90)

    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, utc_from, utc_to)

    if rates is None or len(rates) == 0:
        print(f"❌ No data for {SYMBOL}")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"✅ Data: {len(df)} bars ({df['time'].iloc[0]} → {df['time'].iloc[-1]})")
    print()

    # ── Also run original GoldScalpPro as baseline ──
    print("[0] Running ORIGINAL GoldScalpPro baseline...", end=" ", flush=True)
    random.seed(42); np.random.seed(42)
    from app.strategy.templates.gold_scalp_pro import GoldScalpProStrategy
    baseline_strat = GoldScalpProStrategy()
    baseline_bt = FullFeatureBacktester(baseline_strat, initial_equity=10000.0)
    baseline_result = baseline_bt.run(df, SYMBOL, contract_size=CONTRACT_SIZE, point=POINT, digits=DIGITS)
    print("Done")

    # ── Run Grid ──
    results = []
    for idx, params in enumerate(PARAM_GRID, 1):
        label = params.get("label", f"SET_{idx}")
        print(f"[{idx}/{len(PARAM_GRID)}] {label}...", end=" ", flush=True)
        t0 = time.monotonic()
        result = run_backtest(df, params)
        elapsed = time.monotonic() - t0
        results.append((params, result))
        print(f"Done ({elapsed:.1f}s)")

    # ── Summary Table ──
    print()
    print("=" * 110)
    print(f"{'Config':<22s} | {'Trades':>6} | {'Wins':>5} | {'Loss':>5} | {'WR%':>6} | {'PF':>5} | {'P&L':>11} | {'DD%':>5} | {'Exp':>7} |")
    print("-" * 110)

    # Baseline first
    br = baseline_result
    pnl_icon = "✅" if br.total_profit_usd >= 0 else "❌"
    wr_icon = "🏆" if br.win_rate >= 60 else ("⭐" if br.win_rate >= 55 else "")
    exp = round(br.total_profit_usd / br.total_trades, 2) if br.total_trades > 0 else 0
    print(f"{'BASELINE (PRO)':<22s} | {br.total_trades:>6} | {br.winning_trades:>5} | {br.losing_trades:>5} | {br.win_rate:>5.1f}% | {br.profit_factor:>5.2f} | ${br.total_profit_usd:>+9.2f} | {br.max_drawdown_pct:>4.1f}% | ${exp:>+5.2f} | {pnl_icon} {wr_icon}")
    print("-" * 110)

    # All WR60 configs sorted by WR desc
    sorted_results = sorted(results, key=lambda x: (x[1].win_rate, x[1].total_profit_usd), reverse=True)

    best_wr60 = None
    for params, r in sorted_results:
        label = params.get("label", "?")
        pnl_icon = "✅" if r.total_profit_usd >= 0 else "❌"
        wr_icon = "🏆" if r.win_rate >= 60 else ("⭐" if r.win_rate >= 55 else "")
        exp = round(r.total_profit_usd / r.total_trades, 2) if r.total_trades > 0 else 0

        prefix = ">>> " if r.win_rate >= 60 and r.total_profit_usd > 0 else "    "
        print(f"{prefix}{label:<18s} | {r.total_trades:>6} | {r.winning_trades:>5} | {r.losing_trades:>5} | {r.win_rate:>5.1f}% | {r.profit_factor:>5.2f} | ${r.total_profit_usd:>+9.2f} | {r.max_drawdown_pct:>4.1f}% | ${exp:>+5.2f} | {pnl_icon} {wr_icon}")

        if best_wr60 is None and r.win_rate >= 60 and r.total_profit_usd > 0:
            best_wr60 = (params, r)

    print("=" * 110)

    # ── Winner Analysis ──
    if best_wr60:
        bp, br = best_wr60
        print()
        print("🏆🏆🏆 WINNING CONFIGURATION FOUND! 🏆🏆🏆")
        print(f"   Config: {bp.get('label', '?')}")
        print(f"   Win Rate: {br.win_rate}% ({'✅ MEETS TARGET' if br.win_rate >= 60 else ''})")
        print(f"   Profit Factor: {br.profit_factor}")
        print(f"   Total P&L: ${br.total_profit_usd:.2f}")
        print(f"   Max DD: {br.max_drawdown_pct}%")
        print(f"   Trades: {br.total_trades} ({br.winning_trades}W / {br.losing_trades}L)")
        print()
        print("   Parameters to apply:")
        for k, v in bp.items():
            if k == "label": continue
            print(f"     {k} = {v}")

        # Per-regime
        if br.per_regime:
            print()
            print("   Per-Regime Breakdown:")
            for regime, stats in br.per_regime.items():
                total_r = stats.get('wins', 0) + stats.get('losses', 0)
                wr_r = round(stats['wins'] / total_r * 100, 1) if total_r > 0 else 0
                print(f"     {regime:<20s}: WR={wr_r:>5.1f}% | N={total_r:>3} | P&L=${stats.get('pnl', 0):>+.2f}")
    else:
        # Find closest
        best_wr = max(results, key=lambda x: x[1].win_rate)
        best_pnl = max(results, key=lambda x: x[1].total_profit_usd)
        print()
        print("⚠️  No config hit both WR≥60% AND Profitable.")
        print(f"   Highest WR:  {best_wr[0].get('label','?')} — {best_wr[1].win_rate}% WR, ${best_wr[1].total_profit_usd:.2f}")
        print(f"   Highest P&L: {best_pnl[0].get('label','?')} — {best_pnl[1].win_rate}% WR, ${best_pnl[1].total_profit_usd:.2f}")
        print()
        print("   💡 Recommendation: Try further tightening TP or adding reversal filters.")

    mt5.shutdown()
    print()
    print("✅ Gold WR60 Tuning Complete!")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
