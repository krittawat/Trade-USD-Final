import pandas as pd
import numpy as np

def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 60) -> pd.DataFrame:
    """
    Detects Bullish and Bearish RSI Divergence:
    - Bullish: Price makes Lower Low (LL), RSI makes Higher Low (HL)
    - Bearish: Price makes Higher High (HH), RSI makes Lower High (LH)
    """
    if 'rsi' not in df.columns:
        return df
        
    df['div_bull'] = False
    df['div_bear'] = False
    
    # Simple pivot-based divergence (can be optimized but good for Alpha V5)
    for i in range(lookback, len(df)):
        # 1. Bullish Divergence check
        # Current low is lower than previous significant low, but RSI is higher
        curr_price_low = df['low'].iloc[i]
        curr_rsi_low = df['rsi'].iloc[i]
        
        # Look back for a pivot low
        for j in range(i-2, i-lookback, -1):
            prev_price_low = df['low'].iloc[j]
            prev_rsi_low = df['rsi'].iloc[j]
            
            # Check if j was a pivot low (simplified)
            if df['low'].iloc[j] < df['low'].iloc[j-1] and df['low'].iloc[j] < df['low'].iloc[j+1]:
                # LL in price, HL in RSI?
                if curr_price_low < prev_price_low and curr_rsi_low > prev_rsi_low:
                    if curr_rsi_low < 40: # Only significant in oversold territory
                        df.at[df.index[i], 'div_bull'] = True
                        break
                        
        # 2. Bearish Divergence check
        curr_price_high = df['high'].iloc[i]
        curr_rsi_high = df['rsi'].iloc[i]
        
        for j in range(i-2, i-lookback, -1):
            prev_price_high = df['high'].iloc[j]
            prev_rsi_high = df['rsi'].iloc[j]
            
            if df['high'].iloc[j] > df['high'].iloc[j-1] and df['high'].iloc[j] > df['high'].iloc[j+1]:
                # HH in price, LH in RSI?
                if curr_price_high > prev_price_high and curr_rsi_high < prev_rsi_high:
                    if curr_rsi_high > 60: # Only significant in overbought territory
                        df.at[df.index[i], 'div_bear'] = True
                        break
                        
    return df
