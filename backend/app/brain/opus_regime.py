"""
OPUS Ghost Protocol — Regime Engine v1.

จำแนกสภาวะตลาด 6 ประเภทสำหรับ aggressive smart-money hunting:
    1. STRONG_TREND       — ADX>30, BOS confirmed, clear HH/HL or LH/LL
    2. WEAK_TREND          — ADX 18-30, structure ambiguous
    3. DISTRIBUTION        — Wicks expanding at highs, volume divergence
    4. ACCUMULATION        — Tight range, wicks at lows, volume building
    5. VOL_EXPANSION       — ATR expanding rapidly (>1.5x baseline)
    6. VOL_COMPRESSION     — ATR contracting (squeeze before breakout)

Confidence Score (0–1) from 5 weighted factors:
    1. Structure Clarity    (BOS + HH/HL or LH/LL)           weight 0.25
    2. ATR Expansion Factor (ATR_now / ATR_baseline)          weight 0.25
    3. Range Compression    (range_10 / range_50)             weight 0.15
    4. Wick Anomaly Score   (avg wick/body ratio)             weight 0.20
    5. Session Volatility   (London/NY behavior alignment)    weight 0.15

Trading Gate:
    - confidence >= 0.65 → trade allowed
    - confidence >= 0.75 AND vol_expansion → aggressive mode
    - confidence < 0.65 → HOLD (no trade)
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.domain.enums import RegimeType
from app.domain.models import RegimeContext

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Parameters
# ═══════════════════════════════════════════════════════════════════
ADX_PERIOD = 14
ADX_STRONG = 30           # OPUS is stricter than base (30 vs 25)
ADX_WEAK_MIN = 18

ATR_PERIOD = 14
ATR_BASELINE_PERIOD = 50  # Baseline ATR computed over 50 bars
ATR_EXPANSION_THRESH = 1.5  # ATR_now > 1.5x baseline = expansion
ATR_COMPRESSION_THRESH = 0.6  # ATR_now < 0.6x baseline = compression
ATR_EXTREME_MULT = 2.2    # Kill-switch level

RANGE_SHORT = 10          # Short-term range for compression detection
RANGE_LONG = 50           # Long-term range for compression ratio

WICK_LOOKBACK = 10
WICK_ANOMALY_THRESH = 0.55  # avg wick/range > 55% = manipulation

BOS_LOOKBACK = 20         # Bars for swing detection (BOS)
SWING_TOLERANCE = 3       # Min bars between swing points

# Session volatility expectations (ATR multiplier by session)
SESSION_VOL_EXPECT = {
    "LONDON": 1.3,        # London typically 30% more volatile
    "NEW_YORK": 1.2,      # NY slightly above average
    "OVERLAP": 1.5,       # London-NY overlap = peak
    "ASIA": 0.6,          # Asia = quiet for gold/silver
    "CLOSED": 0.3,
}

# Confidence weights
W_STRUCTURE = 0.25
W_ATR_EXPANSION = 0.25
W_RANGE_COMPRESSION = 0.15
W_WICK_ANOMALY = 0.20
W_SESSION_VOL = 0.15


# ═══════════════════════════════════════════════════════════════════
# Helper: Wilder Smoothing (shared with base regime)
# ═══════════════════════════════════════════════════════════════════

def _wilder_smooth(data: np.ndarray, period: int) -> np.ndarray:
    """Wilder EMA smoothing."""
    out = np.full_like(data, np.nan, dtype=float)
    if len(data) < period:
        return out
    out[period - 1] = np.mean(data[:period])
    alpha = 1.0 / period
    for i in range(period, len(data)):
        out[i] = out[i - 1] * (1 - alpha) + data[i] * alpha
    return out


def _compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                 period: int = 14) -> tuple[float, float, float]:
    """Compute ADX, +DI, -DI. Returns: (adx_last, plus_di_last, minus_di_last)"""
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


def _compute_atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                        period: int = 14) -> np.ndarray:
    """Compute ATR series."""
    if len(close) < 2:
        return np.array([0.0])
    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    return _wilder_smooth(tr, period)


# ═══════════════════════════════════════════════════════════════════
# Structure Detection — Swing Highs/Lows, BOS, HH/HL/LH/LL
# ═══════════════════════════════════════════════════════════════════

def _detect_swings(high: np.ndarray, low: np.ndarray,
                   lookback: int = BOS_LOOKBACK,
                   tolerance: int = SWING_TOLERANCE) -> dict:
    """
    Detect swing highs and swing lows.
    
    Returns:
        dict with:
            swing_highs: list of (index, price)
            swing_lows: list of (index, price)
            structure: "BULLISH" | "BEARISH" | "UNCLEAR"
            bos_detected: bool
            clarity_score: float (0-1)
    """
    n = len(high)
    if n < lookback:
        return {
            "swing_highs": [], "swing_lows": [],
            "structure": "UNCLEAR", "bos_detected": False,
            "clarity_score": 0.0,
        }

    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []

    # Use last `lookback` bars
    start = max(0, n - lookback)
    for i in range(start + tolerance, n - tolerance):
        # Swing high: higher than `tolerance` bars on both sides
        if all(high[i] >= high[i - j] for j in range(1, tolerance + 1)) and \
           all(high[i] >= high[i + j] for j in range(1, tolerance + 1)):
            swing_highs.append((i, float(high[i])))

        # Swing low: lower than `tolerance` bars on both sides
        if all(low[i] <= low[i - j] for j in range(1, tolerance + 1)) and \
           all(low[i] <= low[i + j] for j in range(1, tolerance + 1)):
            swing_lows.append((i, float(low[i])))

    # Determine structure
    structure = "UNCLEAR"
    bos = False
    clarity = 0.0

    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        # Check HH/HL (bullish) or LH/LL (bearish)
        hh = swing_highs[-1][1] > swing_highs[-2][1]
        hl = swing_lows[-1][1] > swing_lows[-2][1]
        lh = swing_highs[-1][1] < swing_highs[-2][1]
        ll = swing_lows[-1][1] < swing_lows[-2][1]

        if hh and hl:
            structure = "BULLISH"
            clarity = 0.8
            # BOS = price broke above last swing high
            if float(high[-1]) > swing_highs[-1][1]:
                bos = True
                clarity = 1.0
        elif lh and ll:
            structure = "BEARISH"
            clarity = 0.8
            # BOS = price broke below last swing low
            if float(low[-1]) < swing_lows[-1][1]:
                bos = True
                clarity = 1.0
        elif hh and not hl:
            structure = "BULLISH"  # Partial bullish
            clarity = 0.5
        elif ll and not lh:
            structure = "BEARISH"  # Partial bearish
            clarity = 0.5
        else:
            clarity = 0.2
    elif len(swing_highs) >= 1 or len(swing_lows) >= 1:
        clarity = 0.3

    return {
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "structure": structure,
        "bos_detected": bos,
        "clarity_score": round(clarity, 3),
    }


# ═══════════════════════════════════════════════════════════════════
# Main: classify_opus_regime
# ═══════════════════════════════════════════════════════════════════

@dataclass
class OpusRegimeResult:
    """OPUS-specific regime classification output."""
    regime: RegimeType
    confidence: float
    actionable: bool
    aggressive_mode: bool
    structure: str  # BULLISH / BEARISH / UNCLEAR
    bos_detected: bool
    atr_expansion_factor: float
    wick_anomaly: float
    reasons: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def to_regime_context(self) -> RegimeContext:
        """Convert to standard RegimeContext for pipeline compatibility."""
        return RegimeContext(
            regime=self.regime,
            actionable=self.actionable,
            score=round(self.confidence, 3),
            reason="; ".join(self.reasons),
            details={
                **self.details,
                "opus_regime": self.regime.value,
                "opus_confidence": round(self.confidence, 3),
                "opus_aggressive": self.aggressive_mode,
                "opus_structure": self.structure,
                "opus_bos": self.bos_detected,
                "opus_atr_expansion": round(self.atr_expansion_factor, 3),
                "opus_wick_anomaly": round(self.wick_anomaly, 3),
            },
        )


def classify_opus_regime(
    candles: pd.DataFrame,
    session: str = "CLOSED",
) -> OpusRegimeResult:
    """
    OPUS Ghost Protocol — 6-regime classifier with 5-factor confidence.

    Args:
        candles: DataFrame with [open, high, low, close, tick_volume(optional)]
        session: Current market session (ASIA/LONDON/NEW_YORK/OVERLAP/CLOSED)

    Returns:
        OpusRegimeResult with full classification and confidence

    Example Scenarios:
        1. VOL_EXPANSION: ATR doubles in 5 bars + BOS confirmed
           → confidence ~0.85, aggressive_mode=True
        2. ACCUMULATION: ADX<18, tight range, wicks at bottom, volume building
           → confidence ~0.70, structure="BULLISH"
        3. DISTRIBUTION: ADX dropping, wicks expanding at highs, volume declining
           → confidence ~0.65, structure="BEARISH"
        4. VOL_COMPRESSION: ATR < 0.6x baseline, BB squeeze imminent
           → confidence ~0.60, actionable=False (wait for expansion)
    """
    min_bars = max(ATR_BASELINE_PERIOD + ATR_PERIOD, RANGE_LONG + 10, 80)
    if candles is None or len(candles) < min_bars:
        return OpusRegimeResult(
            regime=RegimeType.UNKNOWN,
            confidence=0.0,
            actionable=False,
            aggressive_mode=False,
            structure="UNCLEAR",
            bos_detected=False,
            atr_expansion_factor=1.0,
            wick_anomaly=0.0,
            reasons=["Insufficient data"],
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
        return OpusRegimeResult(
            regime=RegimeType.UNKNOWN, confidence=0.0,
            actionable=False, aggressive_mode=False,
            structure="UNCLEAR", bos_detected=False,
            atr_expansion_factor=1.0, wick_anomaly=0.0,
            reasons=["Invalid data"],
        )

    n = len(close)
    details: dict = {}
    reasons: list[str] = []

    # ─── 2. Compute Indicators ───

    # ADX
    adx_val, plus_di, minus_di = _compute_adx(high, low, close, ADX_PERIOD)
    details["adx"] = round(adx_val, 2)
    details["plus_di"] = round(plus_di, 2)
    details["minus_di"] = round(minus_di, 2)

    # ATR + expansion factor
    atr_series = _compute_atr_series(high, low, close, ATR_PERIOD)
    valid_atr = atr_series[~np.isnan(atr_series)]
    current_atr = float(valid_atr[-1]) if len(valid_atr) > 0 else 0.0
    baseline_atr = float(np.mean(valid_atr[-ATR_BASELINE_PERIOD:])) if len(valid_atr) >= ATR_BASELINE_PERIOD else current_atr
    atr_expansion = current_atr / max(baseline_atr, 1e-9)
    details["atr"] = round(current_atr, 5)
    details["atr_baseline"] = round(baseline_atr, 5)
    details["atr_expansion"] = round(atr_expansion, 3)

    # Range compression ratio
    range_short = float(np.max(high[-RANGE_SHORT:]) - np.min(low[-RANGE_SHORT:])) if n >= RANGE_SHORT else 1.0
    range_long = float(np.max(high[-RANGE_LONG:]) - np.min(low[-RANGE_LONG:])) if n >= RANGE_LONG else 1.0
    range_compression = range_short / max(range_long, 1e-9)
    details["range_compression"] = round(range_compression, 3)

    # Wick anomaly score
    candle_range = high[-WICK_LOOKBACK:] - low[-WICK_LOOKBACK:]
    body = np.abs(close[-WICK_LOOKBACK:] - open_[-WICK_LOOKBACK:])
    safe_range = np.where(candle_range <= 0, 1e-9, candle_range)
    wick_pct = (candle_range - body) / safe_range
    wick_anomaly = float(np.mean(np.nan_to_num(wick_pct, nan=0.0)))
    details["wick_anomaly"] = round(wick_anomaly, 3)

    # Structure detection (BOS, HH/HL/LH/LL)
    swings = _detect_swings(high, low, lookback=BOS_LOOKBACK)
    structure = swings["structure"]
    bos = swings["bos_detected"]
    structure_clarity = swings["clarity_score"]
    details["structure"] = structure
    details["bos_detected"] = bos
    details["structure_clarity"] = structure_clarity

    # Volume behavior
    vol_avg_20 = float(np.mean(volume[-20:])) if len(volume) >= 20 else float(np.mean(volume))
    vol_avg_5 = float(np.mean(volume[-5:])) if len(volume) >= 5 else vol_avg_20
    vol_building = vol_avg_5 > vol_avg_20 * 1.2
    vol_declining = vol_avg_5 < vol_avg_20 * 0.7
    details["vol_building"] = vol_building
    details["vol_declining"] = vol_declining

    # Session volatility alignment
    expected_mult = SESSION_VOL_EXPECT.get(session, 1.0)
    # If ATR expansion matches session expectation, score is higher
    session_alignment = min(1.0, atr_expansion / max(expected_mult, 0.1))
    if session in ("LONDON", "NEW_YORK", "OVERLAP") and atr_expansion > 1.0:
        session_alignment = min(1.0, session_alignment * 1.1)
    details["session_alignment"] = round(session_alignment, 3)

    # ─── 3. Compute 5-Factor Confidence Score ───
    f_structure = structure_clarity
    f_atr = min(1.0, max(0.0, (atr_expansion - 0.5) / 2.0))  # 0.5→0, 2.5→1.0
    f_range = 1.0 - range_compression  # Tight range = high compression score
    f_wick = min(1.0, wick_anomaly / 0.8)  # 0→0, 0.8→1.0
    f_session = session_alignment

    raw_confidence = (
        W_STRUCTURE * f_structure +
        W_ATR_EXPANSION * f_atr +
        W_RANGE_COMPRESSION * f_range +
        W_WICK_ANOMALY * f_wick +
        W_SESSION_VOL * f_session
    )

    details["factor_structure"] = round(f_structure, 3)
    details["factor_atr"] = round(f_atr, 3)
    details["factor_range"] = round(f_range, 3)
    details["factor_wick"] = round(f_wick, 3)
    details["factor_session"] = round(f_session, 3)

    # ─── 4. Regime Classification (priority ordering) ───
    regime = RegimeType.UNKNOWN
    actionable = True
    confidence = raw_confidence

    # 4.1 — Volatility Expansion (ATR blow-up → aggressive opportunity)
    if atr_expansion >= ATR_EXPANSION_THRESH and adx_val >= ADX_WEAK_MIN:
        regime = RegimeType.VOL_EXPANSION
        # Boost confidence if structure is clear
        confidence = raw_confidence * 1.2 if bos else raw_confidence * 1.0
        confidence = min(1.0, confidence)
        actionable = True
        reasons.append(f"VOL_EXPANSION: ATR_exp={atr_expansion:.2f}x, ADX={adx_val:.1f}")

    # 4.2 — Volatility Compression (squeeze → wait for breakout)
    elif atr_expansion <= ATR_COMPRESSION_THRESH and range_compression < 0.4:
        regime = RegimeType.VOL_COMPRESSION
        confidence = max(0.3, raw_confidence)
        actionable = False  # Don't trade in squeeze, wait for expansion
        reasons.append(f"VOL_COMPRESSION: ATR_exp={atr_expansion:.2f}x, range_comp={range_compression:.2f}")

    # 4.3 — Distribution (smart money selling)
    elif (adx_val < ADX_STRONG and wick_anomaly > WICK_ANOMALY_THRESH
          and vol_declining and structure in ("BEARISH", "UNCLEAR")):
        regime = RegimeType.DISTRIBUTION
        confidence = min(1.0, raw_confidence * 1.1)
        actionable = True  # Sell-side opportunities
        reasons.append(f"DISTRIBUTION: wicks={wick_anomaly:.2f}, vol_declining, ADX={adx_val:.1f}")

    # 4.4 — Accumulation (smart money buying)
    elif (adx_val < ADX_WEAK_MIN and range_compression < 0.5
          and vol_building and wick_anomaly > 0.35):
        regime = RegimeType.ACCUMULATION
        confidence = min(1.0, raw_confidence * 1.05)
        actionable = True  # Buy-side opportunities after sweep
        reasons.append(f"ACCUMULATION: range_comp={range_compression:.2f}, vol_building, wicks={wick_anomaly:.2f}")

    # 4.5 — Strong Trend
    elif adx_val >= ADX_STRONG and structure in ("BULLISH", "BEARISH"):
        regime = RegimeType.STRONG_TREND
        confidence = min(1.0, raw_confidence + 0.15)
        actionable = True
        reasons.append(f"STRONG_TREND: ADX={adx_val:.1f}, structure={structure}")

    # 4.6 — Weak Trend
    elif adx_val >= ADX_WEAK_MIN:
        regime = RegimeType.WEAK_TREND
        confidence = max(0.3, raw_confidence * 0.9)
        actionable = True
        reasons.append(f"WEAK_TREND: ADX={adx_val:.1f}")

    # Fallback
    else:
        regime = RegimeType.RANGING
        confidence = max(0.2, raw_confidence * 0.6)
        actionable = False
        reasons.append(f"RANGING: ADX={adx_val:.1f}, no clear pattern")

    # ─── 5. Gate Logic ───
    confidence = max(0.0, min(1.0, confidence))
    aggressive_mode = False

    if confidence < 0.65:
        actionable = False  # Below OPUS minimum confidence
        reasons.append(f"OPUS gate: confidence {confidence:.2f} < 0.65 → HOLD")

    if confidence >= 0.75 and regime == RegimeType.VOL_EXPANSION:
        aggressive_mode = True
        reasons.append("OPUS AGGRESSIVE MODE: high confidence + vol expansion")

    # ─── 6. Direction for compatibility ───
    if structure == "BULLISH":
        details["direction"] = RegimeType.TRENDING_UP.value
    elif structure == "BEARISH":
        details["direction"] = RegimeType.TRENDING_DOWN.value
    else:
        details["direction"] = RegimeType.UNKNOWN.value

    final_reason = "; ".join(reasons) if reasons else "Market classified"

    logger.debug("opus_regime_classified", extra={
        "regime": regime.value,
        "confidence": round(confidence, 3),
        "actionable": actionable,
        "aggressive": aggressive_mode,
        "structure": structure,
        "bos": bos,
        "atr_expansion": round(atr_expansion, 3),
        "wick_anomaly": round(wick_anomaly, 3),
        "session": session,
    })

    return OpusRegimeResult(
        regime=regime,
        confidence=round(confidence, 3),
        actionable=actionable,
        aggressive_mode=aggressive_mode,
        structure=structure,
        bos_detected=bos,
        atr_expansion_factor=round(atr_expansion, 3),
        wick_anomaly=round(wick_anomaly, 3),
        reasons=reasons,
        details=details,
    )
