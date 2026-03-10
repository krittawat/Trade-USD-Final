"""
Advanced Regime-Targeted AI Training Script
===========================================
Focus: LIQUIDITY_SWEEP & RANGING (Sideways)

This script isolates trades that occur specifically when the market is either 
ranging in a tight box or performing sharp stop-loss liquidity sweeps.
It trains the UniversalSniperStrategy (V3 All-Weather) exclusively for these
two difficult market conditions and saves the optimal parameters to the brain.db
under their respective regime tags.
"""

import sys
import time
import logging
import random
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from itertools import product
from typing import List, Dict, Any

import pandas as pd
import numpy as np
import MetaTrader5 as mt5

# Adjust path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
logger = logging.getLogger("TrainRegimesAI")

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

TARGET_REGIMES = ["RANGING", "LIQUIDITY_SWEEP"]

# ═══════════════════════════════════════════════════════════════════
# PARAMETER GRID GENERATOR
# ═══════════════════════════════════════════════════════════════════

def generate_universal_grid(quick: bool = False) -> List[Dict[str, Any]]:
    """Generate UniversalSniper parameter grid focused on Reversals and Sweeps."""
    if quick:
        rsi_periods = [14]
        bb_stds = [2.0]
        atr_mults = [1.5, 2.0]
        rr_ratios = [1.5, 2.0]
    else:
        rsi_periods = [10, 14]
        bb_stds = [2.0, 2.5]       # 2.5 is deeper -> good for capturing sweeps
        atr_mults = [1.5, 2.0, 2.5]  # Sweep requires wider SL buffer to not get stopped out
        rr_ratios = [1.5, 2.0, 2.5]  # Range is usually smaller, Sweeps yield bigger RR

    combos = []
    for rsi, bb, atr, rr in product(rsi_periods, bb_stds, atr_mults, rr_ratios):
        label = f"UNI_Target_RSI{rsi}_BB{bb}_SL{atr}_RR{rr}"
        combos.append({
            "strategy_class": "UniversalSniper",
            "label": label,
            "params": {
                "rsi_period": rsi,
                "bb_std": bb,
                "atr_multiplier": atr,
                "rr_ratio": rr,
                "adx_period": 14,
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
    else:
        raise ValueError(f"Unknown strategy: {class_name}")

def apply_params(strategy, params: dict):
    for key, value in params.items():
        if hasattr(strategy, key):
            setattr(strategy, key, value)

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

def compute_targeted_stats(result: BacktestResult, initial_equity: float, target_regime: str) -> dict:
    """Isolate trades that occurred exclusively in the specific regimes."""
    if not result.trades:
        return {"win_rate": 0, "pf": 0, "pnl": 0, "trades": 0, "max_dd": 0}

    # Filter strictly for target regime
    targeted_trades = [t for t in result.trades if t.get("regime") == target_regime]
    
    if not targeted_trades:
        return {"win_rate": 0, "pf": 0, "pnl": 0, "trades": 0, "max_dd": 0}

    wins = [t for t in targeted_trades if t.get("pnl", 0) > 0]
    losses = [t for t in targeted_trades if t.get("pnl", 0) <= 0]
    
    total = len(targeted_trades)
    win_rate = (len(wins) / total * 100) if total > 0 else 0
    
    gross_profit = sum(t.get("pnl", 0) for t in wins)
    gross_loss = abs(sum(t.get("pnl", 0) for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
    
    total_pnl = sum(t.get("pnl", 0) for t in targeted_trades)
    
    peak = 0
    current = 0
    max_dd_usd = 0
    for t in targeted_trades:
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
    """Composite score prioritizing High Win Rate and Risk/Reward."""
    if stats["pnl"] < 0 or stats["trades"] < 3:
        return -100.0

    score = 0.0
    score += min(stats["pf"], 5.0) * 20     
    score += stats["win_rate"] * 1.5          
    score -= stats["max_dd"] * 5.0            
    score += min(stats["pnl"] / 50, 40)      
    return round(score, 2)

def save_to_brain(label: str, strategy_class: str, params: dict,
                  stats: dict, score: float, rank: int, regime: str):
    """Save configuration to brain.db specifically for the requested regime."""
    try:
        store = MemoryStore()
        store.connect()

        strategy_name = "universal_sniper"

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
        save_params["_target_regime"] = regime

        store.save_evolved_params(
            strategy_name=strategy_name,
            symbol=SYMBOL,
            regime=regime,
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

def run_tournament(days: int = 180, initial_equity: float = 0.0, quick: bool = False):
    df, mt5_balance = fetch_data(SYMBOL, days)
    if df is None:
        print("❌ Failed to fetch data.")
        return

    if initial_equity <= 0:
        initial_equity = mt5_balance
    print(f"\n💰 Using equity: {initial_equity:,.2f} (from MT5 account)")

    all_contestants = generate_universal_grid(quick)
    total = len(all_contestants)

    print(f"\n{'='*95}")
    print(f"🎯 AI TARGET TRAINING: LIQUIDITY SWEEP & RANGING — {SYMBOL} | {days} Days")
    print(f"   Data: {len(df):,} M5 candles | Total Combos: {total}")
    print(f"{'='*95}\n")

    regime_results = {regime: [] for regime in TARGET_REGIMES}
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

            # Evaluate independently for EACH target regime
            for regime in TARGET_REGIMES:
                stats = compute_targeted_stats(result, initial_equity, regime)
                score = compute_score(stats)
                
                # Only keep combos that actually generated trades in this regime
                if stats["trades"] > 0:
                    regime_results[regime].append({
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
            print(f"[{i+1}/{total}] Processed {label} in {elapsed:.1f}s. ETA: {format_time(remaining)}")

        except Exception as e:
            times.append(0.1)
            print(f"[{i+1}/{total}] {label:<35} ❌ ERROR: {e}")

    # Process Winners by Regime
    print(f"\n\n{'='*95}")
    print(f"🏅 TOURNAMENT RESULTS BY REGIME")
    print(f"{'='*95}")
    
    for regime in TARGET_REGIMES:
        results = regime_results[regime]
        if not results:
            print(f"\n❌ [{regime}] No profitable configurations found.")
            continue
            
        results.sort(key=lambda x: x["score"], reverse=True)
        best = results[0]
        b_stats = best["stats"]
        
        print(f"\n🏆 RANK 1 — {regime}")
        print(f"  Combo    : {best['label']}")
        print(f"  Win Rate : {b_stats['win_rate']:.2f}%")
        print(f"  PF       : {b_stats['pf']:.2f}")
        print(f"  Profit   : ${b_stats['pnl']:,.2f}")
        print(f"  Max DD   : {b_stats['max_dd']:.2f}%")
        print(f"  Trades   : {b_stats['trades']}")
        print(f"  Score    : {best['score']:.2f}")
        
        print(f"\n💾 Saving Top 3 for {regime}...")
        for rank, r in enumerate(results[:3], 1):
            ok = save_to_brain(r["label"], r["strategy_class"], r["params"],
                              r["stats"], r["score"], rank, regime)
            emoji = ["🥇", "🥈", "🥉"][rank-1]
            status = "✅" if ok else "❌"
            print(f"  {emoji} {status} {r['label']} (Score: {r['score']:.1f})")

    total_time = sum(times)
    print(f"\n{'='*95}")
    print(f"✅ AI Regime-Targeted Training Complete! ({total} combos tested in {format_time(total_time)})")
    print(f"{'='*95}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Regime-Targeted Market Trainer")
    parser.add_argument("--days", type=int, default=180, help="Days of historical data")
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
