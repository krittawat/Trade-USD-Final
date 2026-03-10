import MetaTrader5 as mt5
from datetime import datetime, timedelta

if not mt5.initialize():
    print("MT5 Not initialized")
else:
    info = mt5.account_info()
    print(f"Equity: {info.equity}, Balance: {info.balance}")
    
    today_start = datetime.now().replace(hour=0, minute=0, second=0)
    deals = mt5.history_deals_get(today_start, datetime.now())
    daily_pnl = 0.0
    
    print(f"Total deals today: {len(deals) if deals else 0}")
    if deals:
        for d in deals:
            print(f"Deal {d.ticket}: type={d.type}, entry={d.entry}, profit={d.profit}, symbol={d.symbol}")
            if d.entry == 1:
                daily_pnl += d.profit

    print(f"Daily PnL computed: {daily_pnl}")
    mt5.shutdown()
