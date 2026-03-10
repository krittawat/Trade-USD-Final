import pandas as pd
import numpy as np

def add_institutional_features(df: pd.DataFrame) -> pd.DataFrame:
    """Entry point for all institutional signatures."""
    df = detect_fvg(df)
    df = detect_order_blocks(df)
    df = detect_pd_zones(df)
    df = detect_session_sweeps(df)
    return df

def detect_fvg(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detect Fair Value Gaps (FVG).
    A Bullish FVG exists if High[i-1] < Low[i+1].
    A Bearish FVG exists if Low[i-1] > High[i+1].
    """
    df['fvg_bull'] = 0.0
    df['fvg_bear'] = 0.0
    
    # Needs at least 3 candles
    for i in range(1, len(df) - 1):
        prev_high = df['high'].iloc[i-1]
        next_low = df['low'].iloc[i+1]
        
        # Bullish FVG
        if next_low > prev_high:
            df.at[df.index[i], 'fvg_bull'] = next_low - prev_high
            
        prev_low = df['low'].iloc[i-1]
        next_high = df['high'].iloc[i+1]
        
        # Bearish FVG
        if next_high < prev_low:
            df.at[df.index[i], 'fvg_bear'] = prev_low - next_high
            
    return df

def detect_order_blocks(df: pd.DataFrame, lookback: int = 50) -> pd.DataFrame:
    """
    Identify Order Blocks (OB).
    Bullish OB: The last down-close candle before a strong bullish displacement.
    Bearish OB: The last up-close candle before a strong bearish displacement.
    """
    df['ob_bull'] = False
    df['ob_bear'] = False
    
    if 'displacement_up' not in df.columns:
        # Fallback if displacement not computed
        df['displacement_up'] = False
        df['displacement_down'] = False

    for i in range(1, len(df)):
        # Bullish OB Check
        if df['displacement_up'].iloc[i]:
            # Look for the last red candle
            for j in range(i-1, max(0, i-5), -1):
                if df['close'].iloc[j] < df['open'].iloc[j]:
                    df.at[df.index[j], 'ob_bull'] = True
                    break
                    
        # Bearish OB Check
        if df['displacement_down'].iloc[i]:
            # Look for the last green candle
            for j in range(i-1, max(0, i-5), -1):
                if df['close'].iloc[j] > df['open'].iloc[j]:
                    df.at[df.index[j], 'ob_bear'] = True
                    break
                    
    return df

def detect_pd_zones(df: pd.DataFrame, lookback: int = 40) -> pd.DataFrame:
    """
    Premium / Discount Zones.
    50% Equilibrium of the lookback range.
    Price > 50% = Premium (Expensive)
    Price < 50% = Discount (Cheap)
    """
    df['range_high'] = df['high'].rolling(lookback).max()
    df['range_low'] = df['low'].rolling(lookback).min()
    df['equilibrium'] = (df['range_high'] + df['range_low']) / 2
    
    df['pd_zone'] = "EQUILIBRIUM"
    df.loc[df['close'] > df['equilibrium'], 'pd_zone'] = "PREMIUM"
    df.loc[df['close'] < df['equilibrium'], 'pd_zone'] = "DISCOUNT"
    return df

def detect_session_sweeps(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detects when price sweeps the high/low of a previous trading session.
    Requires 'session' feature from time_utils.
    """
    if 'session' not in df.columns:
        return df
        
    df['sweep_high'] = False
    df['sweep_low'] = False
    
    # Store previous session extrema
    session_extrema = {} # {session_name: {high, low}}
    
    for i in range(1, len(df)):
        curr_session = df['session'].iloc[i]
        prev_session = df['session'].iloc[i-1]
        
        # If session changed, we can check for sweeps of previous sessions
        if curr_session != prev_session and prev_session != "OUT_OF_SESSION":
            # Record previous session high/low
            session_bars = df[df['session'] == prev_session]
            if not session_bars.empty:
                session_extrema[prev_session] = {
                    "high": session_bars['high'].max(),
                    "low": session_bars['low'].min()
                }
        
        # Check current price against all historical session extrema
        curr_high = df['high'].iloc[i]
        curr_low = df['low'].iloc[i]
        
        for sess, ext in session_extrema.items():
            if sess == curr_session: continue # Don't sweep current session yet
            
            # Sweep High: Price went above session high then closed below? 
            # Simplified: Price currently above session high
            if curr_high > ext['high'] and df['close'].iloc[i] <= ext['high']:
                df.at[df.index[i], 'sweep_high'] = True
                
            if curr_low < ext['low'] and df['close'].iloc[i] >= ext['low']:
                df.at[df.index[i], 'sweep_low'] = True
                
    return df
