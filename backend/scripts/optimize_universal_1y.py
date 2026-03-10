"""
Ultimate 1-Year Optimization (Universal Sniper All-Weather)
===========================================================
Systematically grid search the Universal Sniper V3 strategy over 365 days.
Finds the absolute most profitable configuration that ensures maximum profit
and a high win rate across all market conditions.

Saves the Top 3 configurations to `brain.db` under the 'ALL' regime tag,
making the bot natively use them as its core intelligence.

Usage:
    python backend/scripts/optimize_universal_1y.py
    python backend/scripts/optimize_universal_1y.py --quick
"""

import sys
import os
import time
import json
import logging
import random
import argparse
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from itertools import product
from typing import List, Dict, Any

import pandas as pd
import numpy as np

# Adjust path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Initialize MT5
import MetaTrader5 as mt5

# Project imports
from app.core.logging import get_logger
from app.execution.backtester import Backtester, BacktestResult
from app.brain.memory_store import MemoryStore
from app.strategy.templates.ADD_18022026.universal_sniper import UniversalSniperStrategy
from scripts.backtest_full import FullFeatureBacktester

# ─── Logging ───
logging.getLogger().handlers = []
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("Optimize1Y")

for log_name in ["app.core.logging", "app.risk", "app.risk.sizing", "app.brain.regime", "FullBacktest"]:
    logging.getLogger(log_name).setLevel(logging.ERROR)

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════
SYMBOL = "XAUUSDc"
TIMEFRAME = mt5.TIMEFRAME_M5
CONTRACT_SIZE = 100.0
POINT_VALUE = 0.01
DIGITS = 2

# ═══════════════════════════════════════════════════════════════════
# PARAMETER GRID GENERATOR
# ═══════════════════════════════════════════════════════════════════

def generate_universal_grid(quick: bool = False) -> List[Dict[str, Any]]:
    """Generate UniversalSniper parameter grid for 1-year maximization."""
    if quick:
        adx_periods = [14]
        rsi_periods = [14]
        bb_stds = [2.0]
        atr_mults = [1.5, 2.0]
        rr_ratios = [1.5, 2.0]
    else:
        # Full grid searching the boundaries of the All-Weather features
        adx_periods = [10, 14]
        rsi_periods = [10, 14]
        bb_stds = [2.0, 2.5]
        atr_mults = [1.2, 1.5, 2.0, 2.5] 
        rr_ratios = [1.2, 1.5, 2.0, 2.5, 3.0]

    combos = []
    for adx, rsi, bb, atr, rr in product(adx_periods, rsi_periods, bb_stds, atr_mults, rr_ratios):
        # Filter nonsensical combos
        if rr < 1.0 and atr > 2.0: continue
        
        label = f"UNI_1Y_ADX{adx}_RSI{rsi}_BB{bb}_SL{atr}_RR{rr}"
        combos.append({
            "strategy_class": "UniversalSniper",
            "label": label,
            "params": {
                "adx_period": adx,
                "rsi_period": rsi,
                "bb_std": bb,
                "atr_multiplier": atr,
                "rr_ratio": rr,
                "bb_period": 20,
            }
        })
    return combos

# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def create_strategy(class_name: str):
    if class_name == "UniversalSniper":
        return UniversalSniperStrategy()
    raise ValueError(f"Unknown: {class_name}")

def apply_params(strategy, params: dict):
    for key, value in params.items():
        if hasattr(strategy, key):
            setattr(strategy, key, value)

def fetch_data(symbol: str, days: int):
    """Fetch M5 data from MT5 and return (DataFrame, account_balance)."""
    if not mt5.initialize():
        logger.error("MT5 Initialization failed")
        return None, 0.0

    account_info = mt5.account_info()
    balance = account_info.balance if account_info else 10000.0

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)

    logger.info(f"Fetching {days} days of M5 data for {symbol}...")
    rates = mt5.copy_rates_range(symbol, TIMEFRAME, start_date, end_date)

    if rates is None or len(rates) == 0:
        logger.error(f"No data fetched for {symbol}")
        mt5.shutdown()
        return None, 0.0

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    mt5.shutdown()
    return df, balance

def compute_score(result: BacktestResult) -> float:
    """
    Composite score — maximize profit while maintaining high quality.
    - Profit Factor:  x30 (Must be > 1.25)
    - Win Rate:       x2.0 (Must be decent)
    - Total Profit:   Scaled bonus (Important for 1Y net positive)
    - Max DD:         x(-3.0) Heavy penalty on crashes
    """
    if result.total_profit_usd < 0:
        return -100.0
    if result.total_trades < 20: # Need robust trade count over 1 year (approx 1-2 per month minimum)
        return -50.0

    score = 0.0
    score += min(result.profit_factor, 5.0) * 30     
    score += result.win_rate * 2.0                     
    score -= result.max_drawdown_pct * 3.0             
    score += min(result.total_profit_usd / 100, 50)    
    return round(score, 2)

def save_to_brain(label: str, strategy_class: str, params: dict,
                  result: BacktestResult, score: float, rank: int):
    """Save configuration to brain.db (evolved_params — top strategies only)."""
    try:
        store = MemoryStore()
        store.connect()

        strategy_name = "universal_sniper"

        save_params = {k: v for k, v in params.items()}
        save_params["_label"] = label
        save_params["_rank"] = rank
        save_params["_strategy_class"] = strategy_class
        save_params["_win_rate"] = round(result.win_rate, 2)
        save_params["_profit_factor"] = round(result.profit_factor, 2)
        save_params["_total_profit"] = round(result.total_profit_usd, 2)
        save_params["_max_dd"] = round(result.max_drawdown_pct, 2)
        save_params["_total_trades"] = result.total_trades
        save_params["_trained_at"] = datetime.now(timezone.utc).isoformat()

        # Save to the ALL regime so the recommender easily pulls this as default
        store.save_evolved_params(
            strategy_name=strategy_name,
            symbol=SYMBOL,
            regime="ALL",
            params=save_params,
            score=score
        )
        return True
    except Exception as e:
        logger.error(f"Failed to save: {e}")
        return False

def format_time(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"

# ═══════════════════════════════════════════════════════════════════
# MAIN TOURNAMENT
# ═══════════════════════════════════════════════════════════════════

def run_tournament(days: int = 365, initial_equity: float = 0.0, quick: bool = False):
    """Run systematic 1-Year Optimization."""

    df, mt5_balance = fetch_data(SYMBOL, days)
    if df is None:
        print("❌ Failed to fetch data.")
        return

    if initial_equity <= 0:
        initial_equity = mt5_balance
    print(f"\n💰 Using equity: {initial_equity:,.2f} (from MT5 account)")

    # Generate grid
    all_contestants = generate_universal_grid(quick)
    total = len(all_contestants)

    print(f"\n{'='*100}")
    print(f"🏆 ALL-WEATHER UNIVERSAL OPTIMIZATION — 1 YEAR ({days} Days)")
    print(f"   Data: {len(df):,} M5 candles | Total Combos: {total}")
    print(f"{'='*100}\n")

    header = f"{'#':>4} {'Label':<35} {'WR%':>6} {'PF':>6} {'PnL':>10} {'DD%':>6} {'Trds':>5} {'Score':>7} {'ETA':>6}"
    print(header)
    print("-" * len(header))

    results = []
    times = []

    for i, contestant in enumerate(all_contestants):
        label = contestant["label"]
        cls = contestant["strategy_class"]
        params = contestant["params"]
        run_start = time.monotonic()

        try:
            strategy = create_strategy(cls)
            apply_params(strategy, params)

            random.seed(42)
            np.random.seed(42)

            bt = FullFeatureBacktester(strategy, initial_equity=initial_equity)
            result = bt.run(df, SYMBOL, contract_size=CONTRACT_SIZE, point=POINT_VALUE, digits=DIGITS)

            score = compute_score(result)

            results.append({
                "label": label,
                "strategy_class": cls,
                "params": params,
                "result": result,
                "score": score,
            })

            elapsed = time.monotonic() - run_start
            times.append(elapsed)
            avg_time = sum(times) / len(times)
            remaining = (total - i - 1) * avg_time
            eta_str = format_time(remaining)

            pnl_str = f"${result.total_profit_usd:,.0f}"
            print(f"{i+1:>4} {label:<35} {result.win_rate:>5.1f}% {result.profit_factor:>5.2f} {pnl_str:>10} {result.max_drawdown_pct:>5.1f}% {result.total_trades:>5} {score:>7.1f} ~{eta_str}")

        except Exception as e:
            elapsed = time.monotonic() - run_start
            times.append(elapsed)
            print(f"{i+1:>4} {label:<35} ❌ ERROR: {e}")

    if not results:
        print("\n❌ No results.")
        return

    # Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)

    # ─── CHAMPION ───
    best = results[0]
    best_res = best["result"]

    print(f"\n{'='*100}")
    print(f"🏆 1-YEAR CHAMPION: {best['label']} ({best['strategy_class']})")
    print(f"{'='*100}")
    print(f"  Win Rate       : {best_res.win_rate:.2f}%")
    print(f"  Profit Factor  : {best_res.profit_factor:.2f}")
    print(f"  Total Profit   : ${best_res.total_profit_usd:,.2f}")
    print(f"  Max Drawdown   : {best_res.max_drawdown_pct:.2f}%")
    print(f"  Total Trades   : {best_res.total_trades}")
    print(f"  Score          : {best['score']:.2f}")
    print(f"\n  Parameters:")
    for k, v in best["params"].items():
        print(f"    {k:20s} = {v}")

    # ─── SAVE TOP 3 TO BRAIN (evolved_params) ───
    print(f"\n💾 Saving top 3 to brain.db (for robust ALL-regime intelligence)...")
    for rank, r in enumerate(results[:3], 1):
        ok = save_to_brain(r["label"], r["strategy_class"], r["params"],
                           r["result"], r["score"], rank)
        emoji = ["🥇", "🥈", "🥉"][rank-1]
        status = "✅" if ok else "❌"
        print(f"  {emoji} {status} {r['label']} (Score: {r['score']:.1f})")

    total_time = sum(times)
    print(f"\n{'='*100}")
    print(f"✅ 1-Year Optimization Complete! ({len(results)} combos tested in {format_time(total_time)})")
    print(f"{'='*100}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="1-Year Full Feature Strategy Optimization")
    parser.add_argument("--days", type=int, default=365, help="Days of historical data")
    parser.add_argument("--equity", type=float, default=0.0, help="Initial equity")
    parser.add_argument("--quick", action="store_true", help="Quick mode (smaller grid)")
    args = parser.parse_args()

    try:
        run_tournament(days=args.days, initial_equity=args.equity, quick=args.quick)
    except KeyboardInterrupt:
        print("\n⚠️ Optimization interrupted by user.")
    except Exception:
        import traceback
        traceback.print_exc()
