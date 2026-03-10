import MetaTrader5 as mt5

if not mt5.initialize():
    print("initialize() failed, error code =", mt5.last_error())
    quit()

symbols = mt5.symbols_get()
print(f"Total symbols: {len(symbols)}")

for sym in ["US30", "US30m", "USTEC", "USTECm", "NAS100", "NAS100m", "DJ30", "DJ30m"]:
    info = mt5.symbol_info(sym)
    if info:
        print(f"Symbol: {sym}")
        print(f"  Spread: {info.spread}")
        print(f"  Digits: {info.digits}")
        print(f"  Min volume: {info.volume_min}")
        print(f"  Trade mode: {info.trade_mode}")
        print(f"  Visible: {info.visible}")

mt5.shutdown()
