import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone, timedelta

if not mt5.initialize():
    print("❌ MT5 Init failed")
    exit()

symbols = ["XAUUSDm", "XAGUSDm", "BTCUSDm", "USOILm"]
for s in symbols:
    print(f"Checking {s}...")
    rates = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M15, 0, 100)
    if rates is not None:
        print(f"OK {s}: {len(rates)} candles")
    else:
        print(f"FAIL {s}: No data. Error: {mt5.last_error()}")

mt5.shutdown()
