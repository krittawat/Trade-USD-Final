import MetaTrader5 as mt5
import pandas as pd

if not mt5.initialize():
    print(f"FAILED to initialize MT5: {mt5.last_error()}")
    exit()

symbol = "XAUUSDm"
timeframe = mt5.TIMEFRAME_M5
rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 100)

if rates is None:
    print(f"FAILED to fetch rates for {symbol}: {mt5.last_error()}")
else:
    print(f"SUCCESS: Fetched {len(rates)} bars for {symbol}")
    df = pd.DataFrame(rates)
    print(df.tail())

mt5.shutdown()
