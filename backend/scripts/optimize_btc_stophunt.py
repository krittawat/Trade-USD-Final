"""
BTC Stop Hunt (Liquidity Sweep) Optimizer (360 Days)
"""

import sys
import time
import random
import multiprocessing
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import MetaTrader5 as mt5

from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging import get_logger
import logging

logging.disable(logging.CRITICAL)

SYMBOL = "BTCUSDm"
CONTRACT_SIZE = 1.0  
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
    
    swing_lookback_left_opts = [10, 15, 20, 25]
    min_wick_ratio_opts = [0.3, 0.4, 0.5]
    vol_spike_ratio_opts = [1.0, 1.25, 1.5]
    sl_buffer_atr_opts = [0.1, 0.2, 0.5]
    rr_std_opts = [1.2, 1.5, 2.0]
    
    for i in range(n_iter):
        rr_std = random.choice(rr_std_opts)
        
        p = {
            "swing_lookback_left": random.choice(swing_lookback_left_opts),
            "swing_lookback_right": random.choice([2, 3]),
            "min_wick_ratio": random.choice(min_wick_ratio_opts),
            "vol_spike_ratio": random.choice(vol_spike_ratio_opts),
            "sl_buffer_atr": random.choice(sl_buffer_atr_opts),
            "rr_standard": rr_std,
            "rr_strong": rr_std + 0.5,
            "require_ema_alignment": random.choice([True, False])
        }
        params.append(p)
    return params

def worker_task(job: tuple):
    symbol, df, tf_name, params = job
    
    from app.strategy.templates.btc_stop_hunt import BtcStopHuntStrategy
    from app.execution.backtester import Backtester
    
    strategy = BtcStopHuntStrategy()
    strategy.timeframe = tf_name
    
    for k, v in params.items():
        strategy.p[k] = v
        
    bt = Backtester(strategy, initial_equity=1000.0)
    result = bt.run(df, symbol=symbol, contract_size=CONTRACT_SIZE, point=POINT_VALUE)
    
    pnl_by_date = {}
    for t in result.trades:
        date_str = str(t['entry_time']).split(' ')[0]
        pnl_by_date[date_str] = pnl_by_date.get(date_str, 0.0) + t['pnl']
        
    profit_days = sum(1 for v in pnl_by_date.values() if v > 0)
    total_days_traded = len(pnl_by_date)
    win_days_pct = (profit_days / total_days_traded * 100) if total_days_traded > 0 else 0
    avg_daily_profit = (result.total_profit_usd / total_days_traded) if total_days_traded > 0 else 0
    
    return {
        "params": params,
        "winrate": result.win_rate,
        "total_profit": result.total_profit_usd,
        "avg_daily_profit": avg_daily_profit,
        "win_days_pct": win_days_pct,
        "total_days_traded": total_days_traded,
        "total_trades": result.total_trades,
        "max_dd_pct": result.max_drawdown_pct,
        "pf": result.profit_factor
    }

def main():
    N_ITERATIONS = 50
    DAYS = 180  # 6 months for faster iteration first
    
    timeframes = [
        ("M5", mt5.TIMEFRAME_M5),
        ("M15", mt5.TIMEFRAME_M15),
    ]
    
    best_per_tf = {}

    print(f"================================================================================")
    print(f">>> BTC STOP HUNT {DAYS}D OPTIMIZATION (Target: 20-60 USD/Day)")
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
        
        with multiprocessing.Pool(processes=multiprocessing.cpu_count()) as pool:
            for i, res in enumerate(pool.imap_unordered(worker_task, jobs)):
                if res['total_trades'] > 10:  # Minimum statistical significance
                    results.append(res)
                if (i+1) % 10 == 0:
                    print(f"   Progress: {i+1}/{N_ITERATIONS}...")
                    
        elapsed = time.monotonic() - start_t
        print(f"[TIME] Finished {tf_name} in {elapsed:.1f} seconds. Valid configs found: {len(results)}\n")
        
        if not results:
            print(f"[!WARN] No configs with >10 trades found for {tf_name}.\n")
            continue
            
        filtered = [r for r in results if r['pf'] > 1.2 and r['total_profit'] > 0]
        
        if not filtered:
            print(f"[!WARN] No configs met PF > 1.2 criteria for {tf_name}. Showing top Profit instead:")
            filtered = [r for r in results if r['total_profit'] > 0]
            
        if not filtered:
            print(f"[!WARN] STILL NO PROFITABLE CONFIGS for {tf_name}.")
            continue
            
        # Target is High Daily Profit, High WinRate
        sorted_res = sorted(filtered, key=lambda x: (x['avg_daily_profit'], x['pf']), reverse=True)
        best = sorted_res[0]
        
        best_per_tf[tf_name] = best
        
        print(f"[BEST] BEST ON {tf_name}:")
        print(f"   Avg Daily PnL: ${best['avg_daily_profit']:,.2f} USD")
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
        
    overall = sorted(best_per_tf.items(), key=lambda x: x[1]['avg_daily_profit'], reverse=True)
    
    for rank, (tf_name, res) in enumerate(overall, 1):
        print(f"\n#{rank} Timeframe: {tf_name}")
        print(f"   Avg Daily Profit: ${res['avg_daily_profit']:,.2f} USD (~{res['avg_daily_profit']*34:,.0f} THB)")
        print(f"   Win Days : {res['win_days_pct']:.1f}% (Traded {res['total_days_traded']} days)")
        print(f"   Win Rate : {res['winrate']:.1f}%")
        print(f"   Total PnL: ${res['total_profit']:,.2f}")
        print(f"   Max DD   : {res['max_dd_pct']:.2f}%")
        print(f"   Profit F : {res['pf']:.2f}")
        print(f"   Params To Use:")
        
        for k, v in res['params'].items():
            print(f"      '{k}': {v},")


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
