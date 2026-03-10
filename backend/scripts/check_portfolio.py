import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

def check_portfolio():
    if not mt5.initialize():
        print("MT5 initialization failed")
        return

    # Get account info
    account_info = mt5.account_info()
    if account_info:
        print(f"--- Account Info ---")
        print(f"Login: {account_info.login}")
        print(f"Balance: {account_info.balance:.2f} USD")
        print(f"Equity: {account_info.equity:.2f} USD")
        print(f"Profit: {account_info.profit:.2f} USD")
        print(f"Margin: {account_info.margin:.2f} USD")
        print(f"Margin Free: {account_info.margin_free:.2f} USD")
        print(f"Margin Level: {account_info.margin_level:.2f}%")
        print(f"Floating DD: {(1 - account_info.equity / account_info.balance) * 100:.2f}%")
        print("-" * 20)

    # Get positions
    positions = mt5.positions_get()
    if positions:
        print(f"\n--- Open Positions ({len(positions)}) ---")
        df = pd.DataFrame(list(positions), columns=positions[0]._asdict().keys())
        # Convert type to readable names
        df['type_name'] = df['type'].apply(lambda x: "BUY" if x == 0 else "SELL")
        # Keep relevant columns
        cols = ['symbol', 'type_name', 'volume', 'price_open', 'price_current', 'sl', 'tp', 'profit', 'time']
        display_df = df[cols].copy()
        display_df['time'] = pd.to_datetime(display_df['time'], unit='s')
        print(display_df.to_string(index=False))
    else:
        print("\nNo open positions.")

    mt5.shutdown()

if __name__ == "__main__":
    check_portfolio()
