import MetaTrader5 as mt5
if not mt5.initialize():
    print("MT5 Init Failed")
    quit()
symbols = mt5.symbols_get()
for s in symbols:
    if "BTC" in s.name:
        print(f"Symbol: {s.name}, Tradable: {s.visible}")
mt5.shutdown()
