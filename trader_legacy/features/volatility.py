import pandas as pd
import numpy as np

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Computes Average True Range."""
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['atr'] = true_range.rolling(period).mean()
    
    # Baseline ATR (longer term)
    df['atr_baseline'] = true_range.rolling(period * 4).mean()
    return df

def compute_range_compression(df: pd.DataFrame, lookback: int = 10) -> pd.DataFrame:
    """
    Computes range compression ratio.
    Ratio of current ATR to historical maximum ATR over lookback.
    Returns value close to 0 when compressed.
    """
    if 'atr' not in df.columns:
        df = compute_atr(df)
        
    df['max_atr_lookback'] = df['atr'].rolling(lookback).max()
    df['compression_ratio'] = df['atr'] / df['max_atr_lookback'].replace(0, np.nan)
    return df

def compute_candle_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes body size, wick sizes, and their ratios.
    """
    df['body_size'] = np.abs(df['close'] - df['open'])
    df['upper_wick'] = df['high'] - np.maximum(df['open'], df['close'])
    df['lower_wick'] = np.minimum(df['open'], df['close']) - df['low']
    
    total_range = df['high'] - df['low']
    # Avoid division by zero
    valid_range = total_range.replace(0, np.nan)
    
    df['body_ratio'] = df['body_size'] / valid_range
    df['upper_wick_ratio'] = df['upper_wick'] / valid_range
    df['lower_wick_ratio'] = df['lower_wick'] / valid_range
    
    return df

def compute_volume_features(df: pd.DataFrame, lookback: int = 20, spike_mult: float = 2.0, dryup_mult: float = 0.5) -> pd.DataFrame:
    """
    Tick Volume Intelligence:
    - vol_avg: Rolling average volume
    - vol_ratio: Current volume / average (>1 = above avg)
    - vol_spike: True when volume > spike_mult * average (confirms sweep/displacement)
    - vol_dryup: True when volume < dryup_mult * average (pre-breakout compression)
    """
    if 'tick_volume' not in df.columns:
        return df

    df['vol_avg'] = df['tick_volume'].rolling(lookback).mean()
    df['vol_ratio'] = df['tick_volume'] / df['vol_avg'].replace(0, 1)
    df['vol_spike'] = df['vol_ratio'] > spike_mult
    df['vol_dryup'] = df['vol_ratio'] < dryup_mult
    return df

def compute_bull_bear_power(df: pd.DataFrame, ema_period: int = 13) -> pd.DataFrame:
    """
    Bull Power = High - EMA (buyer strength above equilibrium)
    Bear Power = Low - EMA (seller strength below equilibrium)
    Positive bull_power = buyers pushing above EMA
    Negative bear_power = sellers pushing below EMA
    """
    df['ema_power'] = df['close'].ewm(span=ema_period, adjust=False).mean()
    df['bull_power'] = df['high'] - df['ema_power']
    df['bear_power'] = df['low'] - df['ema_power']
    # Net power: positive = bulls dominate, negative = bears dominate
    df['net_power'] = df['bull_power'] + df['bear_power']
    return df

def add_volatility_features(df: pd.DataFrame) -> pd.DataFrame:
    """Master function to enrich DataFrame with all volatility + volume + power features."""
    df = compute_atr(df)
    df = compute_range_compression(df)
    df = compute_candle_metrics(df)
    df = compute_volume_features(df)
    df = compute_bull_bear_power(df)
    return df
