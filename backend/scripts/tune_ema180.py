"""
Optimized Backtest & Tuning Script for EMA 180 MTF + FVG Strategy
"""
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import MetaTrader5 as mt5
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.execution.backtester import Backtester
from app.strategy.templates.ema180_mtf_fvg import Ema180MtfFvgStrategy

SYMBOLS = ["BTCUSDc", "USDJPYc"]
DAYS = 30  # Reduce days for fast tuning

def get_mtf_features(df_m5, df_h1, df_h4):
    """
    Pre-computes H1 and H4 EMA trend alignment to avoid O(N^2) slicing in the loop.
    Merges MTF signals back onto the M5 dataframe.
    """
    df_m5 = df_m5.copy()
    
    # 1. Calculate EMAs on H1
    df_h1['h1_ema50'] = df_h1['close'].ewm(span=20, adjust=False).mean()
    df_h1['h1_ema200'] = df_h1['close'].ewm(span=180, adjust=False).mean()
    df_h1['h1_trend_up'] = df_h1['h1_ema50'] > df_h1['h1_ema200']
    df_h1['h1_trend_down'] = df_h1['h1_ema50'] < df_h1['h1_ema200']
    
    # 2. Calculate EMAs on H4
    df_h4['h4_ema50'] = df_h4['close'].ewm(span=20, adjust=False).mean()
    df_h4['h4_ema200'] = df_h4['close'].ewm(span=180, adjust=False).mean()
    df_h4['h4_trend_up'] = df_h4['h4_ema50'] > df_h4['h4_ema200']
    df_h4['h4_trend_down'] = df_h4['h4_ema50'] < df_h4['h4_ema200']
    
    # Drop irrelevant columns before merge
    h1_subset = df_h1[['time', 'h1_trend_up', 'h1_trend_down']].dropna()
    h4_subset = df_h4[['time', 'h4_trend_up', 'h4_trend_down']].dropna()
    
    # Use merge_asof to align H1/H4 values to the M5 timestamps (backward looking)
    df_m5 = pd.merge_asof(df_m5, h1_subset, on='time', direction='backward')
    df_m5 = pd.merge_asof(df_m5, h4_subset, on='time', direction='backward')
    
    # Fill NA for early bars
    df_m5.fillna(False, inplace=True)
    return df_m5

def run_tuning():
    print("=" * 60)
    print(" TUNE: EMA 180 MTF + FVG Strategy")
    print("=" * 60)

    if not mt5.initialize():
        print(" MT5 Init failed")
        return

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)

    strategy = Ema180MtfFvgStrategy()

    for symbol in SYMBOLS:
        print(f"\nFetching data for {symbol} (Last {DAYS} days)...")
        # Fetch M5
        rates_m5 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, utc_from - timedelta(days=10), utc_to)
        # Fetch H1 / H4 (need more lookback for EMA 200)
        rates_h1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, utc_from - timedelta(days=60), utc_to)
        rates_h4 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H4, utc_from - timedelta(days=200), utc_to)
        
        if rates_m5 is None or len(rates_m5) == 0 or \
           rates_h1 is None or len(rates_h1) == 0 or \
           rates_h4 is None or len(rates_h4) == 0:
            print(f" Missing MTF data for {symbol}")
            continue

        df_m5 = pd.DataFrame(rates_m5)
        df_h1 = pd.DataFrame(rates_h1)
        df_h4 = pd.DataFrame(rates_h4)
        
        for df in [df_m5, df_h1, df_h4]:
            df["time"] = pd.to_datetime(df["time"], unit="s")
            
        print("Pre-computing MTF indicators...")
        df_m5_merged = get_mtf_features(df_m5, df_h1, df_h4)
        
        # Monkey patch strategy to read pre-computed MTF columns instead of raw H1/H4 data
        orig_analyze = strategy.analyze
        def patched_analyze(candles, profile, regime, **kwargs):
            # We recreate a mock h1/h4 structure holding the precomputed values at the last row
            last_row = candles.iloc[-1]
            h1_trend_up = last_row.get('h1_trend_up', False)
            h1_trend_down = last_row.get('h1_trend_down', False)
            h4_trend_up = last_row.get('h4_trend_up', False)
            h4_trend_down = last_row.get('h4_trend_down', False)

            def make_mock_df(is_up, is_down):
                # Strategy expects ewm(span=fast).mean() > ewm(span=slow).mean() to evaluate trend
                # We can cheat by providing a tiny 1-row fake dataframe where close equals exactly what's needed
                return None 

            # Since the original strategy evaluates EMAs inside analyze on the passed df, 
            # the easiest patch is to actually evaluate the strategy but override the MTF checks in it.
            # But the original code is in strategy/templates/ema180_mtf_fvg.py
            # The original code looks at h1_candles and h4_candles lengths. If None, it defaults to False.
            # But wait, original code will fail if we pass None unless we overwrite the check entirely.
            pass
        
        # Actually it's cleaner to rewrite the core logic slightly or just use a dummy H1/H4 object.
        # Let's use the simplest approach: create a tiny DF for h1 and h4 that forces the EMA crossover result.
        def mock_mtf_analyze(candles, profile, regime, **kwargs):
            last = candles.iloc[-1]
            
            # Create a fake df that forces the exact boolean evaluations
            h1_fake = pd.DataFrame({'close': [100.0] * 300}) # > mtf_ema_slow (180)
            h4_fake = pd.DataFrame({'close': [100.0] * 300})

            # But EWM makes the last value equal to the constant.
            # We can't easily mock ewm crossover with constants.
            # INSTEAD: We will temporarily patch the strategy class's method inside this script!
            pass
            
        # Instead of monkeypatching `analyze` intricately, 
        # let's just pass the sliced H1/H4 by fast-slicing using Index.
        df_h1.set_index('time', inplace=True)
        df_h4.set_index('time', inplace=True)
        df_m5_time_indexed = df_m5.copy().set_index('time')
        
        def fast_sliced_analyze(candles, profile, regime, **kwargs):
             current_time = candles.iloc[-1]["time"]
             h1_slice = df_h1.loc[:current_time]
             h4_slice = df_h4.loc[:current_time]
             return orig_analyze(
                 candles=candles, profile=profile, regime=regime,
                 h1_candles=h1_slice.reset_index(), h4_candles=h4_slice.reset_index()
             )
             
        strategy.analyze = fast_sliced_analyze

        # Restrict backtest run to the last 30 days exactly by filtering df_m5
        target_time = pd.to_datetime(utc_from.replace(tzinfo=None))
        df_target = df_m5[df_m5['time'] >= target_time].copy()

        bt = Backtester(strategy, initial_equity=10000.0)
        
        if "XAU" in symbol:
            contract_size = 1.0
        elif "BTC" in symbol:
            contract_size = 0.01
        elif "JPY" in symbol:
            contract_size = 1000.0
        elif "XAG" in symbol:
            contract_size = 50.0
        else:
            contract_size = 100.0
        
        start = time.time()
        result = bt.run(df_target, symbol, contract_size=contract_size)
        dur = time.time() - start

        strategy.analyze = orig_analyze

        print("-" * 50)
        print(f"[{symbol}] Results (Took {dur:.1f}s):")
        print(f"   Win Rate:      {result.win_rate}%")
        print(f"   Total Trades:  {result.total_trades} ({result.winning_trades}W / {result.losing_trades}L)")
        print(f"   Profit Factor: {result.profit_factor}")
        print(f"   Net P&L:       ${result.total_profit_usd:.2f}")
        print(f"   Max DD:        {result.max_drawdown_pct}%")
        print("-" * 50)

    mt5.shutdown()
    print("\n Tuning Complete.")

if __name__ == "__main__":
    run_tuning()
