import MetaTrader5 as mt5
import pandas as pd
import os
from datetime import datetime

if not mt5.initialize():
    print(f"MT5 initialize failed, error code: {mt5.last_error()}")
    quit()

positions = mt5.positions_get()
if positions is None:
    print("No positions found or error.")
elif len(positions) == 0:
    print("Current: No open positions.")
else:
    # Convert to list of dicts
    pos_list = [p._asdict() for p in positions]
    df = pd.DataFrame(pos_list)
    
    # type 0=BUY, 1=SELL
    df['type_str'] = df['type'].apply(lambda x: 'BUY' if x == 0 else 'SELL')
    
    # Select and rename columns for readability
    display_cols = ['ticket', 'symbol', 'type_str', 'volume', 'price_open', 'price_current', 'sl', 'tp', 'profit', 'magic']
    print("--- Open Positions ---")
    print(df[display_cols].to_string())

mt5.shutdown()
