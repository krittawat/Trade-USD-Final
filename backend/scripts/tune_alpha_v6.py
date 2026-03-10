import sys
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
from app.execution.backtester import Backtester
from app.strategy.templates.alpha_v6_smc import AlphaV6SMCStrategy

def run_test(symbol, days=60):
    print(f"\n--- Backtesting Alpha V6 SMC on {symbol} for {days} days ---")
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    
    # We will fetch M15 as the strategy supports it and we configured 15m in registry
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, utc_from, utc_to)
    
    if rates is None or len(rates) == 0:
        print(f"❌ No data for {symbol}")
        return
        
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"✅ Loaded {len(df)} candles for {symbol} (M15)")

    strategy = AlphaV6SMCStrategy(symbol)
    bt = Backtester(strategy, initial_equity=10000.0)
    
    try:
        # Use simple standard backtester for speed
        result = bt.run(df, symbol)
        print(f"\n[RESULTS {symbol}]")
        print(f"Total Trades: {result.total_trades}")
        print(f"Win Rate:     {result.win_rate}%")
        print(f"Profit Fctr:  {result.profit_factor}")
        print(f"P&L:          ${result.total_profit_usd:.2f}")
        print(f"Max DD:       {result.max_drawdown_pct}%")
    except Exception as e:
        print(f"❌ Error during backtest on {symbol}: {e}")

if __name__ == "__main__":
    if not mt5.initialize():
        print("❌ MT5 Init failed")
        sys.exit(1)
        
    symbols_to_test = ["XAUUSDm", "XAGUSDm", "BTCUSDm"]
    
    for sym in symbols_to_test:
        run_test(sym, days=60)
        
    mt5.shutdown()
