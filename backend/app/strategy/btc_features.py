"""
BTC-Specific Feature Engineering Module.

Provides derived indicators tailored for Bitcoin's unique market characteristics:
    - 24/7 trading with distinct session patterns
    - High leverage market with funding rate dynamics
    - Whale-driven volume spikes
    - Extreme volatility regime shifts

These features are consumed by btc_momentum and btc_mean_reversion strategies.
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


def funding_rate_proxy(close: pd.Series, volume: pd.Series, rsi_period: int = 14) -> float:
    """
    Funding Rate Proxy — estimates market leverage bias.

    Logic: When RSI deviates far from 50 AND volume is high,
    the market is likely overleveraged in one direction.

    Returns:
        float: -1 to +1 scale
            Positive = overleveraged longs (bearish bias)
            Negative = overleveraged shorts (bullish bias)
            Near 0 = balanced
    """
    if len(close) < rsi_period + 5:
        return 0.0

    # RSI calculation
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(rsi_period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_val = float(rsi.iloc[-1])

    if pd.isna(rsi_val):
        return 0.0

    # RSI deviation from neutral (50)
    rsi_deviation = (rsi_val - 50) / 50  # -1 to +1

    # Volume weight: higher volume = more conviction
    vol_avg = float(volume.rolling(20).mean().iloc[-1])
    vol_current = float(volume.iloc[-1])
    vol_weight = min(vol_current / vol_avg, 3.0) / 3.0 if vol_avg > 0 else 0.5

    # Funding proxy = RSI deviation weighted by volume
    funding = rsi_deviation * vol_weight

    return round(float(np.clip(funding, -1.0, 1.0)), 4)


def whale_detector(volume: pd.Series, lookback: int = 10, threshold: float = 3.0) -> Dict:
    """
    Whale Detector — identifies abnormal volume spikes.

    Returns dict with:
        whale_bars: count of volume bars > threshold × avg in lookback
        whale_ratio: max volume / avg volume
        is_whale_active: bool (any whale bars detected)
    """
    if len(volume) < lookback + 20:
        return {"whale_bars": 0, "whale_ratio": 1.0, "is_whale_active": False}

    vol = volume.astype(float)
    vol_avg = float(vol.rolling(20).mean().iloc[-lookback - 1])

    if vol_avg <= 0:
        return {"whale_bars": 0, "whale_ratio": 1.0, "is_whale_active": False}

    recent = vol.iloc[-lookback:]
    whale_bars = int((recent > vol_avg * threshold).sum())
    whale_ratio = float(recent.max() / vol_avg)

    return {
        "whale_bars": whale_bars,
        "whale_ratio": round(whale_ratio, 2),
        "is_whale_active": whale_bars > 0,
    }


def exchange_volume_profile(
    candles: pd.DataFrame,
    lookback: int = 100,
) -> Dict:
    """
    Exchange Volume Profile — volume distribution across BTC sessions.

    Sessions (UTC):
        Asia:    00:00-07:59 (low volume, range-bound)
        London:  08:00-12:59 (moderate, can trend)
        NY:      13:00-20:59 (highest volume, strongest moves)
        Overlap: 13:00-16:59 (London+NY overlap = peak)

    Returns dict with session volume percentages and current session.
    """
    result = {
        "asia_pct": 25.0, "london_pct": 25.0,
        "ny_pct": 25.0, "overlap_pct": 25.0,
        "current_session": "unknown",
        "session_strength": 1.0,
    }

    if candles is None or len(candles) < lookback:
        return result

    if "time" not in candles.columns:
        return result

    recent = candles.iloc[-lookback:].copy()
    vol_col = "tick_volume" if "tick_volume" in recent.columns else "volume"
    if vol_col not in recent.columns:
        return result

    # Extract hour
    try:
        hours = pd.to_datetime(recent["time"]).dt.hour
    except Exception:
        return result

    total_vol = float(recent[vol_col].sum())
    if total_vol <= 0:
        return result

    # Session classification
    asia_mask = hours < 8
    london_mask = (hours >= 8) & (hours < 13)
    ny_mask = (hours >= 13) & (hours < 21)
    overlap_mask = (hours >= 13) & (hours < 17)

    asia_vol = float(recent.loc[asia_mask, vol_col].sum())
    london_vol = float(recent.loc[london_mask, vol_col].sum())
    ny_vol = float(recent.loc[ny_mask, vol_col].sum())
    overlap_vol = float(recent.loc[overlap_mask, vol_col].sum())

    result["asia_pct"] = round(asia_vol / total_vol * 100, 1)
    result["london_pct"] = round(london_vol / total_vol * 100, 1)
    result["ny_pct"] = round(ny_vol / total_vol * 100, 1)
    result["overlap_pct"] = round(overlap_vol / total_vol * 100, 1)

    # Current session
    current_hour = int(hours.iloc[-1])
    if current_hour < 8:
        result["current_session"] = "asia"
        result["session_strength"] = 0.6
    elif current_hour < 13:
        result["current_session"] = "london"
        result["session_strength"] = 0.8
    elif current_hour < 17:
        result["current_session"] = "overlap"
        result["session_strength"] = 1.0
    elif current_hour < 21:
        result["current_session"] = "ny"
        result["session_strength"] = 0.9
    else:
        result["current_session"] = "asia_pre"
        result["session_strength"] = 0.5

    return result


def btc_regime_strength(
    close: pd.Series,
    atr_period: int = 14,
    bb_period: int = 20,
    bb_std: float = 2.0,
) -> Dict:
    """
    BTC Regime Strength — quantifies trend vs range.

    Uses ATR% / BB width ratio to determine regime:
        0.0-0.3: Strong range (mean-reversion friendly)
        0.3-0.7: Transitional
        0.7-1.0: Strong trend (momentum friendly)

    Returns dict with regime_score, atr_pct, bb_width_pct, and regime_label.
    """
    if len(close) < max(atr_period, bb_period) + 5:
        return {"regime_score": 0.5, "atr_pct": 0, "bb_width_pct": 0, "regime_label": "unknown"}

    current_close = float(close.iloc[-1])
    if current_close <= 0:
        return {"regime_score": 0.5, "atr_pct": 0, "bb_width_pct": 0, "regime_label": "unknown"}

    # ATR%
    high = close  # Approximate with close for simplicity
    tr = close.diff().abs()
    atr = float(tr.rolling(atr_period).mean().iloc[-1])
    atr_pct = atr / current_close if current_close > 0 else 0

    # BB width%
    bb_mid = close.rolling(bb_period).mean()
    bb_std_val = close.rolling(bb_period).std()
    bb_upper = float((bb_mid + bb_std * bb_std_val).iloc[-1])
    bb_lower = float((bb_mid - bb_std * bb_std_val).iloc[-1])
    bb_width_pct = (bb_upper - bb_lower) / current_close

    # Previous BB width for expansion detection
    bb_upper_prev = float((bb_mid + bb_std * bb_std_val).iloc[-5])
    bb_lower_prev = float((bb_mid - bb_std * bb_std_val).iloc[-5])
    bb_width_prev = (bb_upper_prev - bb_lower_prev) / float(close.iloc[-5]) if float(close.iloc[-5]) > 0 else bb_width_pct

    # Regime score: high ATR% relative to BB width = trending
    # Low ATR% relative to BB width = ranging (BB wide but not moving)
    if bb_width_pct > 0:
        regime_score = min(atr_pct / bb_width_pct, 1.0) if bb_width_pct > 0 else 0.5
    else:
        regime_score = 0.5

    # BB expanding = breakout likely
    bb_expanding = bb_width_pct > bb_width_prev * 1.1

    # Label
    if regime_score < 0.3:
        label = "strong_range"
    elif regime_score < 0.5:
        label = "range"
    elif regime_score < 0.7:
        label = "transitional"
    else:
        label = "trending"

    return {
        "regime_score": round(regime_score, 3),
        "atr_pct": round(atr_pct, 5),
        "bb_width_pct": round(bb_width_pct, 5),
        "bb_expanding": bb_expanding,
        "regime_label": label,
    }


def compute_all_btc_features(candles: pd.DataFrame) -> Dict:
    """
    Compute all BTC-specific features from a candles DataFrame.

    Returns a dict with all features ready for strategy consumption.
    """
    close = candles["close"].astype(float)
    vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
    volume = candles[vol_col].astype(float) if vol_col in candles.columns else pd.Series([0] * len(candles))

    features = {}

    # 1. Funding rate proxy
    features["funding_rate_proxy"] = funding_rate_proxy(close, volume)

    # 2. Whale detection
    whale = whale_detector(volume)
    features.update({f"whale_{k}": v for k, v in whale.items()})

    # 3. Volume profile
    vol_profile = exchange_volume_profile(candles)
    features.update({f"vol_{k}": v for k, v in vol_profile.items()})

    # 4. Regime strength
    regime = btc_regime_strength(close)
    features.update({f"regime_{k}": v for k, v in regime.items()})

    return features
