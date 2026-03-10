import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
import sys
import json

def analyze_history(days=30):
    if not mt5.initialize():
        print("MT5 initialize failed")
        return

    from_date = datetime.now() - timedelta(days=days)
    to_date = datetime.now()
    
    # Get history deals
    deals = mt5.history_deals_get(from_date, to_date)
    if deals is None or len(deals) == 0:
        print(f"No deals found in the last {days} days.")
        mt5.shutdown()
        return

    df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())
    # Filter for closed trades (entry out or out_by_sl/tp)
    # Entry 0=In, 1=Out, 2=In/Out
    closed_deals = df[df['entry'] == 1].copy()
    
    if closed_deals.empty:
        print("No closed deals found.")
        mt5.shutdown()
        return

    # Basic Metrics
    total_pnl = closed_deals['profit'].sum()
    win_rate = (closed_deals['profit'] > 0).mean() * 100
    avg_win = closed_deals[closed_deals['profit'] > 0]['profit'].mean()
    avg_loss = closed_deals[closed_deals['profit'] < 0]['profit'].mean()
    
    print(f"--- Trade History Analysis (Last {days} days) ---")
    print(f"Total Trades: {len(closed_deals)}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Total PnL: ${total_pnl:.2f}")
    print(f"Avg Win: ${avg_win:.2f} | Avg Loss: ${avg_loss:.2f}")
    
    # Symbol breakdown
    sym_stats = closed_deals.groupby('symbol').agg({
        'profit': ['sum', 'count', 'mean'],
    })
    print("\n--- Symbol Performance ---")
    print(sym_stats)

    # Hour of day analysis
    closed_deals['hour'] = pd.to_datetime(closed_deals['time'], unit='s').dt.hour
    hour_stats = closed_deals.groupby('hour')['profit'].sum()
    print("\n--- Hourly Performance ---")
    print(hour_stats)

    mt5.shutdown()

if __name__ == "__main__":
    analyze_history(30)
