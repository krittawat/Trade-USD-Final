import MetaTrader5 as mt5
if not mt5.initialize():
    print("FAIL Init")
    exit()

symbols = ["XAUUSDm", "XAGUSDm", "BTCUSDm", "USOILm"]
for s in symbols:
    info = mt5.symbol_info(s)
    if info:
        print(f"{s}: contract_size={info.trade_contract_size}, point={info.point}, digits={info.digits}")
    else:
        print(f"FAIL {s} not found")
mt5.shutdown()
