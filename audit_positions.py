import MetaTrader5 as mt5

if not mt5.initialize():
    print("Failed to initialize MT5")
    quit()

acct = mt5.account_info()
positions = mt5.positions_get()

if not acct:
    print("Could not get account info")
    mt5.shutdown()
    quit()

print(f"--- ACCOUNT ---")
print(f"Balance: {acct.balance}")
print(f"Equity: {acct.equity}")
print(f"Profit: {acct.profit}")
print(f"Drawdown %: {(acct.balance - acct.equity) / acct.balance * 100:.2f}%")

print("\n--- POSITIONS ---")
for p in positions:
    print(f"Ticket: {p.ticket} | Symbol: {p.symbol} | Type: {'BUY' if p.type == 0 else 'SELL'} | Vol: {p.volume} | Open: {p.price_open} | Cur: {p.price_current}")
    print(f"SL: {p.sl} | TP: {p.tp} | Swap: {p.swap} | Profit: {p.profit}")
    
    # Calculate Risk to SL
    if p.sl > 0:
        sym_info = mt5.symbol_info(p.symbol)
        if sym_info:
            dist = abs(p.price_current - p.sl)
            risk_remaining = dist * p.volume * sym_info.trade_contract_size
            print(f"Remaining Risk to SL: ${risk_remaining:.2f}")

mt5.shutdown()
