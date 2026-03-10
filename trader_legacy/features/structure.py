import pandas as pd
import numpy as np

def identify_swings(df: pd.DataFrame, left: int = 3, right: int = 3) -> pd.DataFrame:
    """Identify Swing Highs and Lows."""
    df['swing_high'] = False
    df['swing_low'] = False
    
    for i in range(left, len(df) - right):
        # Check swing high
        is_sh = True
        for j in range(1, left + 1):
            if df['high'].iloc[i - j] >= df['high'].iloc[i]: is_sh = False
        for j in range(1, right + 1):
            if df['high'].iloc[i + j] >= df['high'].iloc[i]: is_sh = False
        if is_sh:
            df.at[df.index[i], 'swing_high'] = True
            
        # Check swing low
        is_sl = True
        for j in range(1, left + 1):
            if df['low'].iloc[i - j] <= df['low'].iloc[i]: is_sl = False
        for j in range(1, right + 1):
            if df['low'].iloc[i + j] <= df['low'].iloc[i]: is_sl = False
        if is_sl:
            df.at[df.index[i], 'swing_low'] = True
            
    return df

def detect_market_structure(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detect Higher Highs (HH), Lower Highs (LH), Higher Lows (HL), Lower Lows (LL)
    """
    if 'swing_high' not in df.columns:
        df = identify_swings(df)
        
    sh_prices = df[df['swing_high']]['high']
    sl_prices = df[df['swing_low']]['low']
    
    df['structure'] = 'NONE'
    
    # Needs sequence tracking, simplified for prototype
    last_sh = np.nan
    last_sl = np.nan
    
    for idx, row in df.iterrows():
        if row['swing_high']:
            if not np.isnan(last_sh):
                if row['high'] > last_sh:
                    df.at[idx, 'structure'] = 'HH'
                else:
                    df.at[idx, 'structure'] = 'LH'
            last_sh = row['high']
            
        if row['swing_low']:
            if not np.isnan(last_sl):
                if row['low'] > last_sl:
                    df.at[idx, 'structure'] = 'HL'
                else:
                    df.at[idx, 'structure'] = 'LL'
            last_sl = row['low']
            
    return df

def detect_displacement(df: pd.DataFrame, atr_multiplier: float = 2.0) -> pd.DataFrame:
    """
    Displacement: Unusually large body size indicating strong momentum/BOS.
    """
    if 'atr' not in df.columns:
        # Require external computation to avoid circular deps
        pass 
        
    if 'body_size' not in df.columns:
        df['body_size'] = np.abs(df['close'] - df['open'])

    df['displacement_up'] = (df['close'] > df['open']) & (df['body_size'] > df['atr'] * atr_multiplier)
    df['displacement_down'] = (df['close'] < df['open']) & (df['body_size'] > df['atr'] * atr_multiplier)
    
    return df
    
def add_structure_features(df: pd.DataFrame) -> pd.DataFrame:
    df = identify_swings(df)
    df = detect_market_structure(df)
    return df
