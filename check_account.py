import MetaTrader5 as mt5
from datetime import datetime, timedelta
import json
from pathlib import Path

def check_status():
    if not mt5.initialize():
        print("MT5 initialization failed")
        return

    info = mt5.account_info()
    if info is None:
        print("Failed to get account info")
        return

    print(f"--- Account Info ---")
    print(f"Balance: {info.balance}")
    print(f"Equity: {info.equity}")
    print(f"Margin Level: {info.margin_level}%")
    print(f"Free Margin: {info.margin_free}")

    # Check daily deals
    start_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(start_date, datetime.now())
    daily_pnl = 0.0
    if deals:
        for d in deals:
            if d.entry == 1: # Entry Out (Close)
                daily_pnl += d.profit
    
    print(f"\n--- Daily PnL (from 00:00 UTC) ---")
    print(f"Daily PnL: ${daily_pnl:.2f}")
    if info.equity > 0:
        print(f"Loss % of Equity: {abs(daily_pnl)/info.equity*100:.2f}%")

    mt5.shutdown()

if __name__ == "__main__":
    check_status()
