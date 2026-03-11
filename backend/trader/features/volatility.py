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

def compute_volume_features(
    df: pd.DataFrame,
    lookback: int = 20,
    spike_mult: float = 2.0,
    dryup_mult: float = 0.5,
) -> pd.DataFrame:
    """
    Tick Volume Intelligence:
    - tick_vol_avg / tick_vol_ratio: short-term participation
    - tick_vol_avg_slow / tick_vol_ratio_slow: slower benchmark for entry quality
    - buy_pressure / sell_pressure: where price closes within the candle range
    - volume_side: dominant direction behind the current candle
    - volume_climax: very large participation but weak body (exhaustion risk)
    - vol_* aliases remain for backward compatibility with existing strategies
    """
    if 'tick_volume' not in df.columns:
        return df

    short_lookback = max(2, int(lookback))
    slow_lookback = max(20, short_lookback * 2)

    df['tick_vol_avg'] = df['tick_volume'].rolling(short_lookback).mean()
    df['tick_vol_ratio'] = df['tick_volume'] / df['tick_vol_avg'].replace(0, 1)
    df['tick_vol_avg_slow'] = df['tick_volume'].rolling(slow_lookback).mean()
    df['tick_vol_ratio_slow'] = df['tick_volume'] / df['tick_vol_avg_slow'].replace(0, 1)

    # Backward-compatible aliases used across the strategy set.
    df['vol_avg'] = df['tick_vol_avg']
    df['vol_ratio'] = df['tick_vol_ratio']
    df['vol_spike'] = df['tick_vol_ratio'] > spike_mult
    df['vol_dryup'] = df['tick_vol_ratio'] < dryup_mult

    candle_range = (df['high'] - df['low']).replace(0, np.nan)
    df['buy_pressure'] = ((df['close'] - df['low']) / candle_range).clip(0.0, 1.0).fillna(0.5)
    df['sell_pressure'] = ((df['high'] - df['close']) / candle_range).clip(0.0, 1.0).fillna(0.5)
    df['volume_energy'] = (df['tick_vol_ratio_slow'].clip(lower=0.0) * df['body_ratio'].fillna(0.0)).fillna(0.0)
    df['volume_climax'] = (
        (df['tick_vol_ratio_slow'] >= 2.5) &
        (df['body_ratio'].fillna(0.0) <= 0.35)
    )

    vol_side = np.where(
        (df['close'] > df['open']) & (df['buy_pressure'] >= 0.55),
        "BUY",
        np.where(
            (df['close'] < df['open']) & (df['sell_pressure'] >= 0.55),
            "SELL",
            np.where(
                (df['buy_pressure'] - df['sell_pressure']) >= 0.15,
                "BUY",
                np.where(
                    (df['sell_pressure'] - df['buy_pressure']) >= 0.15,
                    "SELL",
                    "NEUTRAL",
                ),
            ),
        ),
    )
    df['volume_side'] = pd.Series(vol_side, index=df.index)
    return df

def compute_bull_bear_power(
    df: pd.DataFrame,
    ema_period: int = 13,
    mt5_period: int = 25,
) -> pd.DataFrame:
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

    # MT5-style variant (often configured as Bulls/Bears period 25 on chart)
    mt5_ema_col = f'ema_power_{mt5_period}'
    bull_col = f'bull_power_{mt5_period}'
    bear_col = f'bear_power_{mt5_period}'
    net_col = f'net_power_{mt5_period}'
    df[mt5_ema_col] = df['close'].ewm(span=mt5_period, adjust=False).mean()
    df[bull_col] = df['high'] - df[mt5_ema_col]
    df[bear_col] = df['low'] - df[mt5_ema_col]
    df[net_col] = df[bull_col] + df[bear_col]
    return df

def compute_ema_trend(df: pd.DataFrame, fast: int = 21, slow: int = 50) -> pd.DataFrame:
    """
    EMA Trend Confirmation:
    - ema_fast (EMA 21): short-term trend
    - ema_slow (EMA 50): medium-term trend
    - ema_bullish: True when EMA fast > EMA slow (uptrend alignment)
    Used as mandatory confluence filter for strategy signals.
    """
    df['ema_fast'] = df['close'].ewm(span=fast, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=slow, adjust=False).mean()
    df['ema_bullish'] = df['ema_fast'] > df['ema_slow']
    return df

def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Computes Relative Strength Index (Wilder's method)."""
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))
    return df


def compute_bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    """Computes Bollinger Bands on close price."""
    df['bb_mid'] = df['close'].rolling(period).mean()
    bb_std = df['close'].rolling(period).std()
    df['bb_upper'] = df['bb_mid'] + (bb_std * std_dev)
    df['bb_lower'] = df['bb_mid'] - (bb_std * std_dev)
    return df


def compute_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
    """Computes Stochastic Oscillator %K and %D."""
    low_min = df['low'].rolling(k_period).min()
    high_max = df['high'].rolling(k_period).max()
    denom = (high_max - low_min).replace(0, np.nan)
    df['stoch_k'] = 100 * ((df['close'] - low_min) / denom)
    df['stoch_d'] = df['stoch_k'].rolling(d_period).mean()
    return df


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Computes MACD line, signal line, and histogram."""
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    df['macd_line'] = ema_fast - ema_slow
    df['macd_signal'] = df['macd_line'].ewm(span=signal, adjust=False).mean()
    df['macd_hist'] = df['macd_line'] - df['macd_signal']
    return df


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Computes Average Directional Index (ADX) for trend strength."""
    plus_dm = df['high'].diff()
    minus_dm = df['low'].diff().abs()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm_val = df['low'].shift() - df['low']
    minus_dm_val = minus_dm_val.where((minus_dm_val > df['high'].diff()) & (minus_dm_val > 0), 0.0)

    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)

    atr_adx = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr_adx.replace(0, np.nan))
    minus_di = 100 * (minus_dm_val.rolling(period).mean() / atr_adx.replace(0, np.nan))
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    df['adx'] = dx.rolling(period).mean()
    df['plus_di'] = plus_di
    df['minus_di'] = minus_di
    return df


def compute_ema_200(df: pd.DataFrame) -> pd.DataFrame:
    """Computes EMA 200 for higher-timeframe trend proxy."""
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    return df


def compute_supertrend(df: pd.DataFrame, period: int = 10, factor: float = 3.0) -> pd.DataFrame:
    """Computes custom Supertrend indicator for trend detection."""
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)
    trend_atr = tr.rolling(period).mean()

    hl2 = (df['high'] + df['low']) / 2
    ub_raw = (hl2 + factor * trend_atr).values
    lb_raw = (hl2 - factor * trend_atr).values
    close = df['close'].values
    n = len(df)

    upper_band = np.zeros(n)
    lower_band = np.zeros(n)
    direction = np.zeros(n)  # 1=Down, -1=Up

    upper_band[0] = ub_raw[0] if not np.isnan(ub_raw[0]) else close[0]
    lower_band[0] = lb_raw[0] if not np.isnan(lb_raw[0]) else close[0]
    direction[0] = 1

    for i in range(1, n):
        if np.isnan(ub_raw[i]) or np.isnan(lb_raw[i]):
            upper_band[i] = upper_band[i - 1]
            lower_band[i] = lower_band[i - 1]
            direction[i] = direction[i - 1]
            continue
        lower_band[i] = max(lb_raw[i], lower_band[i - 1]) if close[i - 1] > lower_band[i - 1] else lb_raw[i]
        upper_band[i] = min(ub_raw[i], upper_band[i - 1]) if close[i - 1] < upper_band[i - 1] else ub_raw[i]
        if direction[i - 1] == 1:
            direction[i] = -1 if close[i] > upper_band[i] else 1
        else:
            direction[i] = 1 if close[i] < lower_band[i] else -1

    df['st_direction'] = direction
    df['is_uptrend'] = direction == -1
    df['is_downtrend'] = direction == 1
    return df


def compute_volume_delta(df: pd.DataFrame) -> pd.DataFrame:
    """Computes volume delta (buy vs sell volume apportionment)."""
    vol_col = 'tick_volume' if 'tick_volume' in df.columns else 'volume'
    if vol_col not in df.columns:
        df['vol_delta'] = 0.0
        df['delta_bullish'] = False
        df['delta_bearish'] = False
        return df

    candle_range = df['high'] - df['low']
    mask_valid = candle_range > 0
    vol = df[vol_col]
    buy_vol = np.where(mask_valid, vol * (df['close'] - df['low']) / candle_range, vol * 0.5)
    sell_vol = np.where(mask_valid, vol * (df['high'] - df['close']) / candle_range, vol * 0.5)
    df['vol_delta'] = buy_vol - sell_vol
    df['delta_ema'] = pd.Series(df['vol_delta'].values).ewm(span=10, adjust=False).mean().values
    df['delta_bullish'] = df['vol_delta'] > 0
    df['delta_bearish'] = df['vol_delta'] < 0
    return df


def compute_obv(df: pd.DataFrame) -> pd.DataFrame:
    """On-Balance Volume — accumulative volume direction indicator.
    OBV rising = buying pressure, OBV falling = selling pressure.
    obv_bullish: True when OBV > its 20-EMA (net buying momentum).
    """
    vol_col = 'tick_volume' if 'tick_volume' in df.columns else 'volume'
    if vol_col not in df.columns:
        df['obv'] = 0.0
        df['obv_ema'] = 0.0
        df['obv_bullish'] = False
        return df

    sign = np.where(df['close'] > df['close'].shift(), 1,
                    np.where(df['close'] < df['close'].shift(), -1, 0))
    df['obv'] = (df[vol_col] * sign).cumsum()
    df['obv_ema'] = df['obv'].ewm(span=20, adjust=False).mean()
    df['obv_bullish'] = df['obv'] > df['obv_ema']
    return df


def compute_vwap(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Rolling VWAP (Volume Weighted Average Price).
    price > vwap = bullish positioning, price < vwap = bearish positioning.
    """
    vol_col = 'tick_volume' if 'tick_volume' in df.columns else 'volume'
    if vol_col not in df.columns:
        df['vwap'] = df['close']
        return df

    tp = (df['high'] + df['low'] + df['close']) / 3
    vol_sum = df[vol_col].rolling(period).sum().replace(0, np.nan)
    df['vwap'] = (tp * df[vol_col]).rolling(period).sum() / vol_sum
    return df


def compute_roc(df: pd.DataFrame, period: int = 5) -> pd.DataFrame:
    """Rate of Change — measures momentum as % price change over N bars.
    Positive ROC = price accelerating up, Negative = accelerating down.
    This is a LEADING indicator — it detects force before EMAs/MACD react.
    """
    df['roc_5'] = df['close'].pct_change(periods=period) * 100
    return df


def compute_momentum(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """MT5 Momentum oscillator (base 100): close / close[n] * 100."""
    base = df['close'].shift(period).replace(0, np.nan)
    df[f'momentum_{period}'] = (df['close'] / base) * 100.0
    return df


def compute_force_index(df: pd.DataFrame, ema_period: int = 13) -> pd.DataFrame:
    """Force Index = Price Change × Volume.
    Combines price movement with volume to measure the FORCE behind moves.
    High positive force = strong buying pressure, High negative = strong selling.
    force_ema smooths to filter noise.
    """
    vol_col = 'tick_volume' if 'tick_volume' in df.columns else 'volume'
    if vol_col not in df.columns:
        df['force_index'] = 0.0
        df['force_ema'] = 0.0
        return df

    df['force_index'] = df['close'].diff() * df[vol_col]
    df['force_ema'] = df['force_index'].ewm(span=ema_period, adjust=False).mean()
    return df


def compute_consecutive_direction(df: pd.DataFrame) -> pd.DataFrame:
    """Count consecutive bullish/bearish bars.
    consec_bull: how many green bars in a row (current streak)
    consec_bear: how many red bars in a row (current streak)
    Used by Momentum Rider to confirm sustained directional pressure.
    """
    is_bull = (df['close'] > df['open']).astype(int)
    is_bear = (df['close'] < df['open']).astype(int)

    # Vectorized consecutive count using groupby trick
    bull_groups = (is_bull != is_bull.shift()).cumsum()
    bear_groups = (is_bear != is_bear.shift()).cumsum()

    df['consec_bull'] = is_bull.groupby(bull_groups).cumsum() * is_bull
    df['consec_bear'] = is_bear.groupby(bear_groups).cumsum() * is_bear

    return df


def add_volatility_features(df: pd.DataFrame, volume_lookback: int = 10) -> pd.DataFrame:
    """Master function to enrich DataFrame with all volatility + volume + power + indicator features."""
    df = compute_atr(df)
    df = compute_range_compression(df)
    df = compute_candle_metrics(df)
    df = compute_volume_features(df, lookback=volume_lookback)
    df = compute_bull_bear_power(df)
    df = compute_ema_trend(df)
    # New indicators for ported strategies
    df = compute_rsi(df)
    df = compute_bollinger_bands(df)
    df = compute_stochastic(df)
    df = compute_macd(df)
    df = compute_adx(df)
    df = compute_ema_200(df)
    df = compute_supertrend(df)
    df = compute_volume_delta(df)
    # Volume Intelligence V2
    df = compute_obv(df)
    df = compute_vwap(df)
    # Momentum Features V3 (for Momentum Rider strategy)
    df = compute_roc(df)
    df = compute_momentum(df, period=14)
    df = compute_force_index(df)
    df = compute_consecutive_direction(df)
    return df
