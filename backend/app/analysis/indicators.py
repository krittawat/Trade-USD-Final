import pandas as pd
import numpy as np

def sma(series: pd.Series, length: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=length, min_periods=length).mean()

def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=length, adjust=False, min_periods=length).mean()

def wma(series: pd.Series, length: int) -> pd.Series:
    """Fast Weighted Moving Average using NumPy."""
    if len(series) < length:
        return pd.Series(np.nan, index=series.index)
    
    weights = np.arange(1, length + 1)
    w_sum = weights.sum()
    
    # Use numpy convolution for speed
    values = series.values
    out = np.full_like(values, np.nan)
    
    # Convolution logic for WMA
    # We use a sliding window via as_strided or just a simple loop for the convolution part if length is small,
    # but for best speed on large arrays, np.convolve is king.
    conv = np.convolve(values, weights[::-1], mode='valid') / w_sum
    out[length - 1:] = conv
    
    return pd.Series(out, index=series.index)

def hma(series: pd.Series, length: int) -> pd.Series:
    """Hull Moving Average."""
    half_length = int(length / 2)
    sqrt_length = int(np.sqrt(length))
    wmaf = wma(series, half_length) * 2 - wma(series, length)
    return wma(wmaf, sqrt_length)

def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    
    # Wilder's exponential smoothing (alpha = 1/length)
    roll_up = up.ewm(alpha=1/length, adjust=False, min_periods=length).mean()
    roll_down = down.ewm(alpha=1/length, adjust=False, min_periods=length).mean()
    
    rs = roll_up / roll_down
    return 100.0 - (100.0 / (1.0 + rs))

def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Moving Average Convergence Divergence."""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    
    return pd.DataFrame({
        f'MACD_{fast}_{slow}_{signal}': macd_line,
        f'MACDh_{fast}_{slow}_{signal}': histogram,
        f'MACDs_{fast}_{slow}_{signal}': signal_line
    })

def tr(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range."""
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Average True Range (Wilder's)."""
    true_range = tr(high, low, close)
    return true_range.ewm(alpha=1/length, adjust=False, min_periods=length).mean()

def stoch(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3, smooth_k: int = 3) -> pd.DataFrame:
    """Stochastic Oscillator."""
    lowest_low = low.rolling(window=k, min_periods=k).min()
    highest_high = high.rolling(window=k, min_periods=k).max()
    
    stoch_k_fast = 100 * (close - lowest_low) / (highest_high - lowest_low)
    
    # Smooth %K
    stoch_k = sma(stoch_k_fast, smooth_k)
    # Smooth %D
    stoch_d = sma(stoch_k, d)
    
    return pd.DataFrame({
        f'STOCHk_{k}_{d}_{smooth_k}': stoch_k,
        f'STOCHd_{k}_{d}_{smooth_k}': stoch_d
    })

def bbands(series: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands."""
    middle_band = sma(series, length)
    std_dev = series.rolling(window=length, min_periods=length).std()
    
    upper_band = middle_band + (std_dev * std)
    lower_band = middle_band - (std_dev * std)
    
    return pd.DataFrame({
        f'BBL_{length}_{std}': lower_band,
        f'BBM_{length}_{std}': middle_band,
        f'BBU_{length}_{std}': upper_band,
        f'BBB_{length}_{std}': (upper_band - lower_band) / middle_band * 100 # Bandwidth
    })

def vwma(close: pd.Series, volume: pd.Series, length: int) -> pd.Series:
    """Volume Weighted Moving Average."""
    cv = close * volume
    return cv.rolling(window=length, min_periods=length).sum() / volume.rolling(window=length, min_periods=length).sum()

def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.DataFrame:
    """Average Directional Index."""
    up_move = high - high.shift()
    down_move = low.shift() - low
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    plus_dm = pd.Series(plus_dm, index=close.index)
    minus_dm = pd.Series(minus_dm, index=close.index)
    
    true_range = tr(high, low, close)
    
    # Wilder's Smoothing
    atr_smooth = true_range.ewm(alpha=1/length, adjust=False, min_periods=length).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/length, adjust=False, min_periods=length).mean() / atr_smooth)
    minus_di = 100 * (minus_dm.ewm(alpha=1/length, adjust=False, min_periods=length).mean() / atr_smooth)
    
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
    adx_line = dx.ewm(alpha=1/length, adjust=False, min_periods=length).mean()
    
    return pd.DataFrame({
        f'ADX_{length}': adx_line,
        f'DMP_{length}': plus_di,
        f'DMN_{length}': minus_di
    })

def supertrend(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 7, multiplier: float = 3.0) -> pd.DataFrame:
    """Supertrend Indicator (Optimized NumPy)."""
    atr_val = atr(high, low, close, length).values
    close_vals = close.values
    hl2 = ((high + low) / 2).values
    
    basic_upperband = hl2 + (multiplier * atr_val)
    basic_lowerband = hl2 - (multiplier * atr_val)
    
    # Pre-allocate numpy arrays
    final_upperband = np.zeros_like(close_vals)
    final_lowerband = np.zeros_like(close_vals)
    supertrend_line = np.full_like(close_vals, np.nan)
    direction = np.zeros_like(close_vals, dtype=int) # 1 for up, -1 for down
    
    for i in range(len(close_vals)):
        if i < length or np.isnan(atr_val[i]):
            final_upperband[i] = basic_upperband[i]
            final_lowerband[i] = basic_lowerband[i]
            continue

        prev_close = close_vals[i-1]
        prev_upper = final_upperband[i-1]
        prev_lower = final_lowerband[i-1]
        
        # Upper band
        if basic_upperband[i] < prev_upper or prev_close > prev_upper:
            final_upperband[i] = basic_upperband[i]
        else:
            final_upperband[i] = prev_upper
            
        # Lower band
        if basic_lowerband[i] > prev_lower or prev_close < prev_lower:
            final_lowerband[i] = basic_lowerband[i]
        else:
            final_lowerband[i] = prev_lower
            
        # Trend / Supertrend
        prev_st = supertrend_line[i-1]
        if np.isnan(prev_st):
            if close_vals[i] >= final_lowerband[i]:
                supertrend_line[i] = final_lowerband[i]
                direction[i] = 1
            else:
                supertrend_line[i] = final_upperband[i]
                direction[i] = -1
        elif prev_st == prev_lower:
            if close_vals[i] < final_lowerband[i]:
                supertrend_line[i] = final_upperband[i]
                direction[i] = -1
            else:
                supertrend_line[i] = final_lowerband[i]
                direction[i] = 1
        else: # prev_st == prev_upper
            if close_vals[i] > final_upperband[i]:
                supertrend_line[i] = final_lowerband[i]
                direction[i] = 1
            else:
                supertrend_line[i] = final_upperband[i]
                direction[i] = -1

    dir_series = pd.Series(direction, index=close.index).map({1: 'up', -1: 'down', 0: np.nan})
    
    return pd.DataFrame({
        f'SUPERT_{length}_{multiplier}': pd.Series(supertrend_line, index=close.index),
        f'SUPERTd_{length}_{multiplier}': dir_series,
        f'SUPERTl_{length}_{multiplier}': pd.Series(final_lowerband, index=close.index),
        f'SUPERTu_{length}_{multiplier}': pd.Series(final_upperband, index=close.index),
    })

def cmo(series: pd.Series, length: int = 14) -> pd.Series:
    """Chande Momentum Oscillator."""
    diff = series.diff()
    up = diff.clip(lower=0)
    down = -1 * diff.clip(upper=0)
    
    sum_up = up.rolling(window=length, min_periods=length).sum()
    sum_down = down.rolling(window=length, min_periods=length).sum()
    
    return 100 * ((sum_up - sum_down) / (sum_up + sum_down))

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On Balance Volume."""
    close_diff = close.diff()
    direction = np.sign(close_diff)
    obv_val = direction * volume
    # Initialize first valid value as volume value
    first_valid = close_diff.first_valid_index()
    if first_valid is not None:
         obv_val.loc[first_valid] = volume.loc[first_valid]
    return obv_val.cumsum()

def heiken_ashi(open_s: pd.Series, high_s: pd.Series, low_s: pd.Series, close_s: pd.Series) -> pd.DataFrame:
    """Heiken Ashi Candles (Optimized NumPy)."""
    open_vals = open_s.values
    high_vals = high_s.values
    low_vals = low_s.values
    close_vals = close_s.values
    
    ha_close = (open_vals + high_vals + low_vals + close_vals) / 4
    ha_open = np.zeros_like(open_vals)
    
    if len(open_vals) > 0:
        ha_open[0] = (open_vals[0] + close_vals[0]) / 2
        
        # This one is still a loop because it's recursive, but on numpy arrays it's faster
        for i in range(1, len(open_vals)):
            ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2
            
    ha_high = np.max([high_vals, ha_open, ha_close], axis=0)
    ha_low = np.min([low_vals, ha_open, ha_close], axis=0)
    
    return pd.DataFrame({
        'HA_Open': pd.Series(ha_open, index=open_s.index),
        'HA_High': pd.Series(ha_high, index=open_s.index),
        'HA_Low': pd.Series(ha_low, index=open_s.index),
        'HA_Close': pd.Series(ha_close, index=open_s.index)
    })

def qqe_mod(series: pd.Series, rsi_period: int = 6, rsi_smoothing: int = 5, fast_factor: float = 3.0, thresh: float = 3.0) -> pd.DataFrame:
    """QQE MOD (Optimized NumPy)."""
    # 1. calculate smooth RSI
    rs = rsi(series, rsi_period)
    rsi_ma = ema(rs, rsi_smoothing)
    rsi_ma_vals = rsi_ma.values
    
    # 2. calculate fast ATR of RSI
    atr_rsi = abs(rsi_ma.shift(1) - rsi_ma)
    wilders_period = rsi_period * 2 - 1
    ma_atr_rsi = ema(atr_rsi, wilders_period)
    dar = ema(ma_atr_rsi, wilders_period) * fast_factor
    dar_vals = dar.values
    
    new_shortband = rsi_ma_vals + dar_vals
    new_longband = rsi_ma_vals - dar_vals
    
    longband = np.zeros_like(rsi_ma_vals)
    shortband = np.zeros_like(rsi_ma_vals)
    trend = np.ones_like(rsi_ma_vals, dtype=int)
    
    for i in range(1, len(rsi_ma_vals)):
        prev_rsi = rsi_ma_vals[i-1]
        curr_rsi = rsi_ma_vals[i]
        prev_long = longband[i-1]
        prev_short = shortband[i-1]
        
        # Longband
        if prev_rsi > prev_long and curr_rsi > prev_long:
            longband[i] = max(prev_long, new_longband[i])
        else:
            longband[i] = new_longband[i]
            
        # Shortband
        if prev_rsi < prev_short and curr_rsi < prev_short:
            shortband[i] = min(prev_short, new_shortband[i])
        else:
            shortband[i] = new_shortband[i]
            
        # Trend crossover
        # Note: cross logic relies on bands from 1 and 2 bars ago
        cross_long = (longband[i-1] < curr_rsi and longband[i-2] >= prev_rsi) if i > 1 else False
        cross_short = (curr_rsi < shortband[i-1] and prev_rsi >= shortband[i-2]) if i > 1 else False
        
        if cross_short:
            trend[i] = -1
        elif cross_long:
            trend[i] = 1
        else:
            trend[i] = trend[i-1]
            
    fast_atr_rsi_tl = np.where(trend == 1, longband, shortband)
    fast_atr_rsi_tl_series = pd.Series(fast_atr_rsi_tl, index=series.index)
    
    # Bollinger Bands on QQE
    basis = sma(fast_atr_rsi_tl_series - 50, 50)
    dev = 0.35 * (fast_atr_rsi_tl_series - 50).rolling(window=50).std()
    upper = basis + dev
    lower = basis - dev
    
    # Zero cross and Green/Red bars logic
    greenbar = (rsi_ma - 50 > upper)
    redbar = (rsi_ma - 50 < lower)

    return pd.DataFrame({
        'QQE_Line': fast_atr_rsi_tl,
        'RSI_MA': rsi_ma,
        'QQE_Up': greenbar,
        'QQE_Down': redbar,
        'QQE_Trend': trend
    })

def wae(close: pd.Series, fast: int = 20, slow: int = 40, channel: int = 20, mult: float = 2.0, sensitivity: int = 150) -> pd.DataFrame:
    """Waddah Attar Explosion."""
    macd_val = ema(close, fast) - ema(close, slow)
    macd_prev = macd_val.shift(1)
    
    t1 = (macd_val - macd_prev) * sensitivity
    
    # BB on Close
    bb_upper = bbands(close, channel, mult)[f'BBU_{channel}_{mult}']
    bb_lower = bbands(close, channel, mult)[f'BBL_{channel}_{mult}']
    e1 = bb_upper - bb_lower
    
    trend_up = np.where(t1 >= 0, t1, 0.0)
    trend_down = np.where(t1 < 0, -1 * t1, 0.0)
    
    wae_buy = (trend_up > 0) & (trend_up > e1)
    wae_sell = (trend_down > 0) & (trend_down > e1)
    
    return pd.DataFrame({
        'WAE_TrendUp': trend_up,
        'WAE_TrendDown': trend_down,
        'WAE_ExplosionLine': e1,
        'WAE_Buy': wae_buy,
        'WAE_Sell': wae_sell
    })

def smc_pivots(high: pd.Series, low: pd.Series, length: int = 5) -> pd.DataFrame:
    """Detects Swing Highs and Lows for Smart Money Concepts (Vectorized)."""
    
    is_ph = pd.Series(True, index=high.index)
    is_pl = pd.Series(True, index=low.index)
    
    for j in range(1, length + 1):
        is_ph = is_ph & (high > high.shift(j)) & (high > high.shift(-j))
        is_pl = is_pl & (low < low.shift(j)) & (low < low.shift(-j))
        
    ph = pd.Series(np.nan, index=high.index)
    pl = pd.Series(np.nan, index=low.index)
    
    ph[is_ph] = high[is_ph]
    pl[is_pl] = low[is_pl]
            
    return pd.DataFrame({'PivotHigh': ph, 'PivotLow': pl})

def bulls_power(high: pd.Series, close: pd.Series, length: int = 13) -> pd.Series:
    """Bulls Power Indicator."""
    return high - ema(close, length)

def bears_power(low: pd.Series, close: pd.Series, length: int = 13) -> pd.Series:
    """Bears Power Indicator."""
    return low - ema(close, length)

def momentum(series: pd.Series, length: int = 14) -> pd.Series:
    """Momentum Indicator (MT5 style: (Close / Close[n]) * 100)."""
    return (series / series.shift(length)) * 100.0

def psar(high_s: pd.Series, low_s: pd.Series, af_start: float = 0.02, af_inc: float = 0.02, af_max: float = 0.2) -> pd.Series:
    """
    Parabolic SAR Indicator (Optimized Manual Calculation).
    """
    high = high_s.values
    low = low_s.values
    psar_vals = np.zeros_like(high)
    bull = True
    af = af_start
    ep = high[0]
    psar_vals[0] = low[0]

    for i in range(1, len(high)):
        prev_psar = psar_vals[i-1]
        if bull:
            psar_vals[i] = prev_psar + af * (ep - prev_psar)
            psar_vals[i] = min(psar_vals[i], low[i-1], low[max(0, i-2)])
            if low[i] < psar_vals[i]:
                bull = False
                psar_vals[i] = ep
                af = af_start
                ep = low[i]
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + af_inc, af_max)
        else:
            psar_vals[i] = prev_psar + af * (ep - prev_psar)
            psar_vals[i] = max(psar_vals[i], high[i-1], high[max(0, i-2)])
            if high[i] > psar_vals[i]:
                bull = True
                psar_vals[i] = ep
                af = af_start
                ep = high[i]
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + af_inc, af_max)
                    
    return pd.Series(psar_vals, index=high_s.index)

def ehma(series: pd.Series, length: int) -> pd.Series:
    """Exponential Hull Moving Average."""
    half_length = int(length / 2)
    sqrt_length = int(np.sqrt(length))
    # EHMA uses EMA instead of WMA
    emaf = ema(series, half_length) * 2 - ema(series, length)
    return ema(emaf, sqrt_length)

def thma(series: pd.Series, length: int) -> pd.Series:
    """Triple Hull Moving Average."""
    # thma = wma(wma(src, length / 3) * 3 - wma(src, length / 2) - wma(src, length), length)
    len3 = int(length / 3)
    len2 = int(length / 2)
    wma_f = wma(series, len3) * 3 - wma(series, len2) - wma(series, length)
    return wma(wma_f, length)

def ut_bot_trail(close: pd.Series, high: pd.Series, low: pd.Series, key_value: float = 2.0, atr_period: int = 10) -> pd.Series:
    """
    UT Bot ATR-based Trailing Stop.
    Iterative calculation (state-dependent).
    """
    atr_val = atr(high, low, close, atr_period).values
    close_vals = close.values
    trail_stop = np.zeros_like(close_vals)
    
    # Initialize first valid stop
    first_idx = np.where(~np.isnan(atr_val))[0]
    if len(first_idx) == 0:
        return pd.Series(np.nan, index=close.index)
        
    idx = first_idx[0]
    n_loss = key_value * atr_val[idx]
    trail_stop[idx] = close_vals[idx] - n_loss
    
    for i in range(idx + 1, len(close_vals)):
        n_loss = key_value * atr_val[i]
        prev_stop = trail_stop[i-1]
        curr_price = close_vals[i]
        prev_price = close_vals[i-1]
        
        if curr_price > prev_stop and prev_price > prev_stop:
            trail_stop[i] = max(prev_stop, curr_price - n_loss)
        elif curr_price < prev_stop and prev_price < prev_stop:
            trail_stop[i] = min(prev_stop, curr_price + n_loss)
        elif curr_price > prev_stop:
            trail_stop[i] = curr_price - n_loss
        else:
            trail_stop[i] = curr_price + n_loss
            
    return pd.Series(trail_stop, index=close.index)

def detect_three_bar_reversal(open_s: pd.Series, high_s: pd.Series, low_s: pd.Series, close_s: pd.Series) -> pd.DataFrame:
    """
    Detects Three-Bar Reversal patterns.
    Returns DataFrame with 'bullish_3br' and 'bearish_3br' boolean columns.
    """
    # Vectorized check
    # Pine Bullish: (close[2] < open[2]) and (low[1] < low[2]) and (high[1] < high[2]) and (close[1] < open[1]) and (close > open) and (high > high[2])
    
    c = close_s.values
    o = open_s.values
    h = high_s.values
    l = low_s.values
    
    bullish = (c[:-2] < o[:-2]) & \
              (l[1:-1] < l[:-2]) & (h[1:-1] < h[:-2]) & (c[1:-1] < o[1:-1]) & \
              (c[2:] > o[2:]) & (h[2:] > h[:-2])
              
    bearish = (c[:-2] > o[:-2]) & \
              (h[1:-1] > h[:-2]) & (l[1:-1] > l[:-2]) & (c[1:-1] > o[1:-1]) & \
              (c[2:] < o[2:]) & (l[2:] < l[:-2])
              
    # Pad results to match original length
    bull_res = np.concatenate([[False, False], bullish])
    bear_res = np.concatenate([[False, False], bearish])
    
    return pd.DataFrame({
        'bullish_3br': bull_res,
        'bearish_3br': bear_res
    }, index=close_s.index)

def lux_order_blocks(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, length: int = 5) -> pd.DataFrame:
    """
    LuxAlgo Style Order Blocks.
    Identifies zones based on volume pivots and price action state.
    Confirmed 'length' bars after the pivot.
    """
    hl2 = (high + low) / 2
    high_vals = high.values
    low_vals = low.values
    vol_vals = volume.values
    hl2_vals = hl2.values
    
    # ta.highest/lowest(length)
    upper = high.rolling(window=length).max().values
    lower = low.rolling(window=length).min().values
    
    # State tracking
    os = np.zeros(len(high))
    for i in range(length, len(high)):
        if high_vals[i-length] > upper[i-length-1]:
            os[i] = 0
        elif low_vals[i-length] < lower[i-length-1]:
            os[i] = 1
        else:
            os[i] = os[i-1]
            
    # Volume Pivots detection (manual loop for exact match)
    bull_ob_top = np.full_like(high_vals, np.nan)
    bull_ob_btm = np.full_like(high_vals, np.nan)
    bear_ob_top = np.full_like(high_vals, np.nan)
    bear_ob_btm = np.full_like(high_vals, np.nan)
    
    for i in range(2 * length, len(vol_vals)):
        v_mid = vol_vals[i - length]
        
        # Check if v_mid is higher than surrounding volume
        is_pivot = True
        for j in range(1, length + 1):
            if v_mid < vol_vals[i - length - j] or v_mid < vol_vals[i - length + j]:
                is_pivot = False
                break
                
        if is_pivot:
            # Pivot detected at i - length, confirmed at i
            if os[i] == 1:
                bull_ob_top[i] = hl2_vals[i-length]
                bull_ob_btm[i] = low_vals[i-length]
            elif os[i] == 0:
                bear_ob_top[i] = high_vals[i-length]
                bear_ob_btm[i] = hl2_vals[i-length]
                
    return pd.DataFrame({
        'bull_ob_top': bull_ob_top,
        'bull_ob_btm': bull_ob_btm,
        'bear_ob_top': bear_ob_top,
        'bear_ob_btm': bear_ob_btm
    }, index=high.index)

def vmc_wavetrend(src: pd.Series, chlen: int = 9, avg: int = 12, malen: int = 3) -> pd.DataFrame:
    """
    VuManChu WaveTrend (Market Cipher B).
    Returns wt1, wt2, and vwap.
    """
    esa = ema(src, chlen)
    de = ema((src - esa).abs(), chlen)
    ci = (src - esa) / (0.015 * de)
    wt1 = ema(ci, avg)
    wt2 = sma(wt1, malen)
    vwap = wt1 - wt2
    return pd.DataFrame({'wt1': wt1, 'wt2': wt2, 'vwap': vwap}, index=src.index)

def vmc_mfi_rsi(open_s: pd.Series, high_s: pd.Series, low_s: pd.Series, close_s: pd.Series, period: int = 60, multiplier: float = 150.0) -> pd.Series:
    """VuManChu Money Flow Index + RSI blend."""
    # ((close - open) / (high - low)) * multiplier
    val = ((close_s - open_s) / (high_s - low_s).replace(0, 1e-9)) * multiplier
    return sma(val, period)

def schaff_trend_cycle(src: pd.Series, length: int = 10, fast: int = 23, slow: int = 50, factor: float = 0.5) -> pd.Series:
    """Schaff Trend Cycle (STC)."""
    ema1 = ema(src, fast)
    ema2 = ema(src, slow)
    macd_val = ema1 - ema2
    
    alpha = macd_val.rolling(window=length).min()
    beta = macd_val.rolling(window=length).max() - alpha
    gamma = (macd_val - alpha) / beta.replace(0, 1e-9) * 100
    
    # First smoothing
    delta = gamma.copy()
    gamma_vals = gamma.values
    delta_vals = delta.values
    for i in range(1, len(gamma)):
        if np.isnan(delta_vals[i-1]):
            continue
        delta_vals[i] = delta_vals[i-1] + factor * (gamma_vals[i] - delta_vals[i-1])
    delta = pd.Series(delta_vals, index=gamma.index)
        
    epsilon = delta.rolling(window=length).min()
    zeta = delta.rolling(window=length).max() - epsilon
    eta = (delta - epsilon) / zeta.replace(0, 1e-9) * 100
    
    # Second smoothing
    stc = eta.copy()
    eta_vals = eta.values
    stc_vals = stc.values
    for i in range(1, len(eta)):
        if np.isnan(stc_vals[i-1]):
            continue
        stc_vals[i] = stc_vals[i-1] + factor * (eta_vals[i] - stc_vals[i-1])
        
    return pd.Series(stc_vals, index=eta.index)

def detect_divergences(src: pd.Series, high: pd.Series, low: pd.Series, top_limit: float = 45, bot_limit: float = -65) -> pd.DataFrame:
    """
    Detects Regular and Hidden Divergences.
    Uses fractal tops/bottoms.
    """
    def is_top(s, i):
        return s.iloc[i-4] < s.iloc[i-2] and s.iloc[i-3] < s.iloc[i-2] and \
               s.iloc[i-2] > s.iloc[i-1] and s.iloc[i-2] > s.iloc[i]
               
    def is_bot(s, i):
        return s.iloc[i-4] > s.iloc[i-2] and s.iloc[i-3] > s.iloc[i-2] and \
               s.iloc[i-2] < s.iloc[i-1] and s.iloc[i-2] < s.iloc[i]

    bear_div = np.zeros(len(src), dtype=bool)
    bull_div = np.zeros(len(src), dtype=bool)
    
    # Simplified tracking for efficiency
    last_top_src = np.nan
    last_top_price = np.nan
    last_bot_src = np.nan
    last_bot_price = np.nan
    
    vals = src.values
    highs = high.values
    lows = low.values
    
    for i in range(4, len(src)):
        # Check Top Fractal
        if vals[i-4] < vals[i-2] and vals[i-3] < vals[i-2] and vals[i-2] > vals[i-1] and vals[i-2] > vals[i]:
            if vals[i-2] >= top_limit:
                if not np.isnan(last_top_src):
                    if highs[i-2] > last_top_price and vals[i-2] < last_top_src:
                        bear_div[i] = True
                last_top_src = vals[i-2]
                last_top_price = highs[i-2]
                
        # Check Bot Fractal
        if vals[i-4] > vals[i-2] and vals[i-3] > vals[i-2] and vals[i-2] < vals[i-1] and vals[i-2] < vals[i]:
            if vals[i-2] <= bot_limit:
                if not np.isnan(last_bot_src):
                    if lows[i-2] < last_bot_price and vals[i-2] > last_bot_src:
                        bull_div[i] = True
                last_bot_src = vals[i-2]
                last_bot_price = lows[i-2]
                
    return pd.DataFrame({'bear_div': bear_div, 'bull_div': bull_div}, index=src.index)

