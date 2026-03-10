import MetaTrader5 as mt5
import pandas as pd

if not mt5.initialize():
    print(f"MT5 initialize failed")
    quit()

target = "USOILm"
positions = mt5.positions_get(symbol=target)

if positions:
    pos_list = [p._asdict() for p in positions]
    df = pd.DataFrame(pos_list)
    df['type_str'] = df['type'].apply(lambda x: 'BUY' if x == 0 else 'SELL')
    display_cols = ['ticket', 'symbol', 'type_str', 'volume', 'price_open', 'price_current', 'sl', 'tp', 'profit', 'magic']
    print(f"--- Open Positions for {target} ---")
    print(df[display_cols].to_string())
else:
    print(f"No open positions for {target}")

mt5.shutdown()
