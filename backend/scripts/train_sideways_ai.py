"""
Sideways Market Maximum Profit Finder — AI Training Script
==========================================================
Systematic grid search across strategies to find the most profitable
configuration specifically for RANGING and LOW_VOLATILITY regimes.

Auto-generates parameter combinations, runs full backtests, filters 
trades exclusively for Sideways regimes, ranks by composite score, 
and saves top 3 to brain.db (MemoryStore) specifically for the 
'RANGING' regime.

When the live bot detects a 'RANGING' regime, the AI Recommender 
will fetch these highly optimized parameters!

Usage:
    python backend/scripts/train_sideways_ai.py --days 365
    python backend/scripts/train_sideways_ai.py --days 365 --quick 
"""

import sys
import os
import time
import json
import logging
import logging.handlers
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
from app.strategy.templates.gold_scalp_pro import GoldScalpProStrategy
from app.strategy.templates.ranging_sniper import RangingSniperStrategy
from app.strategy.templates.gold_elite import GoldEliteStrategy
from scripts.backtest_full import FullFeatureBacktester

# ─── Logging ───
logging.getLogger().handlers = []
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("TrainSidewaysAI")

def remove_file_handlers():
    for name in logging.root.manager.loggerDict:
        lg = logging.getLogger(name)
        to_remove = [h for h in lg.handlers if isinstance(h, (logging.FileHandler, logging.handlers.RotatingFileHandler))]
        for h in to_remove:
            lg.removeHandler(h)

remove_file_handlers()

import app.core.logging
_orig_get_logger = app.core.logging.get_logger
def _patched_get_logger(name, level="INFO"):
    lg = _orig_get_logger(name, level)
    for h in [h for h in lg.handlers if isinstance(h, (logging.FileHandler, logging.handlers.RotatingFileHandler))]:
        lg.removeHandler(h)
    return lg
app.core.logging.get_logger = _patched_get_logger

for log_name in ["app.core.logging", "app.risk", "app.risk.sizing",
                  "app.risk.trailing", "app.brain.regime", "FullBacktest"]:
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

def generate_gsp_grid(quick: bool = False) -> List[Dict[str, Any]]:
    """Generate GoldScalpPro parameter grid (focused on tighter ranges for sideways)."""
    if quick:
        sl_atrs  = [1.0, 1.5]
        tp_atrs  = [1.5, 2.0]
        st_lens  = [7, 10]
        st_muls  = [2.0]
        chand_mults = [1.5]
    else:
        sl_atrs  = [1.0, 1.2, 1.5, 2.0]
        tp_atrs  = [1.2, 1.5, 2.0, 2.5]
        st_lens  = [7, 10, 14]
        st_muls  = [1.5, 2.0, 2.5]
        chand_mults = [1.5, 2.0]

    combos = []
    for sl, tp, stl, stm, ch in product(sl_atrs, tp_atrs, st_lens, st_muls, chand_mults):
        rr = tp / sl if sl > 0 else 0
        if rr < 0.8: # We want decent RR even in sideways
            continue
        
        label = f"GSP_SW_SL{sl}_TP{tp}_ST{stl}x{stm}_CH{ch}"
        combos.append({
            "strategy_class": "GoldScalpPro",
            "label": label,
            "params": {
                "SL_ATR_MULT": sl, "TP_ATR_MULT": tp, "MIN_SL_DISTANCE": max(1.0, sl * 0.8),
                "SUPERTREND_LEN": stl, "SUPERTREND_MUL": stm,
                "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
                "CHANDELIER_MULT": ch,
                "PROFIT_LOCK_1": round(tp * 0.4, 2), # Lock in early for sideways
                "PROFIT_LOCK_2": round(tp * 0.7, 2),
            }
        })
    return combos

def generate_elite_grid(quick: bool = False) -> List[Dict[str, Any]]:
    """Generate GoldElite parameter grid for sideways optimization."""
    if quick:
        min_scores = [50, 55]
        sl_atrs = [1.5]
        tp_rrs = [1.5, 2.0]
    else:
        min_scores = [45, 50, 55, 60]
        sl_atrs = [1.2, 1.5, 2.0]
        tp_rrs = [1.5, 2.0, 2.5]

    combos = []
    for score, sl, tp in product(min_scores, sl_atrs, tp_rrs):
        label = f"Elite_SW_Score{score}_SL{sl}_TP{tp}"
        combos.append({
            "strategy_class": "GoldElite",
            "label": label,
            "params": {
                "min_score_trade": score,
                "sl_atr_multiplier": sl,
                "tp_rr_multiplier": tp,
            }
        })
    return combos

# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def create_strategy(class_name: str):
    if class_name == "GoldScalpPro":
        return GoldScalpProStrategy()
    elif class_name == "GoldElite":
        return GoldEliteStrategy()
    elif class_name == "RangingSniper":
        return RangingSniperStrategy()
    else:
        raise ValueError(f"Unknown strategy: {class_name}")

def apply_params(strategy, params: dict):
    # Depending on strategy structure, we apply params differently
    for key, value in params.items():
        if hasattr(strategy, "p") and isinstance(strategy.p, dict):
            if key in strategy.p:
                strategy.p[key] = value
            elif key.lower() in strategy.p:
                strategy.p[key.lower()] = value
        
        if hasattr(strategy, key):
            setattr(strategy, key, value)
            
        # specifically for params dict
        if hasattr(strategy, "params") and isinstance(strategy.params, dict):
            strategy.params[key] = value

def fetch_data(symbol: str, days: int):
    """Fetch M5 data from MT5."""
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

def compute_sideways_stats(result: BacktestResult, initial_equity: float) -> dict:
    """Isolate trades that occurred exclusively in sideways regimes."""
    if not result.trades:
        return {"win_rate": 0, "pf": 0, "pnl": 0, "trades": 0, "max_dd": 0}

    # Filter strictly for sideways regimes!
    sideways_trades = [
        t for t in result.trades 
        if t.get("regime") in ("RANGING", "LOW_VOLATILITY")
    ]
    
    if not sideways_trades:
        return {"win_rate": 0, "pf": 0, "pnl": 0, "trades": 0, "max_dd": 0}

    wins = [t for t in sideways_trades if t.get("pnl", 0) > 0]
    losses = [t for t in sideways_trades if t.get("pnl", 0) <= 0]
    
    total = len(sideways_trades)
    win_rate = (len(wins) / total * 100) if total > 0 else 0
    
    gross_profit = sum(t.get("pnl", 0) for t in wins)
    gross_loss = abs(sum(t.get("pnl", 0) for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
    
    total_pnl = sum(t.get("pnl", 0) for t in sideways_trades)
    
    # Calculate approx Max DD for sideways trades only
    peak = 0
    current = 0
    max_dd_usd = 0
    for t in sideways_trades:
        current += t.get("pnl", 0)
        if current > peak:
            peak = current
        dd = peak - current
        if dd > max_dd_usd:
            max_dd_usd = dd

    max_dd_pct = (max_dd_usd / initial_equity * 100) if initial_equity > 0 else 0
    
    return {
        "win_rate": win_rate,
        "pf": pf,
        "pnl": total_pnl,
        "trades": total,
        "max_dd": max_dd_pct
    }

def compute_score(stats: dict) -> float:
    """
    Composite score optimized for Sideways markets.
    Sideways trading needs higher WR and strict DD control.
    """
    if stats["pnl"] < 0 or stats["trades"] < 5:
        return -100.0

    score = 0.0
    score += min(stats["pf"], 5.0) * 30       # PF weight
    score += stats["win_rate"] * 2.0          # High WR is crucial in sideways
    score -= stats["max_dd"] * 5.0            # Strict DD penalty
    score += min(stats["pnl"] / 100, 50)      # Profit bonus
    return round(score, 2)

def save_to_brain(label: str, strategy_class: str, params: dict,
                  stats: dict, score: float, rank: int):
    """Save configuration to brain.db specifically for regime='RANGING'."""
    try:
        store = MemoryStore()
        store.connect()

        # Map to actual strategy internal names
        strategy_name = "gold_scalp_pro"
        if strategy_class == "GoldElite":
            strategy_name = "gold_elite"
        elif strategy_class == "RangingSniper":
            strategy_name = "ranging_sniper"

        save_params = {k: v for k, v in params.items()}
        save_params["_label"] = label
        save_params["_rank"] = rank
        save_params["_strategy_class"] = strategy_class
        save_params["_win_rate"] = round(stats["win_rate"], 2)
        save_params["_profit_factor"] = round(stats["pf"], 2)
        save_params["_total_profit"] = round(stats["pnl"], 2)
        save_params["_max_dd"] = round(stats["max_dd"], 2)
        save_params["_total_trades"] = stats["trades"]
        save_params["_trained_at"] = datetime.now(timezone.utc).isoformat()
        save_params["_target_regime"] = "RANGING"

        # IMPORTANT: Save specifically for RANGING regime
        store.save_evolved_params(
            strategy_name=strategy_name,
            symbol=SYMBOL,
            regime="RANGING",  # <--- THIS IS THE MAGIC
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
    df, mt5_balance = fetch_data(SYMBOL, days)
    if df is None:
        print("❌ Failed to fetch data.")
        return

    if initial_equity <= 0:
        initial_equity = mt5_balance
    print(f"\n💰 Using equity: {initial_equity:,.2f} (from MT5 account)")

    gsp_combos = generate_gsp_grid(quick)
    elite_combos = generate_elite_grid(quick)
    
    all_contestants = gsp_combos + elite_combos
    total = len(all_contestants)

    print(f"\n{'='*95}")
    print(f"🧩 SIDEWAYS AI TRAINING — {SYMBOL} | {days} Days | Equity: {initial_equity:,.0f}")
    print(f"   Target Regimes: RANGING & LOW_VOLATILITY")
    print(f"   Data: {len(df):,} M5 candles | Total Combos: {total}")
    print(f"{'='*95}\n")

    header = f"{'#':>4} {'Label':<35} {'S.WR%':>6} {'S.PF':>6} {'S.PnL':>10} {'S.DD%':>6} {'S.Trds':>6} {'Score':>7} {'ETA':>6}"
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

            # Full backtest over entire period
            bt = FullFeatureBacktester(strategy, initial_equity=initial_equity)
            result = bt.run(df, SYMBOL, contract_size=CONTRACT_SIZE, point=POINT_VALUE, digits=DIGITS)

            # FILTER FOR SIDEWAYS TRADES ONLY
            stats = compute_sideways_stats(result, initial_equity)
            score = compute_score(stats)

            results.append({
                "label": label,
                "strategy_class": cls,
                "params": params,
                "stats": stats,
                "score": score,
            })

            elapsed = time.monotonic() - run_start
            times.append(elapsed)
            avg_time = sum(times) / len(times)
            remaining = (total - i - 1) * avg_time
            eta_str = format_time(remaining)

            pnl_str = f"${stats['pnl']:,.0f}"
            print(f"{i+1:>4} {label:<35} {stats['win_rate']:>5.1f}% {stats['pf']:>5.2f} {pnl_str:>10} {stats['max_dd']:>5.1f}% {stats['trades']:>6} {score:>7.1f} ~{eta_str}")

        except Exception as e:
            elapsed = time.monotonic() - run_start
            times.append(elapsed)
            print(f"{i+1:>4} {label:<35} ❌ ERROR: {e}")

    if not results:
        print("\n❌ No valid sideways trades found in results.")
        return

    # Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)
    for rank, r in enumerate(results, 1):
        r["rank"] = rank

    # ─── CHAMPION ───
    best = results[0]
    b_stats = best["stats"]

    print(f"\n{'='*95}")
    print(f"🏆 SIDEWAYS CHAMPION: {best['label']} ({best['strategy_class']})")
    print(f"{'='*95}")
    print(f"  Sideways Win Rate : {b_stats['win_rate']:.2f}%")
    print(f"  Sideways PF       : {b_stats['pf']:.2f}")
    print(f"  Sideways Profit   : ${b_stats['pnl']:,.2f}")
    print(f"  Sideways Max DD   : {b_stats['max_dd']:.2f}%")
    print(f"  Sideways Trades   : {b_stats['trades']}")
    print(f"  Overall Score     : {best['score']:.2f}")
    print(f"\n  Parameters Ready for RANGING Regime:")
    for k, v in best["params"].items():
        print(f"    {k:20s} = {v}")

    # ─── SAVE TOP 3 TO BRAIN (regime=RANGING) ───
    print(f"\n💾 Saving Top 3 to brain.db strictly for regime='RANGING'...")
    for rank, r in enumerate(results[:3], 1):
        ok = save_to_brain(r["label"], r["strategy_class"], r["params"],
                          r["stats"], r["score"], rank)
        emoji = ["🥇", "🥈", "🥉"][rank-1]
        status = "✅" if ok else "❌"
        print(f"  {emoji} {status} {r['label']} (Score: {r['score']:.1f})")

    total_time = sum(times)
    print(f"\n{'='*95}")
    print(f"✅ AI Sideways Training Complete! ({len(results)} combos tested in {format_time(total_time)})")
    print(f"   The Brain will now natively use these parameters when the market goes RANGING.")
    print(f"{'='*95}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sideways AI Market Trainer")
    parser.add_argument("--days", type=int, default=365, help="Days of historical data")
    parser.add_argument("--equity", type=float, default=0.0, help="Initial equity")
    parser.add_argument("--quick", action="store_true", help="Quick mode")
    args = parser.parse_args()

    try:
        run_tournament(days=args.days, initial_equity=args.equity, quick=args.quick)
    except KeyboardInterrupt:
        print("\n⚠️ Training interrupted by user.")
    except Exception:
        import traceback
        traceback.print_exc()
