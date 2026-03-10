"""
Regime Classifier v2 — Market Regime Intelligence Core.

จำแนกสภาวะตลาด 8 ประเภท ด้วย multi-indicator scoring:
    1. STRONG_TREND      — ADX>25, EMA50≠EMA200, ATR ขยาย
    2. WEAK_TREND         — ADX 15-25
    3. RANGING            — ADX<15, BB width แคบ
    4. BREAKOUT           — Volume spike + ทะลุ BB + ATR ขยาย
    5. FAKEOUT            — ทะลุแล้ว volume ลด + ราคากลับ
    6. HIGH_VOLATILITY    — ATR percentile > 80%
    7. LOW_VOLATILITY     — ATR percentile < 20%
    8. LIQUIDITY_SWEEP    — wick ยาวซ้ำๆ (Smart Money Trap)

Confidence Score (0-1):
    ถ่วงน้ำหนักจาก ADX, ATR consistency, BB position, volume

Backward Compat:
    - ยังตั้ง TRENDING_UP / TRENDING_DOWN ตาม EMA slope
    - Return RegimeContext (เดิม) + ค่า confidence ใหม่
"""

import numpy as np
import pandas as pd
from typing import Optional

from app.core.logging import get_logger
from app.domain.enums import RegimeType
from app.domain.models import RegimeContext

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Parameters
# ═══════════════════════════════════════════════════════════════════
ADX_PERIOD = 14
ADX_STRONG = 25          # ADX > 25 = strong trend
ADX_WEAK_MIN = 15        # 15 < ADX < 25 = weak trend
ADX_RANGING = 15          # ADX < 15 = ranging

ATR_PERIOD = 14
ATR_PERCENTILE_HIGH = 80  # ATR > 80th percentile = high volatility
ATR_PERCENTILE_LOW = 20   # ATR < 20th percentile = low volatility
ATR_LOOKBACK = 100        # bars for percentile calculation

EMA_FAST = 50
EMA_SLOW = 200
EMA_SLOPE_PERIOD = 10

BB_PERIOD = 20
BB_STD = 2
BB_NARROW_PERCENTILE = 25  # BB width < 25th percentile = narrow

RSI_PERIOD = 14

VOLUME_SPIKE_MULT = 2.0   # volume > 2x average = spike
WICK_RATIO_TRAP = 0.6     # wick > 60% of range = trap
WICK_LOOKBACK = 5


# ═══════════════════════════════════════════════════════════════════
# Helper: compute indicators (pure numpy, no pandas_ta dependency)
# ═══════════════════════════════════════════════════════════════════

def _wilder_smooth(data: np.ndarray, period: int) -> np.ndarray:
    """Wilder EMA smoothing."""
    out = np.full_like(data, np.nan)
    if len(data) < period:
        return out
    out[period - 1] = np.mean(data[:period])
    alpha = 1.0 / period
    for i in range(period, len(data)):
        out[i] = out[i - 1] * (1 - alpha) + data[i] * alpha
    return out


def _compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                 period: int = 14) -> tuple[float, float, float]:
    """
    Compute ADX, +DI, -DI.
    Returns: (adx_last, plus_di_last, minus_di_last)
    """
    n = len(close)
    if n < period + 1:
        return 0.0, 0.0, 0.0

    up_move = np.diff(high)
    down_move = -np.diff(low)

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))

    atr_smooth = _wilder_smooth(tr, period)
    plus_dm_smooth = _wilder_smooth(plus_dm, period)
    minus_dm_smooth = _wilder_smooth(minus_dm, period)

    # Avoid division by zero
    safe_atr = np.where(atr_smooth > 0, atr_smooth, 1e-9)
    plus_di = 100.0 * plus_dm_smooth / safe_atr
    minus_di = 100.0 * minus_dm_smooth / safe_atr

    di_sum = plus_di + minus_di
    safe_sum = np.where(di_sum > 0, di_sum, 1e-9)
    dx = 100.0 * np.abs(plus_di - minus_di) / safe_sum

    adx = _wilder_smooth(dx[~np.isnan(dx)], period)

    last_adx = float(adx[-1]) if len(adx) > 0 and not np.isnan(adx[-1]) else 0.0
    last_pdi = float(plus_di[-1]) if not np.isnan(plus_di[-1]) else 0.0
    last_mdi = float(minus_di[-1]) if not np.isnan(minus_di[-1]) else 0.0

    return last_adx, last_pdi, last_mdi


def _compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                 period: int = 14) -> np.ndarray:
    """Compute ATR series."""
    if len(close) < 2:
        return np.array([0.0])
    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    return _wilder_smooth(tr, period)


def _compute_ema(data: np.ndarray, period: int) -> np.ndarray:
    """Compute EMA series."""
    out = np.full_like(data, np.nan, dtype=float)
    if len(data) < period:
        return out
    out[period - 1] = np.mean(data[:period])
    alpha = 2.0 / (period + 1)
    for i in range(period, len(data)):
        out[i] = out[i - 1] * (1 - alpha) + data[i] * alpha
    return out


def _compute_bollinger(close: np.ndarray, period: int = 20,
                       std_mult: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute Bollinger Bands: upper, middle, lower."""
    n = len(close)
    upper = np.full(n, np.nan)
    middle = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period - 1, n):
        window = close[i - period + 1:i + 1]
        m = np.mean(window)
        s = np.std(window, ddof=0)
        middle[i] = m
        upper[i] = m + std_mult * s
        lower[i] = m - std_mult * s
    return upper, middle, lower


def _compute_rsi(close: np.ndarray, period: int = 14) -> float:
    """Compute last RSI value."""
    if len(close) < period + 1:
        return 50.0
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = _wilder_smooth(gains, period)
    avg_loss = _wilder_smooth(losses, period)

    last_g = avg_gain[-1] if not np.isnan(avg_gain[-1]) else 0.0
    last_l = avg_loss[-1] if not np.isnan(avg_loss[-1]) else 0.0

    if last_l == 0:
        return 100.0 if last_g > 0 else 50.0
    rs = last_g / last_l
    return float(100.0 - 100.0 / (1.0 + rs))


# ═══════════════════════════════════════════════════════════════════
# Main: classify_regime — 8-regime classifier with confidence
# ═══════════════════════════════════════════════════════════════════

def classify_regime(
    candles: pd.DataFrame,
    profile=None,
) -> RegimeContext:
    """
    จำแนกสภาวะตลาด 8 ประเภท + confidence score.

    Args:
        candles: DataFrame with [open, high, low, close, tick_volume(optional)]
        profile: PersonalityProfile (optional, for dynamic thresholds)

    Returns:
        RegimeContext: regime, actionable, score (confidence), reason, details
    """
    min_bars = max(ATR_LOOKBACK, EMA_SLOW + 10, 60)
    if candles is None or len(candles) < min_bars:
        return RegimeContext(
            regime=RegimeType.UNKNOWN,
            actionable=True,
            score=0.0,
            reason="Insufficient data",
        )

    # ─── 1. Data Prep ───
    try:
        high = candles["high"].values.astype(float)
        low = candles["low"].values.astype(float)
        close = candles["close"].values.astype(float)
        open_ = candles["open"].values.astype(float)
        volume = (candles["tick_volume"].values.astype(float)
                  if "tick_volume" in candles.columns
                  else np.ones(len(close)))
    except Exception:
        return RegimeContext(regime=RegimeType.UNKNOWN, actionable=False,
                            reason="Invalid data")

    details: dict = {}
    reasons: list[str] = []
    n = len(close)

    # ─── 2. Compute Indicators ───

    # ADX
    adx_val, plus_di, minus_di = _compute_adx(high, low, close, ADX_PERIOD)
    details["adx"] = round(adx_val, 2)
    details["plus_di"] = round(plus_di, 2)
    details["minus_di"] = round(minus_di, 2)

    # ATR + percentile
    atr_series = _compute_atr(high, low, close, ATR_PERIOD)
    valid_atr = atr_series[~np.isnan(atr_series)]
    current_atr = float(valid_atr[-1]) if len(valid_atr) > 0 else 0.0
    atr_lookback = valid_atr[-ATR_LOOKBACK:] if len(valid_atr) >= ATR_LOOKBACK else valid_atr
    atr_percentile = (float(np.sum(atr_lookback < current_atr) / len(atr_lookback) * 100)
                      if len(atr_lookback) > 0 else 50.0)
    atr_expanding = False
    if len(valid_atr) >= 5:
        atr_expanding = float(valid_atr[-1]) > float(valid_atr[-5])
    details["atr"] = round(current_atr, 4)
    details["atr_percentile"] = round(atr_percentile, 1)
    details["atr_expanding"] = atr_expanding

    # EMA 50 / 200
    ema50 = _compute_ema(close, EMA_FAST)
    ema200 = _compute_ema(close, EMA_SLOW)
    ema50_last = float(ema50[-1]) if not np.isnan(ema50[-1]) else close[-1]
    ema200_last = float(ema200[-1]) if not np.isnan(ema200[-1]) else close[-1]
    ema_bullish = ema50_last > ema200_last
    # Slope for direction
    ema_slope = 0.0
    if len(ema50) >= EMA_SLOPE_PERIOD and not np.isnan(ema50[-EMA_SLOPE_PERIOD]):
        ema_slope = ema50_last - float(ema50[-EMA_SLOPE_PERIOD])
    details["ema50"] = round(ema50_last, 2)
    details["ema200"] = round(ema200_last, 2)
    details["ema_bullish"] = ema_bullish
    details["ema_slope"] = round(ema_slope, 4)

    # Bollinger Bands
    bb_upper, bb_mid, bb_lower = _compute_bollinger(close, BB_PERIOD, BB_STD)
    bb_width = np.nan_to_num(bb_upper - bb_lower, nan=0.0)
    valid_bbw = bb_width[~np.isnan(bb_width)]
    current_bbw = float(valid_bbw[-1]) if len(valid_bbw) > 0 else 0.0
    bbw_percentile = (float(np.sum(valid_bbw < current_bbw) / len(valid_bbw) * 100)
                      if len(valid_bbw) > 0 else 50.0)
    bb_upper_last = float(bb_upper[-1]) if not np.isnan(bb_upper[-1]) else close[-1] + 1
    bb_lower_last = float(bb_lower[-1]) if not np.isnan(bb_lower[-1]) else close[-1] - 1
    close_above_bb = close[-1] > bb_upper_last
    close_below_bb = close[-1] < bb_lower_last
    details["bb_width"] = round(current_bbw, 4)
    details["bb_width_percentile"] = round(bbw_percentile, 1)

    # RSI
    rsi_val = _compute_rsi(close, RSI_PERIOD)
    details["rsi"] = round(rsi_val, 2)

    # Volume spike
    vol_avg = np.mean(volume[-20:]) if len(volume) >= 20 else np.mean(volume)
    vol_current = float(volume[-1])
    vol_spike = vol_current > (vol_avg * VOLUME_SPIKE_MULT) if vol_avg > 0 else False
    vol_dropping = False
    if len(volume) >= 3 and vol_avg > 0:
        vol_dropping = float(volume[-1]) < float(volume[-3]) * 0.6
    details["volume_spike"] = vol_spike
    details["volume_ratio"] = round(vol_current / vol_avg, 2) if vol_avg > 0 else 1.0

    # Wick ratio (fakeout/trap detection)
    range_len = high[-WICK_LOOKBACK:] - low[-WICK_LOOKBACK:]
    body_len = np.abs(close[-WICK_LOOKBACK:] - open_[-WICK_LOOKBACK:])
    safe_range = np.where(range_len <= 0, 1e-9, range_len)
    wick_ratio_arr = (range_len - body_len) / safe_range
    wick_ratio = float(np.mean(np.nan_to_num(wick_ratio_arr, nan=0.0)))
    details["wick_ratio"] = round(wick_ratio, 3)

    # Candle breakout check (close outside recent range)
    if n >= 30:
        recent_high = float(np.max(high[-30:-1]))
        recent_low = float(np.min(low[-30:-1]))
        candle_breakout_up = close[-1] > recent_high
        candle_breakout_down = close[-1] < recent_low
    else:
        candle_breakout_up = False
        candle_breakout_down = False
    candle_breakout = candle_breakout_up or candle_breakout_down

    # ─── 3. Regime Classification (priority ordering) ───
    regime = RegimeType.UNKNOWN
    confidence = 0.5
    actionable = True

    # --- 3.1 Smart Money Trap / Liquidity Sweep ---
    if wick_ratio > WICK_RATIO_TRAP and adx_val < ADX_STRONG:
        regime = RegimeType.LIQUIDITY_SWEEP
        confidence = min(1.0, 0.5 + (wick_ratio - 0.5) * 1.5)
        actionable = False
        reasons.append(f"Liquidity Sweep: wick_ratio={wick_ratio:.2f}")

    # --- 3.2 Fakeout ---
    elif (candle_breakout and vol_dropping and wick_ratio > 0.45):
        regime = RegimeType.FAKEOUT
        confidence = min(1.0, 0.5 + wick_ratio * 0.5)
        actionable = False
        reasons.append(f"Fakeout: breakout+vol_drop+wick={wick_ratio:.2f}")

    # --- 3.3 High Volatility ---
    elif atr_percentile > ATR_PERCENTILE_HIGH:
        regime = RegimeType.HIGH_VOLATILITY
        confidence = min(1.0, atr_percentile / 100.0)
        # Actionable but risky — strategy decides
        actionable = True
        reasons.append(f"High Volatility: ATR pct={atr_percentile:.0f}%")

    # --- 3.4 Low Volatility ---
    elif atr_percentile < ATR_PERCENTILE_LOW:
        regime = RegimeType.LOW_VOLATILITY
        confidence = min(1.0, 1.0 - atr_percentile / 100.0)
        actionable = False
        reasons.append(f"Low Volatility: ATR pct={atr_percentile:.0f}%")

    # --- 3.5 Breakout ---
    elif (candle_breakout and vol_spike and atr_expanding
          and (close_above_bb or close_below_bb)):
        regime = RegimeType.BREAKOUT
        conf_factors = [0.7]
        if adx_val > 20:
            conf_factors.append(0.15)
        if abs(ema_slope) > current_atr * 0.5:
            conf_factors.append(0.15)
        confidence = min(1.0, sum(conf_factors))
        actionable = True
        reasons.append(f"Breakout: vol_spike+BB_break+ATR_expand")

    # --- 3.6 Strong Trend ---
    elif adx_val > ADX_STRONG and atr_expanding:
        regime = RegimeType.STRONG_TREND
        confidence = min(1.0, 0.5 + (adx_val - ADX_STRONG) / 50.0 + 0.1)
        actionable = True
        reasons.append(f"Strong Trend: ADX={adx_val:.1f}, ATR expanding")

    # --- 3.7 Weak Trend ---
    elif adx_val >= ADX_WEAK_MIN:
        regime = RegimeType.WEAK_TREND
        confidence = 0.3 + (adx_val - ADX_WEAK_MIN) / (ADX_STRONG - ADX_WEAK_MIN) * 0.3
        actionable = True
        reasons.append(f"Weak Trend: ADX={adx_val:.1f}")

    # --- 3.8 Ranging ---
    elif adx_val < ADX_RANGING and bbw_percentile < BB_NARROW_PERCENTILE:
        regime = RegimeType.RANGING
        confidence = min(1.0, 0.6 + (1.0 - adx_val / ADX_RANGING) * 0.3)
        actionable = False
        reasons.append(f"Range: ADX={adx_val:.1f}, BB_width pct={bbw_percentile:.0f}%")

    # --- 3.9 Accumulation / Distribution ---
    elif (adx_val < ADX_RANGING and wick_ratio > 0.4
          and not vol_spike and bbw_percentile < 50):
        regime = RegimeType.ACCUMULATION
        confidence = 0.4 + wick_ratio * 0.3
        actionable = False
        reasons.append(f"Accumulation: low ADX + moderate wicks + tight BB")

    # --- 3.10 Fallback: classify by ADX/slope ---
    else:
        if adx_val >= ADX_WEAK_MIN:
            regime = RegimeType.WEAK_TREND
            confidence = 0.3
        else:
            regime = RegimeType.RANGING
            actionable = False
            confidence = 0.3
        reasons.append(f"Fallback: ADX={adx_val:.1f}")

    # ─── 4. Direction (backward compat) ───
    if ema_slope > 0 and plus_di > minus_di:
        direction = RegimeType.TRENDING_UP
    elif ema_slope < 0 and minus_di > plus_di:
        direction = RegimeType.TRENDING_DOWN
    else:
        direction = RegimeType.UNKNOWN
    details["direction"] = direction.value

    # For backward-compat: if regime is STRONG/WEAK_TREND,
    # also return the directional type for strategy registry matching
    compat_regime = regime
    if regime in (RegimeType.STRONG_TREND, RegimeType.WEAK_TREND, RegimeType.BREAKOUT):
        compat_regime = direction if direction != RegimeType.UNKNOWN else RegimeType.TRENDING_UP
    details["primary_regime"] = regime.value

    # ─── 5. Dynamic thresholds from personality ───
    if profile:
        fakeout_prob = getattr(profile, 'fakeout_probability', 0.0)
        if fakeout_prob > 0.5 and regime == RegimeType.BREAKOUT:
            # High fakeout symbol → downgrade confidence
            confidence *= 0.7
            reasons.append(f"Personality fakeout_prob={fakeout_prob:.2f} → conf reduced")

    # ─── 6. Build result ───
    final_reason = "; ".join(reasons) if reasons else "Market classified"
    confidence = max(0.0, min(1.0, confidence))

    logger.debug("regime_intelligence", extra={
        "regime": regime.value,
        "direction": direction.value,
        "compat_regime": compat_regime.value,
        "confidence": round(confidence, 3),
        "actionable": actionable,
        "reasons": final_reason,
    })

    return RegimeContext(
        regime=compat_regime,  # backward compat: directional type for existing strategies
        actionable=actionable,
        score=round(confidence, 3),
        reason=final_reason,
        details={
            **details,
            "primary_regime": regime.value,
            "confidence": round(confidence, 3),
        },
    )

