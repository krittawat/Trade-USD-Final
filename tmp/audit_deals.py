import MetaTrader5 as mt5
from datetime import datetime, timedelta
import pandas as pd

def audit_deals():
    if not mt5.initialize():
        print("MT5 initialization failed")
        return

    # Look from start of day (UTC)
    now = datetime.now()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    print(f"Auditing deals from {start_of_day} to {now}")
    
    deals = mt5.history_deals_get(start_of_day, now)
    if deals is None:
        print("No deals found or error fetching deals")
        mt5.shutdown()
        return

    df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys()) if len(deals) > 0 else pd.DataFrame()
    
    if df.empty:
        print("No deals found for today.")
    else:
        # Filter for "OUT" deals to see realized PnL
        # deal_entry: 0=IN, 1=OUT, 2=IN/OUT
        out_deals = df[df['entry'] == 1].copy()
        
        total_pnl = out_deals['profit'].sum()
        total_commission = df['commission'].sum()
        total_swap = df['swap'].sum()
        net_daily = total_pnl + total_commission + total_swap
        
        print(f"\n--- Daily Audit Summary ---")
        print(f"Total Closed Deals: {len(out_deals)}")
        print(f"Realized Profit/Loss: ${total_pnl:.2f}")
        print(f"Total Commissions: ${total_commission:.2f}")
        print(f"Total Swaps: ${total_swap:.2f}")
        print(f"Net Daily PnL: ${net_daily:.2f}")
        
        print("\n--- Detailed Out Deals ---")
        for _, row in out_deals.iterrows():
            print(f"Ticket: {row['ticket']} | Symbol: {row['symbol']} | Profit: ${row['profit']:.2f} | Time: {pd.to_datetime(row['time'], unit='s')}")

    # Check open positions too
    positions = mt5.positions_get()
    if positions:
        pos_df = pd.DataFrame(list(positions), columns=positions[0]._asdict().keys())
        total_floating = pos_df['profit'].sum()
        print(f"\n--- Open Positions ---")
        print(f"Total Open: {len(positions)}")
        print(f"Total Floating PnL: ${total_floating:.2f}")
    else:
        print("\nNo open positions.")

    account_info = mt5.account_info()
    if account_info:
        print(f"\n--- Account Info ---")
        print(f"Balance: ${account_info.balance:.2f}")
        print(f"Equity: ${account_info.equity:.2f}")
        print(f"Margin Free: ${account_info.margin_free:.2f}")

    mt5.shutdown()

if __name__ == "__main__":
    audit_deals()
