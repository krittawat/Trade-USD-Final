import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta

def check_xau_trend():
    if not mt5.initialize():
        print("MT5 initialize failed")
        return

    symbol = "XAUUSDm"
    if not mt5.symbol_select(symbol, True):
        print(f"Failed to select symbol {symbol}")
        mt5.shutdown()
        return

    print(f"--- {symbol} TREND ANALYSIS ---")
    
    timeframes = {
        "M3": mt5.TIMEFRAME_M3,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1
    }

    for tf_name, tf_value in timeframes.items():
        rates = mt5.copy_rates_from_pos(symbol, tf_value, 0, 400)
        if rates is None:
            continue
            
        df = pd.DataFrame(rates)
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        
        # EMA 200 for Trend
        df['ema_200'] = ta.ema(df['close'], length=200)
        
        # Power calculation (same as system)
        df['ema_power'] = df['close'].ewm(span=13, adjust=False).mean()
        df['bull_power'] = df['high'] - df['ema_power']
        df['bear_power'] = df['low'] - df['ema_power']
        df['net_power'] = df['bull_power'] + df['bear_power']
        
        # Supertrend (used for regime classification)
        # Simplified for debug
        
        latest = df.iloc[-1]
        close = latest['close']
        ema_200 = latest['ema_200']
        net_power = latest['net_power']
        
        status = "UPTREND (UP)" if close > ema_200 else "DOWNTREND (DOWN)"
        
        print(f"[{tf_name}] Price: {close:.2f} | EMA 200: {ema_200:.2f} | {status} | Power: {net_power:+.2f}")
        
    mt5.shutdown()

if __name__ == "__main__":
    check_xau_trend()
