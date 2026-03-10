import MetaTrader5 as mt5

if not mt5.initialize():
    print(f"MT5 initialize failed")
    quit()

target = "USOILm"
info = mt5.symbol_info(target)

if info is None:
    print(f"Symbol {target} not found directly. Searching...")
    all_symbols = mt5.symbols_get()
    for s in all_symbols:
        if "USOIL" in s.name.upper():
            print(f"Found match: {s.name}")
            info = mt5.symbol_info(s.name)
            target = s.name
            break

if info:
    print(f"--- {target} Status ---")
    print(f"Bid: {info.bid}")
    print(f"Ask: {info.ask}")
    print(f"Spread: {info.spread}")
    print(f"Volume Min: {info.volume_min}")
    print(f"Trade Mode: {info.trade_mode}")
    
    positions = mt5.positions_get(symbol=target)
    if positions:
        print(f"Found {len(positions)} positions for {target}")
    else:
        print(f"No open positions for {target}")
else:
    print(f"Symbol {target} (or similar) not found.")

mt5.shutdown()
