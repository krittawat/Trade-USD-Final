import MetaTrader5 as mt5
if not mt5.initialize():
    print("MT5 initialize failed")
    quit()

symbols = mt5.symbols_get()
if symbols is None:
    print("No symbols found")
else:
    names = [s.name for s in symbols]
    print(f"Total symbols: {len(names)}")
    print(f"First 10 symbols: {names[:10]}")
    # Search for XAUUSD specifically
    xau_symbols = [n for n in names if "XAUUSD" in n]
    print(f"XAUUSD related symbols: {xau_symbols}")

mt5.shutdown()
