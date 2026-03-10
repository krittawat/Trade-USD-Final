import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta
import os

def check_m5_uptrend_condition():
    if not mt5.initialize():
        print("MT5 initialize failed")
        return

    symbol = "BTCUSDm" # Correct symbol for Exness Standard
    if not mt5.symbol_select(symbol, True):
        print(f"Failed to select symbol {symbol}")
        mt5.shutdown()
        return

    timeframe = mt5.TIMEFRAME_M5
    
    # Fetch 400 bars for accurate EMA 200
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, 400)
    if rates is None:
        err = mt5.last_error()
        print(f"Failed to fetch rates for {symbol}: {err}")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df['close'] = df['close'].astype(float)
    
    # Calculate EMA 200
    df['ema_200'] = ta.ema(df['close'], length=200)
    
    latest = df.iloc[-1]
    close = latest['close']
    ema_200 = latest['ema_200']
    
    print(f"--- {symbol} M5 TREND ANALYSIS ---")
    print(f"Current Price: {close:.2f}")
    print(f"EMA 200 (Trend Line): {ema_200:.2f}")
    
    if close > ema_200:
        print("Status: Already UPTREND (▲)")
        print(f"Safety Margin: {close - ema_200:.2f} USD above Trend Line")
    else:
        print(f"Status: DOWNTREND (▼)")
        gap = ema_200 - close
        print(f"Condition: Price must CLOSE above {ema_200:.2f}")
        print(f"Gap: +{gap:.2f} USD to become UPTREND")
        
    mt5.shutdown()

if __name__ == "__main__":
    check_m5_uptrend_condition()
