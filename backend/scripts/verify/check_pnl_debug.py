
import MetaTrader5 as mt5
from datetime import datetime, timezone, timedelta
import pandas as pd

def utc_day_start():
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)

def main():
    if not mt5.initialize():
        print("MT5 Init Failed")
        return

    start = utc_day_start()
    end = datetime.now(timezone.utc)
    
    deals = mt5.history_deals_get(start, end)
    if deals is None:
        print("No deals found")
        return
        
    df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())
    # Convert time to readable format
    df['time_readable'] = pd.to_datetime(df['time'], unit='s')
    
    # Filter for closed trades (entry=1)
    closed = df[df['entry'] == 1].copy()
    closed['total_pnl'] = closed['profit'] + closed['commission'] + closed['swap'] + closed.get('fee', 0.0)
    
    print(f"Total Daily PnL: {closed['total_pnl'].sum():.2f}")
    print("\nRecent Closed Deals:")
    print(closed[['ticket', 'symbol', 'time_readable', 'profit', 'total_pnl']].tail(10))
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
