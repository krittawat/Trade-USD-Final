import MetaTrader5 as mt5

if not mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe"):
    print("initialize() failed")
    mt5.shutdown()
    exit()

symbols = ['XAUUSDc', 'XAGUSDc', 'BTCUSDc']
print("=== MT5 Max Broker Volume ===")
for sym in symbols:
    info = mt5.symbol_info(sym)
    if info is not None:
        print(f"{sym}: Max Lot = {info.volume_max} (Broker Limit)")
    else:
        print(f"{sym}: Not found in MT5")

mt5.shutdown()
