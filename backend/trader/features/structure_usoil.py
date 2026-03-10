import pandas as pd
import numpy as np

def identify_swings_usoil(df: pd.DataFrame, left: int = 5, right: int = 5) -> pd.DataFrame:
    """
    Identify Swing Highs and Lows for USOIL with Confirmation Lag (Causal).
    Vectorized version using rolling windows.
    """
    # 1. Swing Highs
    # A peak at i is a swing high if it's the max in [i-left, i+right]
    # and strictly greater than its neighbors (to handle flat tops)
    highs = df['high']
    window_size = left + right + 1
    
    # We use center=True to check i against neighbors, then shift by 'right' to be causal
    roll_max = highs.rolling(window=window_size, center=True).max()
    is_sh_raw = (highs == roll_max) & (roll_max.notna())
    
    # confirmed at i + right
    df['swing_high'] = is_sh_raw.shift(right).fillna(False)
    df['swing_high_raw'] = is_sh_raw
    
    # 2. Swing Lows
    lows = df['low']
    roll_min = lows.rolling(window=window_size, center=True).min()
    is_sl_raw = (lows == roll_min) & (roll_min.notna())
    
    df['swing_low'] = is_sl_raw.shift(right).fillna(False)
    df['swing_low_raw'] = is_sl_raw
    
    # Price Storage (Causal)
    df['swing_high_price'] = np.where(df['swing_high_raw'], df['high'], np.nan)
    df['swing_high_price'] = df['swing_high_price'].shift(right).ffill()
    
    df['swing_low_price'] = np.where(df['swing_low_raw'], df['low'], np.nan)
    df['swing_low_price'] = df['swing_low_price'].shift(right).ffill()
    
    return df

def detect_market_structure_usoil(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detect HH, LH, HL, LL using USOIL causal swing data.
    Vectorized using shift and comparisons.
    """
    if 'swing_high' not in df.columns:
        df = identify_swings_usoil(df)
        
    df['structure'] = 'NONE'
    
    # New swing detection (at confirmation bar)
    new_sh = df['swing_high'] & (~df['swing_high'].shift(1).fillna(False))
    new_sl = df['swing_low'] & (~df['swing_low'].shift(1).fillna(False))
    
    # Get previous confirmed swing prices
    # We need the price of the PREVIOUS confirmed swing high to compare with CURRENT confirmed one
    prev_sh_price = df['swing_high_price'].shift(1)
    prev_sl_price = df['swing_low_price'].shift(1)
    
    # HH: New SH price > Previous SH price
    is_hh = new_sh & (df['swing_high_price'] > prev_sh_price)
    is_lh = new_sh & (df['swing_high_price'] <= prev_sh_price)
    
    # LL: New SL price < Previous SL price
    is_ll = new_sl & (df['swing_low_price'] < prev_sl_price)
    is_hl = new_sl & (df['swing_low_price'] >= prev_sl_price)
    
    # Assign structure
    df.loc[is_hh, 'structure'] = 'HH'
    df.loc[is_lh, 'structure'] = 'LH'
    df.loc[is_ll, 'structure'] = 'LL'
    df.loc[is_hl, 'structure'] = 'HL'
    
    return df

def detect_displacement_usoil(df: pd.DataFrame, atr_multiplier: float = 2.5) -> pd.DataFrame:
    """
    Displacement: Unusually large body size indicating strong momentum/BOS.
    USOIL uses a stricter 2.5 ATR multiplier due to its inherent volatility.
    """
    if 'atr' not in df.columns:
        pass 
        
    if 'body_size' not in df.columns:
        df['body_size'] = np.abs(df['close'] - df['open'])

    df['displacement_up'] = (df['close'] > df['open']) & (df['body_size'] > df['atr'] * atr_multiplier)
    df['displacement_down'] = (df['close'] < df['open']) & (df['body_size'] > df['atr'] * atr_multiplier)
    
    return df
    
def add_structure_features_usoil(df: pd.DataFrame) -> pd.DataFrame:
    df = identify_swings_usoil(df)
    df = detect_market_structure_usoil(df)
    return df
