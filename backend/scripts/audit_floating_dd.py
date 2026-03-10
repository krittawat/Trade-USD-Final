import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

def audit():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return

    account_info = mt5.account_info()
    if account_info:
        print(f"--- ACCOUNT STATUS ---")
        print(f"Balance: {account_info.balance}")
        print(f"Equity: {account_info.equity}")
        print(f"Floating PnL: {account_info.profit}")
        dd = (account_info.balance - account_info.equity) / account_info.balance * 100 if account_info.balance > 0 else 0
        print(f"Floating DD: {dd:.2f}%")
        print(f"Margin Level: {account_info.margin_level}%")

    positions = mt5.positions_get()
    if positions:
        print(f"\n--- OPEN POSITIONS ({len(positions)}) ---")
        df = pd.DataFrame(list(positions), columns=positions[0]._asdict().keys())
        # Convert time
        df['time'] = pd.to_datetime(df['time'], unit='s')
        
        # Select key columns
        cols = ['symbol', 'type', 'volume', 'price_open', 'price_current', 'sl', 'tp', 'profit', 'time', 'comment']
        print(df[cols].to_string())
        
        # Check for SL gaps
        missing_sl = df[df['sl'] == 0]
        if not missing_sl.empty:
            print(f"\nCRITICAL: {len(missing_sl)} positions missing Stop Loss!")
            print(missing_sl[['symbol', 'ticket', 'time']])
    else:
        print("\nNo open positions.")

    mt5.shutdown()

if __name__ == "__main__":
    audit()
