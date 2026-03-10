"""
Ultimate MTF BTC Optimizer (360 Days)
=====================================
Optimizes BtcEliteStrategy across M3, M5, M6, M12, M30, H1.
Objective: >52% Winrate & Profitable Every Day (High Win Days %).

Uses multiprocessing for fast parameter search on `FullFeatureBacktester`.

Usage:
    python backend/scripts/optimize_btc_mtf_360d.py
"""

import sys
import time
import random
import multiprocessing
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import MetaTrader5 as mt5

# Initialize MT5 at top level to ensure types are available
from typing import List, Dict, Any

# Adjust path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging import get_logger
import logging

# Disable ALL logging globally during backtest to prevent console freeze
logging.disable(logging.CRITICAL)

SYMBOL = "BTCUSDm"
CONTRACT_SIZE = 1.0  # Or whatever contract size BTC has on Exness (assume 1 for score tracking)
POINT_VALUE = 0.01

def fetch_data(symbol: str, days: int, timeframe_enum: int) -> pd.DataFrame:
    if not mt5.initialize():
        print("MT5 Initialization failed")
        return None

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    
    rates = mt5.copy_rates_range(symbol, timeframe_enum, start_date, end_date)
    mt5.shutdown()
    
    if rates is None or len(rates) == 0:
        return None
        
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df

def generate_params(n_iter: int) -> List[Dict[str, Any]]:
    params = []
    
    # Grid choices
    ema_fast_opts = [7, 9, 10, 12]
    ema_mid_opts = [20, 21, 26, 30]
    ema_slow_opts = [50, 60]
    adx_opts = [10, 14, 20]
    adx_min_opts = [15, 18, 20, 25]
    rsi_opts = [10, 14, 21]
    sl_mult_opts = [1.5, 2.0, 2.5, 3.0]
    rr_std_opts = [1.0, 1.2, 1.5, 2.0]
    rr_elite_opts = [2.0, 2.5, 3.0]
    min_score_opts = [50, 60, 65, 70]

    for i in range(n_iter):
        rr_std = random.choice(rr_std_opts)
        rr_eli = random.choice(rr_elite_opts)
        
        # ensure rr_elite >= rr_std
        if rr_eli < rr_std:
            rr_eli = rr_std + 0.5
            
        p = {
            "ema_fast": random.choice(ema_fast_opts),
            "ema_mid": random.choice(ema_mid_opts),
            "ema_slow": random.choice(ema_slow_opts),
            "adx_period": random.choice(adx_opts),
            "adx_min": random.choice(adx_min_opts),
            "rsi_period": random.choice(rsi_opts),
            "sl_atr_mult": random.choice(sl_mult_opts),
            "sl_atr_mult_mr": random.choice(sl_mult_opts) - 0.5, # MR is usually tighter
            "rr_standard": rr_std,
            "rr_strong": rr_std + 0.5,
            "rr_elite": rr_eli,
            "min_score_trade": random.choice(min_score_opts)
        }
        params.append(p)
    return params

def worker_task(job: tuple):
    symbol, df, tf_name, params = job
    
    from app.strategy.templates.btc_elite import BtcEliteStrategy
    from app.execution.backtester import Backtester
    
    strategy = BtcEliteStrategy()
    strategy.timeframe = tf_name
    
    # Override defaults
    for k, v in params.items():
        strategy.p[k] = v
        
    # Run backtester
    bt = Backtester(strategy, initial_equity=1000.0)
    result = bt.run(df, symbol=symbol, contract_size=CONTRACT_SIZE, point=POINT_VALUE)
    
    # Compute daily profitability percentage
    pnl_by_date = {}
    for t in result.trades:
        # Assuming entry_time is formatted as YYYY-MM-DD HH:MM:SS
        date_str = str(t['entry_time']).split(' ')[0]
        pnl_by_date[date_str] = pnl_by_date.get(date_str, 0.0) + t['pnl']
        
    profit_days = sum(1 for v in pnl_by_date.values() if v > 0)
    total_days_traded = len(pnl_by_date)
    win_days_pct = (profit_days / total_days_traded * 100) if total_days_traded > 0 else 0
    
    return {
        "params": params,
        "winrate": result.win_rate,
        "total_profit": result.total_profit_usd,
        "win_days_pct": win_days_pct,
        "total_days_traded": total_days_traded,
        "total_trades": result.total_trades,
        "max_dd_pct": result.max_drawdown_pct,
        "pf": result.profit_factor
    }

def main():
    N_ITERATIONS = 20
    DAYS = 360
    
    # Desired Timeframes
    # MT5 constants
    timeframes = [
        ("M3", mt5.TIMEFRAME_M3),
        ("M5", mt5.TIMEFRAME_M5),
        ("M6", mt5.TIMEFRAME_M6),
        ("M12", mt5.TIMEFRAME_M12),
        ("M30", mt5.TIMEFRAME_M30),
        ("H1", mt5.TIMEFRAME_H1)
    ]
    
    # Keep track of winner per timeframe
    best_per_tf = {}

    print(f"================================================================================")
    print(f">>> BTC MTF 360D OPTIMIZATION (Target: WinRate > 52%, Max WinDays)")
    print(f"================================================================================\n")
    
    for tf_name, tf_enum in timeframes:
        print(f"--- Downloading Data for {tf_name} ---")
        df = fetch_data(SYMBOL, DAYS, tf_enum)
        if df is None or len(df) == 0:
            print(f"[X] Failed to fetch data for {tf_name}. Skipping.")
            continue
            
        print(f"[OK] Fetched {len(df)} candles for {tf_name}. Generating {N_ITERATIONS} random configs...")
        
        param_grid = generate_params(N_ITERATIONS)
        jobs = [(SYMBOL, df, tf_name, p) for p in param_grid]
        
        results = []
        start_t = time.monotonic()
        
        # Map using multiprocessing
        with multiprocessing.Pool(processes=multiprocessing.cpu_count()) as pool:
            for i, res in enumerate(pool.imap_unordered(worker_task, jobs)):
                if res['total_trades'] > 20:  # Need minimum statistical significance
                    results.append(res)
                # Print progress
                if (i+1) % 10 == 0:
                    print(f"   Progress: {i+1}/{N_ITERATIONS}...")
                    
        elapsed = time.monotonic() - start_t
        print(f"[TIME] Finished {tf_name} in {elapsed:.1f} seconds. Valid configs found: {len(results)}\n")
        
        if not results:
            print(f"[!WARN] No configs with >20 trades found for {tf_name}.\n")
            continue
            
        # Filter for WinRate > 52% and Profitable
        filtered = [r for r in results if r['winrate'] > 52.0 and r['total_profit'] > 0]
        
        if not filtered:
            print(f"[!WARN] No configs met WinRate > 52% criteria for {tf_name}. Showing top Profit instead:")
            filtered = [r for r in results if r['total_profit'] > 0]
            
        if not filtered:
            print(f"[!WARN] STILL NO PROFITABLE CONFIGS for {tf_name}.")
            continue
            
        # We value: 1. Win Days % (High consistency) 2. Total Profit 3. WinRate
        sorted_res = sorted(filtered, key=lambda x: (x['win_days_pct'], x['total_profit'], x['winrate']), reverse=True)
        best = sorted_res[0]
        
        best_per_tf[tf_name] = best
        
        print(f"[BEST] BEST ON {tf_name}:")
        print(f"   Win Days : {best['win_days_pct']:.1f}% ({best['total_days_traded']} traded days)")
        print(f"   Win Rate : {best['winrate']:.1f}%")
        print(f"   Total PnL: ${best['total_profit']:,.2f}")
        print(f"   Trades   : {best['total_trades']}")
        print(f"   Max DD   : {best['max_dd_pct']:.2f}%")
        print(f"   Profit F : {best['pf']:.2f}")
        print(f"   Params   : {best['params']}")
        print(f"{'-'*80}")
        
    
    print(f"\n\n================================================================================")
    print(f"*** FINAL SUMMARY OF BEST TIMEFRAMES FOR {SYMBOL} ***")
    print(f"================================================================================")
    
    if not best_per_tf:
        print("No optimal setups found across any timeframe.")
        return
        
    overall = sorted(best_per_tf.items(), key=lambda x: x[1]['win_days_pct'], reverse=True)
    
    for rank, (tf_name, res) in enumerate(overall, 1):
        print(f"\n#{rank} Timeframe: {tf_name}")
        print(f"   Win Days : {res['win_days_pct']:.1f}% (Traded {res['total_days_traded']} days)")
        print(f"   Win Rate : {res['winrate']:.1f}%")
        print(f"   Total PnL: ${res['total_profit']:,.2f}")
        print(f"   Max DD   : {res['max_dd_pct']:.2f}%")
        print(f"   Params To Use:")
        
        for k, v in res['params'].items():
            print(f"      '{k}': {v},")


if __name__ == '__main__':
    # Fix for multiprocessing on Windows
    multiprocessing.freeze_support()
    main()
