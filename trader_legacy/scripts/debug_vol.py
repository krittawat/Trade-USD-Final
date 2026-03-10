# -*- coding: utf-8 -*-
"""Quick debug: check what columns MT5 provides and if volume features compute correctly"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from trader.data.fetcher import fetcher
import MetaTrader5 as mt5
from trader.features.volatility import add_volatility_features

fetcher.connect()
df = fetcher.get_rates('XAUUSD', mt5.TIMEFRAME_M5, 200)

print("=== RAW COLUMNS ===")
print(df.columns.tolist())
print(f"\ntick_volume exists: {'tick_volume' in df.columns}")
print(f"Last 3 tick_volume: {df['tick_volume'].tail(3).tolist()}")

df = add_volatility_features(df)
last = df.iloc[-1]

print("\n=== AFTER FEATURES ===")
vol_cols = [c for c in df.columns if 'vol' in c or 'power' in c or 'tick' in c]
print(f"Volume/Power columns: {vol_cols}")
for c in vol_cols:
    print(f"  {c}: {last[c]}")

fetcher.disconnect()
