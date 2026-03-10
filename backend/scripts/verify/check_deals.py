import MetaTrader5 as mt5
from datetime import datetime, timedelta

def main():
    if not mt5.initialize():
        print("MT5 init failed")
        return
        
    date_to = datetime.now()
    date_from = date_to - timedelta(days=1)
    
    deals = mt5.history_deals_get(date_from, date_to, group="*XAUUSD*")
    if not deals:
        print("Failed to get deals or no deals found")
        mt5.shutdown()
        return
        
    print(f"Total deals: {len(deals)}")
    
    large_deals = [d for d in deals if getattr(d, 'volume', 0) > 50.0]
    
    if large_deals:
        print("LARGE DEALS FOUND:")
        for d in large_deals:
            print(d._asdict())
    else:
        print("No large deals (>50 lots) found today.")
        print("\nLast 5 deals:")
        for d in deals[-5:]:
            print(d._asdict())
        
    mt5.shutdown()

if __name__ == '__main__':
    main()
