"""
Gold Strategy Isolation & Tuning Backtest
==========================================
Dedicated backtester for XAUUSD using GoldScalpPro strategy.
Runs a parameter grid search over 200 days to find optimal settings
targeting >60% Win Rate + Profitability.

Usage:
    python backend/scripts/backtest_gold_tune.py
"""
print("🥇 Gold Strategy Tuning — Starting...", flush=True)

import sys
import os
import time
import random
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from copy import deepcopy

# Suppress library noise
logging.disable(logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta
import numpy as np

from app.core.config import get_settings
from app.execution.backtester import Backtester, BacktestTrade, BacktestResult
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
DAYS = 200  # 200 days of M5 data


# =========================================================
# Parameter Sets to Grid Search
# =========================================================
PARAM_GRID = [
    # --- Baseline (current production config) ---
    {
        "label": "BASELINE",
        "SL_ATR_MULT": 2.0,
        "TP_ATR_MULT": 3.0,
        "MIN_SL_DISTANCE": 3.0,
        "SUPERTREND_LEN": 10,
        "SUPERTREND_MUL": 3.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 2.0,
        "PROFIT_LOCK_1": 1.0,
        "PROFIT_LOCK_2": 2.0,
    },
    # --- Tighter SL, Tighter TP (Quick Scalp) ---
    {
        "label": "QUICK_SCALP",
        "SL_ATR_MULT": 1.5,
        "TP_ATR_MULT": 2.0,
        "MIN_SL_DISTANCE": 2.0,
        "SUPERTREND_LEN": 10,
        "SUPERTREND_MUL": 3.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.5,
        "PROFIT_LOCK_1": 0.8,
        "PROFIT_LOCK_2": 1.5,
    },
    # --- Cash Cow Focus (Small wins, high WR) ---
    {
        "label": "CASH_COW",
        "SL_ATR_MULT": 1.5,
        "TP_ATR_MULT": 1.5,
        "MIN_SL_DISTANCE": 2.0,
        "SUPERTREND_LEN": 10,
        "SUPERTREND_MUL": 3.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.5,
        "PROFIT_LOCK_1": 0.6,
        "PROFIT_LOCK_2": 1.2,
    },
    # --- SuperTrend Tight (faster trend detection) ---
    {
        "label": "ST_TIGHT",
        "SL_ATR_MULT": 1.8,
        "TP_ATR_MULT": 2.5,
        "MIN_SL_DISTANCE": 2.5,
        "SUPERTREND_LEN": 7,
        "SUPERTREND_MUL": 2.0,
        "RSI_PERIOD": 10,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.8,
        "PROFIT_LOCK_1": 0.8,
        "PROFIT_LOCK_2": 1.5,
    },
    # --- Conservative Trend (Wide SL, Big TP) ---
    {
        "label": "TREND_RIDE",
        "SL_ATR_MULT": 2.5,
        "TP_ATR_MULT": 5.0,
        "MIN_SL_DISTANCE": 4.0,
        "SUPERTREND_LEN": 14,
        "SUPERTREND_MUL": 3.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 2.5,
        "PROFIT_LOCK_1": 1.5,
        "PROFIT_LOCK_2": 3.0,
    },
    # --- Balanced Pro (Sweet spot target) ---
    {
        "label": "BALANCED_PRO",
        "SL_ATR_MULT": 1.8,
        "TP_ATR_MULT": 2.2,
        "MIN_SL_DISTANCE": 2.5,
        "SUPERTREND_LEN": 10,
        "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 12,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.8,
        "PROFIT_LOCK_1": 0.7,
        "PROFIT_LOCK_2": 1.4,
    },
    # --- Sniper Extreme (Stricter entry, moderate SL/TP) ---
    {
        "label": "SNIPER_EXTREME",
        "SL_ATR_MULT": 1.5,
        "TP_ATR_MULT": 2.0,
        "MIN_SL_DISTANCE": 2.5,
        "SUPERTREND_LEN": 10,
        "SUPERTREND_MUL": 3.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 20,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.5,
        "PROFIT_LOCK_1": 0.5,
        "PROFIT_LOCK_2": 1.0,
    },
    # --- Momentum Scalp (RSI tight, fast in/out) ---
    {
        "label": "MOMENTUM_SCALP",
        "SL_ATR_MULT": 1.2,
        "TP_ATR_MULT": 1.5,
        "MIN_SL_DISTANCE": 2.0,
        "SUPERTREND_LEN": 7,
        "SUPERTREND_MUL": 2.0,
        "RSI_PERIOD": 7,
        "ADX_PERIOD": 10,
        "ATR_PERIOD": 10,
        "CHANDELIER_MULT": 1.2,
        "PROFIT_LOCK_1": 0.5,
        "PROFIT_LOCK_2": 1.0,
    },
]


def apply_params(strategy, params: dict):
    """Apply parameter set to strategy instance."""
    for key, val in params.items():
        if key == "label":
            continue
        if hasattr(strategy, key):
            setattr(strategy, key, val)


def run_single_backtest(candles: pd.DataFrame, params: dict, initial_equity: float = 10000.0) -> Tuple[dict, BacktestResult]:
    """Run a single Gold backtest with specified parameters."""
    # Fix random seed for reproducibility (anti_hunt_sl uses random)
    random.seed(42)
    np.random.seed(42)
    
    from app.strategy.templates.gold_scalp_pro import GoldScalpProStrategy
    strategy = GoldScalpProStrategy()
    apply_params(strategy, params)
    
    bt = FullFeatureBacktester(strategy, initial_equity=initial_equity)
    result = bt.run(candles, SYMBOL, contract_size=CONTRACT_SIZE, point=POINT, digits=DIGITS)
    return params, result


def print_result_row(label: str, result: BacktestResult, highlight: bool = False):
    """Print a single result row."""
    pnl = result.total_profit_usd
    wr = result.win_rate
    pf = result.profit_factor
    dd = result.max_drawdown_pct
    trades = result.total_trades
    
    marker = ""
    if wr >= 60 and pnl > 0:
        marker = " 🏆"
    elif wr >= 55 and pnl > 0:
        marker = " ⭐"
    elif pnl > 0:
        marker = " ✅"
    else:
        marker = " ❌"
    
    prefix = ">>> " if highlight else "    "
    print(f"{prefix}{label:<20s} | Trades: {trades:>4} | WR: {wr:>5.1f}% | PF: {pf:>5.2f} | P&L: ${pnl:>+10.2f} | DD: {dd:>5.1f}%{marker}")


def main():
    print("=" * 80)
    print("🥇 GOLD STRATEGY TUNING BACKTEST")
    print(f"   Symbol: {SYMBOL} | Days: {DAYS} | Timeframe: M5")
    print(f"   Parameter Sets: {len(PARAM_GRID)}")
    print("=" * 80)
    
    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return
    
    # ── Load Data ──
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, utc_from, utc_to)
    
    if rates is None or len(rates) == 0:
        print(f"❌ No data for {SYMBOL}")
        mt5.shutdown()
        return
    
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"✅ Loaded {len(df)} bars ({df['time'].iloc[0]} → {df['time'].iloc[-1]})")
    print()
    
    # ── Run Grid Search ──
    all_results: List[Tuple[dict, BacktestResult]] = []
    
    for idx, params in enumerate(PARAM_GRID, 1):
        label = params.get("label", f"SET_{idx}")
        print(f"[{idx}/{len(PARAM_GRID)}] Running: {label}...", end=" ", flush=True)
        
        start_t = time.monotonic()
        p, result = run_single_backtest(df, params)
        elapsed = time.monotonic() - start_t
        
        all_results.append((p, result))
        print(f"Done ({elapsed:.1f}s)")
    
    # ── Results Summary ──
    print()
    print("=" * 100)
    print("📋 GOLD TUNING RESULTS — RANKED BY WIN RATE + PROFITABILITY")
    print("=" * 100)
    print(f"    {'Config':<20s} | {'Trades':>7} | {'WR%':>7} | {'PF':>6} | {'P&L':>12} | {'DD%':>6} |")
    print("-" * 100)
    
    # Sort by: (1) WR >= 60 first, (2) then by P&L descending
    def sort_key(item):
        _, r = item
        wr_pass = 1 if r.win_rate >= 60 else 0
        return (wr_pass, r.total_profit_usd)
    
    sorted_results = sorted(all_results, key=sort_key, reverse=True)
    
    best_params = None
    best_result = None
    
    for i, (params, result) in enumerate(sorted_results):
        label = params.get("label", "UNKNOWN")
        is_best = (i == 0)
        print_result_row(label, result, highlight=is_best)
        if is_best:
            best_params = params
            best_result = result
    
    print("=" * 100)
    
    # ── Best Config Detail ──
    if best_result:
        print()
        print("🏆 BEST CONFIGURATION:")
        print(f"   Label: {best_params.get('label', '?')}")
        print(f"   Win Rate: {best_result.win_rate}%")
        print(f"   Profit Factor: {best_result.profit_factor}")
        print(f"   Total P&L: ${best_result.total_profit_usd:.2f}")
        print(f"   Max DD: {best_result.max_drawdown_pct}%")
        print(f"   Total Trades: {best_result.total_trades}")
        print()
        print("   Parameters:")
        for k, v in best_params.items():
            if k == "label": continue
            print(f"     {k}: {v}")
        
        # Per-Regime Breakdown
        if best_result.per_regime:
            print()
            print("   Per-Regime Breakdown:")
            for regime, stats in best_result.per_regime.items():
                total_r = stats.get('wins', 0) + stats.get('losses', 0)
                wr_r = round(stats['wins'] / total_r * 100, 1) if total_r > 0 else 0
                print(f"     {regime:<20s}: WR={wr_r:>5.1f}% | Trades={total_r:>3} | P&L=${stats.get('pnl', 0):>+.2f}")
    
    # ── Check if any config meets target ──
    print()
    winners = [(p, r) for p, r in all_results if r.win_rate >= 60 and r.total_profit_usd > 0]
    if winners:
        print(f"✅ {len(winners)} configuration(s) meet the target (WR≥60%, Profitable)!")
        print()
        print("   Recommended Production Config (copy to config_service or .env):")
        best_winner = max(winners, key=lambda x: x[1].total_profit_usd)
        bp = best_winner[0]
        print(f"   [STRATEGY_GOLD_SCALP_PRO]")
        for k, v in bp.items():
            if k == "label": continue
            print(f"   {k} = {v}")
    else:
        print("⚠️  No configuration meets WR≥60% + Profitable. Further tuning needed.")
        # Show closest
        closest = max(all_results, key=lambda x: x[1].win_rate)
        print(f"   Closest: {closest[0].get('label', '?')} — WR={closest[1].win_rate}%, P&L=${closest[1].total_profit_usd:.2f}")
    
    # ── Trade log for best config (last 20 trades) ──
    if best_result and best_result.trades:
        print()
        print("📝 Last 20 Trades (Best Config):")
        trades = best_result.trades[-20:]
        for t in trades:
            if isinstance(t, dict):
                action = t.get('action', '?')
                entry = t.get('entry_price', 0)
                exit_p = t.get('exit_price', 0)
                pnl = t.get('profit_usd', 0)
                reason = t.get('exit_reason', '?')
            else:
                action = t.action
                entry = t.entry_price
                exit_p = t.exit_price
                pnl = t.profit_usd
                reason = t.exit_reason
            icon = "🟢" if pnl >= 0 else "🔴"
            print(f"   {icon} {action:<4s} | Entry: {entry:>9.2f} | Exit: {exit_p:>9.2f} | P&L: ${pnl:>+8.2f} | {reason}")
    
    mt5.shutdown()
    print()
    print("✅ Gold Tuning Complete!")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
