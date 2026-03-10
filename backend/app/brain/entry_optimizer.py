"""
Entry Optimizer — Adaptive Entry Timing for Super-Human Trading.

Instead of entering immediately at the first signal, this module evaluates
whether the current price offers a GOOD entry point.

Humans rush entries out of FOMO. This module enforces patience:
    - Calculates VWAP-based "value area"
    - Detects pullbacks toward the value area
    - Identifies rejection wicks (confirmation)
    - Grades entries: A (perfect), B (good), C (extended/poor)

Rules:
    - Advisory only — never overrides Risk Engine
    - Grade A → boost confidence +0.10
    - Grade B → no change
    - Grade C → penalize confidence -0.10
    - RAM safe: pure numpy, no large buffers

Usage:
    optimizer = EntryOptimizer()
    quality = optimizer.evaluate(candles, direction="BUY", atr=2.5)
    # quality.grade = "A", quality.confidence_boost = 0.10
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)


# ====================================================================
# Data Containers
# ====================================================================

@dataclass
class EntryQuality:
    """Result of entry timing analysis."""
    grade: str = "C"                # A / B / C
    score: float = 0.0              # 0-100
    confidence_boost: float = 0.0   # -0.10 to +0.10
    optimal_entry: float = 0.0      # Recommended entry price
    current_distance_atr: float = 0.0  # Distance from optimal in ATR units
    is_pullback: bool = False       # Price pulling back toward value
    has_rejection_wick: bool = False # Rejection wick present
    reason: str = ""


# ====================================================================
# Helpers
# ====================================================================

def _compute_vwap(candles: pd.DataFrame) -> float:
    """Compute session VWAP from OHLCV data."""
    if len(candles) < 5:
        return float(candles["close"].iloc[-1])

    typical_price = (
        candles["high"].values + candles["low"].values + candles["close"].values
    ) / 3.0

    # Volume
    vol_col = None
    for col in ["tick_volume", "volume", "real_volume"]:
        if col in candles.columns:
            vol_col = col
            break

    if vol_col is None:
        return float(np.mean(typical_price[-20:]))

    vol = candles[vol_col].values.astype(float)

    # Use last 50 candles for session VWAP
    lookback = min(50, len(candles))
    tp = typical_price[-lookback:]
    v = vol[-lookback:]

    total_vol = np.sum(v)
    if total_vol < 1e-10:
        return float(np.mean(tp))

    return float(np.sum(tp * v) / total_vol)


def _compute_atr(candles: pd.DataFrame, period: int = 14) -> float:
    """Compute last ATR value."""
    if len(candles) < period + 1:
        return float(candles["high"].iloc[-1] - candles["low"].iloc[-1])

    high = candles["high"].values.astype(float)
    low = candles["low"].values.astype(float)
    close = candles["close"].values.astype(float)

    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1])
        )
    )

    if len(tr) < period:
        return float(np.mean(tr))

    # Wilder smoothing
    atr = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period

    return float(atr)


def _detect_rejection_wick(candles: pd.DataFrame, direction: str) -> bool:
    """
    Detect rejection wick on the latest candle.

    BUY: long lower wick (buyers rejected lower prices)
    SELL: long upper wick (sellers rejected higher prices)
    """
    if len(candles) < 1:
        return False

    last = candles.iloc[-1]
    o = float(last["open"])
    h = float(last["high"])
    l = float(last["low"])
    c = float(last["close"])

    body = abs(c - o)
    total_range = h - l

    if total_range < 1e-10:
        return False

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # Wick should be at least 2x the body
    if direction == "BUY":
        return lower_wick > body * 2.0 and lower_wick > total_range * 0.4
    elif direction == "SELL":
        return upper_wick > body * 2.0 and upper_wick > total_range * 0.4

    return False


def _is_pulling_back(candles: pd.DataFrame, vwap: float, direction: str) -> bool:
    """
    Detect if price is pulling back toward the value area.

    BUY:  price was above VWAP, now coming down toward it
    SELL: price was below VWAP, now coming up toward it
    """
    if len(candles) < 3:
        return False

    closes = candles["close"].values[-5:].astype(float)
    current = closes[-1]
    prev = closes[-2]

    if direction == "BUY":
        # Price was above VWAP and is now approaching it from above
        # OR price just dipped below VWAP slightly
        was_above = prev > vwap
        now_closer = abs(current - vwap) < abs(prev - vwap)
        return was_above and now_closer and current >= vwap * 0.998
    elif direction == "SELL":
        was_below = prev < vwap
        now_closer = abs(current - vwap) < abs(prev - vwap)
        return was_below and now_closer and current <= vwap * 1.002

    return False


# ====================================================================
# Entry Optimizer
# ====================================================================

class EntryOptimizer:
    """
    Adaptive Entry Timing — eliminates FOMO entries.

    Humans often enter at the worst possible price (chasing momentum).
    This module scores entry quality using:
        1. VWAP-based value area
        2. Pullback detection
        3. Rejection wick confirmation
        4. ATR-normalized distance from optimal

    Entry Grades:
        A = Pullback to value area + rejection wick (best entry)
        B = Near value area OR pullback without wick  
        C = Extended from value area (worst entry, likely to revert)
    """

    def evaluate(
        self,
        candles: pd.DataFrame,
        direction: str,
        atr: float = 0.0,
    ) -> EntryQuality:
        """
        Evaluate current entry quality.

        Args:
            candles: DataFrame with OHLCV
            direction: "BUY" or "SELL"
            atr: Pre-computed ATR (optional, will compute if 0)

        Returns:
            EntryQuality with grade, score, and confidence adjustment
        """
        result = EntryQuality()

        if candles is None or len(candles) < 10:
            result.reason = "insufficient_data"
            result.grade = "B"  # Neutral on insufficient data
            return result

        # Compute ATR if not provided
        if atr <= 0:
            atr = _compute_atr(candles)
        if atr <= 0:
            atr = 1.0  # Safety fallback

        # Compute VWAP
        vwap = _compute_vwap(candles)
        result.optimal_entry = vwap

        # Current price
        current_price = float(candles["close"].iloc[-1])

        # Distance from VWAP in ATR units
        distance = abs(current_price - vwap) / atr
        result.current_distance_atr = round(distance, 2)

        # Pullback detection
        result.is_pullback = _is_pulling_back(candles, vwap, direction)

        # Rejection wick
        result.has_rejection_wick = _detect_rejection_wick(candles, direction)

        # ── Scoring ──
        score = 50.0  # Start neutral

        # Distance scoring: closer to VWAP = better
        if distance < 0.3:
            score += 25.0  # Right at value area
        elif distance < 0.7:
            score += 15.0  # Near value area
        elif distance < 1.2:
            score += 5.0   # Moderate distance
        else:
            score -= 15.0  # Extended — poor entry

        # Direction-aware distance
        if direction == "BUY":
            if current_price < vwap:
                score += 10.0  # Below VWAP for a buy = discount
            elif current_price > vwap + atr:
                score -= 10.0  # Extended above VWAP for a buy
        elif direction == "SELL":
            if current_price > vwap:
                score += 10.0  # Above VWAP for a sell = premium
            elif current_price < vwap - atr:
                score -= 10.0  # Extended below VWAP for a sell

        # Pullback bonus
        if result.is_pullback:
            score += 15.0

        # Rejection wick bonus
        if result.has_rejection_wick:
            score += 10.0

        result.score = max(0.0, min(100.0, score))

        # ── Grade ──
        if result.score >= 75:
            result.grade = "A"
            result.confidence_boost = 0.10
            result.reason = "excellent_entry"
        elif result.score >= 45:
            result.grade = "B"
            result.confidence_boost = 0.0
            result.reason = "acceptable_entry"
        else:
            result.grade = "C"
            result.confidence_boost = -0.10
            result.reason = "poor_entry_extended"

        return result
