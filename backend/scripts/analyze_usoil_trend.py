import MetaTrader5 as mt5
import pandas as pd
import numpy as np

def analyze_trend(symbol):
    if not mt5.initialize():
        print("MT5 initialization failed")
        return

    # H1 Trend
    h1_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 200)
    if h1_rates is None:
        print(f"Failed to fetch H1 for {symbol}")
        mt5.shutdown()
        return

    h1_df = pd.DataFrame(h1_rates)
    h1_df['ema_200'] = h1_df['close'].ewm(span=200, adjust=False).mean()
    h1_latest = h1_df.iloc[-1]
    h1_trend = "BULLISH" if h1_latest['close'] > h1_latest['ema_200'] else "BEARISH"
    
    # M15 Trend
    m15_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 200)
    m15_df = pd.DataFrame(m15_rates)
    m15_df['ema_9'] = m15_df['close'].ewm(span=9, adjust=False).mean()
    m15_latest = m15_df.iloc[-1]
    m15_trend = "BULLISH" if m15_latest['close'] > m15_latest['ema_9'] else "BEARISH"

    print(f"--- Analysis for {symbol} ---")
    print(f"H1 Trend (HTF): {h1_trend} (Price: {h1_latest['close']:.4f} vs EMA200: {h1_latest['ema_200']:.4f})")
    print(f"M15 Trend: {m15_trend} (Price: {m15_latest['close']:.4f} vs EMA9: {m15_latest['ema_9']:.4f})")
    
    mt5.shutdown()

if __name__ == "__main__":
    analyze_trend("USOILm")
