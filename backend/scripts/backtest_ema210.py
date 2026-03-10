"""
Backtest Script for EMA 180 MTF + FVG Strategy
"""
import sys
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.disable(logging.CRITICAL)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import faulthandler
faulthandler.enable()

from app.execution.backtester import Backtester
from app.strategy.templates.ema180_mtf_fvg import Ema180MtfFvgStrategy

# Configuration
SYMBOLS = ["XAUUSDc", "XAGUSDc"]
DAYS = 180

def get_h1_h4_data(symbol, utc_from, utc_to):
    # Fetch higher timeframe data for the MTF logic
    h1_rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, utc_from - timedelta(days=240), utc_to)
    h4_rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H4, utc_from - timedelta(days=240), utc_to)
    
    h1_df = pd.DataFrame(h1_rates) if h1_rates is not None else None
    h4_df = pd.DataFrame(h4_rates) if h4_rates is not None else None
    
    if h1_df is not None:
        h1_df["time"] = pd.to_datetime(h1_df["time"], unit="s")
    if h4_df is not None:
        h4_df["time"] = pd.to_datetime(h4_df["time"], unit="s")
        
    return h1_df, h4_df


def main():
    print("=" * 80)
    print(" EMA 180 MTF + FVG Strategy - 180 Day Backtest")
    print("=" * 80)

    if not mt5.initialize():
        print(" MT5 Init failed")
        return

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)

    strategy = Ema180MtfFvgStrategy()

    for symbol in SYMBOLS:
        print(f"\nFetching data for {symbol} (Last {DAYS} days)...")
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, utc_from, utc_to)
        
        if rates is None or len(rates) == 0:
            print(f" No M5 data for {symbol}")
            continue

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        
        h1_df, h4_df = get_h1_h4_data(symbol, utc_from, utc_to)

        print(f" Data loaded: M5={len(df)} bars, H1={len(h1_df) if h1_df is not None else 0} bars, H4={len(h4_df) if h4_df is not None else 0} bars")

        # We must align the backtester with the MTF data.
        # Since the backtester runs row by row, we'll inject the H1 and H4 data to the strategy via analyze's kwargs.
        # However, the standard FullFeatureBacktester does not automatically download/pass H1/H4 data unless customized.
        # Let's use a workaround: we monkey-patch the strategy's analyze method to always use the full H1/H4 dfs,
        # since EMA values for the *current* time can be approximated by taking the data up to that time.
        
        # Original analyze
        orig_analyze = strategy.analyze
        
        def patched_analyze(candles, profile, regime, **kwargs):
            current_time = candles.iloc[-1]["time"]
            
            # Filter H1 and H4 to only include data up to current_time to prevent lookahead bias
            if h1_df is not None:
                h1_up_to_now = h1_df[h1_df["time"] <= current_time]
            else:
                h1_up_to_now = None
                
            if h4_df is not None:
                h4_up_to_now = h4_df[h4_df["time"] <= current_time]
            else:
                h4_up_to_now = None

            return orig_analyze(
                candles=candles,
                profile=profile,
                regime=regime,
                h1_candles=h1_up_to_now,
                h4_candles=h4_up_to_now,
                **kwargs
            )
            
        strategy.analyze = patched_analyze

        print(f" Running backtest on {symbol}...")
        bt = Backtester(strategy, initial_equity=10000.0)
        contract_size = 100.0 if "XAU" in symbol else 5000.0
        result = bt.run(df, symbol, contract_size=contract_size)

        # Restore
        strategy.analyze = orig_analyze

        print("-" * 50)
        print(f"[{symbol}] Results:")
        print(f"   Win Rate:      {result.win_rate}%")
        print(f"   Total Trades:  {result.total_trades} ({result.winning_trades}W / {result.losing_trades}L)")
        print(f"   Profit Factor: {result.profit_factor}")
        print(f"   Net P&L:       ${result.total_profit_usd:.2f}")
        print(f"   Max DD:        {result.max_drawdown_pct}%")
        print("-" * 50)

    mt5.shutdown()
    print("\n Backtest Complete.")

if __name__ == "__main__":
    main()
