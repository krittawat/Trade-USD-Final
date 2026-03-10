import MetaTrader5 as mt5
import pandas as pd

if not mt5.initialize():
    print(f"MT5 initialize failed")
    quit()

symbol = "USOIL" # Standard mapping
info = mt5.symbol_info(symbol)
if info is None:
    # Try mapping if standard name doesn't work
    for s in mt5.symbols_get():
        if "OIL" in s.name.upper():
            print(f"Found related symbol: {s.name}")
            symbol = s.name
            info = mt5.symbol_info(symbol)
            break

if info:
    print(f"--- {symbol} Status ---")
    print(f"Bid: {info.bid}")
    print(f"Ask: {info.ask}")
    print(f"Spread: {info.spread}")
    print(f"Trade Mode: {info.trade_mode}")
    print(f"Volume Min: {info.volume_min}")
else:
    print(f"Symbol {symbol} not found in MT5.")

mt5.shutdown()
