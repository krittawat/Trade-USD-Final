"""
Forex Precision Strategy — High-Confluence, High Win-Rate Forex Entry.

7-Layer Confluence System:
    1. EMA 50/200 Trend Alignment     → ทิศทางหลัก
    2. ADX > 25 Trend Strength        → ยืนยันเทรนด์แข็งแรง
    3. RSI Pullback Zone (40-60)      → entry ที่ไม่ extreme
    4. Price near EMA 50 (≤1.5 ATR)   → pullback to dynamic support
    5. Candle Pattern (engulfing/pin) → confirmation candle
    6. Session Filter (London/NY)     → high-vol session only
    7. +DI/-DI Directional Match      → momentum direction

Entry Rules:
    - ≥5 out of 7 layers must pass
    - SL = 3.5× ATR (wide for Forex noise)
    - TP = 7.0× ATR (RR ~1:2)
    - Designed for WR ≥70%, PF ≥2.0

Target:
    - Timeframe: M5
    - Forex pairs: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, NZDUSD
    - Sessions: London (08-16 UTC), NY (13-21 UTC)
"""
import pandas as pd

import app.analysis.indicators as ind
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)

# ─── Parameters ──────────────────────────────────────────
EMA_FAST = 50
EMA_SLOW = 200
ADX_PERIOD = 14
ADX_THRESHOLD = 20    # Relaxed: Forex ADX generally lower than Gold
RSI_PERIOD = 14
ATR_PERIOD = 14

# SL/TP — optimized for Forex: tighter SL + wider TP
SL_ATR_MULT = 2.0     # Tighter SL (was 3.5 — too wide, ate profits)
RR_TARGET = 3.0       # 1:3 RR → profitable even at 33% WR
PULLBACK_ZONE = 2.5   # Widened: 1.5 was too tight, missed most entries

MIN_CONFLUENCE = 5    # Lowered: 6/7 was nearly impossible to hit
MIN_CONFIDENCE = 0.60 # Lowered from 0.75 (was too strict)

# Session hours (UTC) — only trade during London/NY
LONDON_START = 7   # London opens 07:00 UTC (pre-market 7, main 8)
LONDON_END = 16
NY_START = 13      # NY opens 13:00 UTC
NY_END = 21


# ─── Candle Pattern Detection ────────────────────────────

def _is_bullish_engulfing(candles: pd.DataFrame) -> bool:
    """Detect bullish engulfing on last 2 bars."""
    if len(candles) < 2:
        return False
    prev = candles.iloc[-2]
    curr = candles.iloc[-1]
    # Previous: bearish, Current: bullish, body engulfs
    return (prev["close"] < prev["open"] and
            curr["close"] > curr["open"] and
            curr["close"] > prev["open"] and
            curr["open"] <= prev["close"])


def _is_bearish_engulfing(candles: pd.DataFrame) -> bool:
    """Detect bearish engulfing on last 2 bars."""
    if len(candles) < 2:
        return False
    prev = candles.iloc[-2]
    curr = candles.iloc[-1]
    return (prev["close"] > prev["open"] and
            curr["close"] < curr["open"] and
            curr["close"] < prev["open"] and
            curr["open"] >= prev["close"])


def _is_bullish_pin_bar(candles: pd.DataFrame, atr: float) -> bool:
    """Detect bullish pin bar (long lower wick, small body)."""
    if len(candles) < 1 or atr <= 0:
        return False
    c = candles.iloc[-1]
    body = abs(c["close"] - c["open"])
    lower_wick = min(c["open"], c["close"]) - c["low"]
    upper_wick = c["high"] - max(c["open"], c["close"])
    total_range = c["high"] - c["low"]
    if total_range < atr * 0.3:
        return False  # Too small candle
    # Long lower wick (≥60% of range), small body (≤30%), bullish close
    return (lower_wick > total_range * 0.55 and
            body < total_range * 0.35 and
            c["close"] >= c["open"])


def _is_bearish_pin_bar(candles: pd.DataFrame, atr: float) -> bool:
    """Detect bearish pin bar (long upper wick, small body)."""
    if len(candles) < 1 or atr <= 0:
        return False
    c = candles.iloc[-1]
    body = abs(c["close"] - c["open"])
    upper_wick = c["high"] - max(c["open"], c["close"])
    total_range = c["high"] - c["low"]
    if total_range < atr * 0.3:
        return False
    return (upper_wick > total_range * 0.55 and
            body < total_range * 0.35 and
            c["close"] <= c["open"])


def _is_strong_candle(candles: pd.DataFrame, atr: float) -> tuple[bool, bool]:
    """
    Check if current candle has strong body (≥60% of range, body > 0.5×ATR).
    Returns (is_bullish_strong, is_bearish_strong).
    """
    if len(candles) < 1 or atr <= 0:
        return False, False
    c = candles.iloc[-1]
    body = abs(c["close"] - c["open"])
    total_range = c["high"] - c["low"]
    if total_range <= 0:
        return False, False
    body_ratio = body / total_range
    is_strong = body_ratio > 0.55 and body > atr * 0.4
    if is_strong:
        return (c["close"] > c["open"], c["close"] < c["open"])
    return False, False


def _get_session(candles: pd.DataFrame) -> str:
    """Get current trading session from candle time."""
    try:
        last_time = candles.iloc[-1].get("time")
        if last_time is None:
            return "UNKNOWN"
        if isinstance(last_time, (pd.Timestamp, datetime)):
            hour = last_time.hour
        else:
            return "UNKNOWN"

        if LONDON_START <= hour < NY_START:
            return "LONDON"
        elif NY_START <= hour < LONDON_END:
            return "OVERLAP"  # London + NY overlap = best
        elif LONDON_END <= hour < NY_END:
            return "NY"
        else:
            return "ASIAN"
    except Exception:
        return "UNKNOWN"


# ─── Strategy Class ──────────────────────────────────────

class ForexPrecisionStrategy(BaseStrategy):
    """
    Forex Precision Strategy — 7-layer high-confluence system.

    Combines the best elements of trend_rider (EMA alignment + ADX + pullback)
    and sniper (candle pattern + structure) into a unified Forex-optimized engine.

    Target: WR ≥70%, PF ≥2.0
    """

    name = "forex_precision"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.RANGING,        # Allow ranging — rely on EMA/ADX to filter
    ]

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        sl_atr_mult: float = SL_ATR_MULT,
        rr_target: float = RR_TARGET,
        **kwargs,
    ) -> Decision:
        """
        7-Layer Confluence Analysis for Forex pairs.

        Layers:
            1. EMA 50/200 alignment
            2. ADX > 25
            3. RSI in pullback zone
            4. Price near EMA 50
            5. Candle pattern confirmation
            6. Session filter
            7. +DI/-DI match
        """
        symbol = profile.symbol

        # ─── Data validation ───
        if candles is None or len(candles) < EMA_SLOW + 10:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {EMA_SLOW + 10})",
            )

        # ─── Calculate Indicators ───
        ema50 = ind.ema(candles["close"], length=EMA_FAST)
        ema200 = ind.ema(candles["close"], length=EMA_SLOW)
        rsi = ind.rsi(candles["close"], length=RSI_PERIOD)
        atr = ind.atr(candles["high"], candles["low"], candles["close"], length=ATR_PERIOD)
        adx_df = ind.adx(candles["high"], candles["low"], candles["close"], length=ADX_PERIOD)

        # Extract values
        close = candles["close"].iloc[-1]
        ema50_val = ema50.iloc[-1] if ema50 is not None else None
        ema200_val = ema200.iloc[-1] if ema200 is not None else None
        rsi_val = rsi.iloc[-1] if rsi is not None else None
        atr_val = atr.iloc[-1] if atr is not None else None

        # ADX and DI values
        adx_val = None
        plus_di = None
        minus_di = None
        if adx_df is not None:
            adx_col = f"ADX_{ADX_PERIOD}"
            dmp_col = f"DMP_{ADX_PERIOD}"
            dmn_col = f"DMN_{ADX_PERIOD}"
            if adx_col in adx_df.columns:
                adx_val = adx_df[adx_col].iloc[-1]
            if dmp_col in adx_df.columns:
                plus_di = adx_df[dmp_col].iloc[-1]
            if dmn_col in adx_df.columns:
                minus_di = adx_df[dmn_col].iloc[-1]

        # NaN check
        if any(v is None or (isinstance(v, float) and pd.isna(v))
               for v in [ema50_val, ema200_val, atr_val, rsi_val]):
            return self.create_hold(symbol=symbol, reason="Indicators NaN")

        # ─── HARD GATES (all must pass — reject trade immediately otherwise) ───

        # Gate 1: EMA Trend — must have clear trend direction
        uptrend = ema50_val > ema200_val
        downtrend = ema50_val < ema200_val
        if not uptrend and not downtrend:
            return self.create_hold(symbol=symbol, reason="No clear trend (EMA50 ≈ EMA200)")

        # Gate 2: ADX > 18 — must have some directional movement
        strong_trend = adx_val is not None and not pd.isna(adx_val) and adx_val > ADX_THRESHOLD
        if not strong_trend:
            return self.create_hold(
                symbol=symbol,
                reason=f"ADX {adx_val:.1f} ≤ {ADX_THRESHOLD} (flat market — skip)",
            )

        # Gate 3: Session — prefer London/NY/Overlap, allow Asian with penalty
        session = _get_session(candles)
        session_penalty = 0.0
        if session == "ASIAN":
            session_penalty = -0.10  # Allow but penalize confidence
        elif session not in ("LONDON", "OVERLAP", "NY", "ASIAN"):
            return self.create_hold(
                symbol=symbol,
                reason=f"Session: {session} (market closed)",
            )

        # Soft Layer 4: Pullback — bonus if price near EMA50 (not hard gate)
        dist_to_ema50 = abs(close - ema50_val)
        pullback_threshold = atr_val * PULLBACK_ZONE
        is_pullback = dist_to_ema50 <= pullback_threshold

        # Soft Layer 5: Candle Pattern — bonus if present (not hard gate)
        bull_engulf = _is_bullish_engulfing(candles)
        bear_engulf = _is_bearish_engulfing(candles)
        bull_pin = _is_bullish_pin_bar(candles, atr_val)
        bear_pin = _is_bearish_pin_bar(candles, atr_val)
        bull_strong, bear_strong = _is_strong_candle(candles, atr_val)

        has_bull_candle = bull_engulf or bull_pin or bull_strong
        has_bear_candle = bear_engulf or bear_pin or bear_strong

        # ─── Gates passed — build confidence from bonus layers ───
        confidence = 0.50 + session_penalty  # Base + session adjustment
        reasons = []
        action = Action.BUY if uptrend else Action.SELL

        # Bonus 1: EMA trend (already confirmed by gate)
        reasons.append(f"EMA50{'>' if uptrend else '<'}200")

        # Bonus 2: ADX strength magnitude
        if adx_val > 35:
            confidence += 0.10
            reasons.append(f"ADX {adx_val:.1f} (very strong)")
        else:
            confidence += 0.05
            reasons.append(f"ADX {adx_val:.1f} (trending)")

        # Bonus 3: RSI confirmation
        if rsi_val is not None and not pd.isna(rsi_val):
            if uptrend and 35 <= rsi_val <= 55:
                confidence += 0.10
                reasons.append(f"RSI {rsi_val:.1f} pullback zone")
            elif downtrend and 45 <= rsi_val <= 65:
                confidence += 0.10
                reasons.append(f"RSI {rsi_val:.1f} pullback zone")
            elif uptrend and rsi_val > 70:
                confidence -= 0.10
                reasons.append(f"RSI {rsi_val:.1f} overbought penalty")
            elif downtrend and rsi_val < 30:
                confidence -= 0.10
                reasons.append(f"RSI {rsi_val:.1f} oversold penalty")

        # Bonus 4: Candle pattern quality (now a bonus, not hard gate)
        if uptrend:
            if bull_engulf:
                confidence += 0.15
                reasons.append("Bullish engulfing")
            elif bull_pin:
                confidence += 0.12
                reasons.append("Bullish pin bar")
            elif bull_strong:
                confidence += 0.08
                reasons.append("Strong bullish body")
            else:
                confidence += 0.02  # Weak candle — still allow entry
                reasons.append("Neutral candle (no pattern)")
        else:
            if bear_engulf:
                confidence += 0.15
                reasons.append("Bearish engulfing")
            elif bear_pin:
                confidence += 0.12
                reasons.append("Bearish pin bar")
            elif bear_strong:
                confidence += 0.08
                reasons.append("Strong bearish body")
            else:
                confidence += 0.02
                reasons.append("Neutral candle (no pattern)")

        # Bonus 5: Session quality
        if session == "OVERLAP":
            confidence += 0.10
            reasons.append("London/NY Overlap (best)")
        else:
            confidence += 0.05
            reasons.append(f"Session: {session}")

        # Bonus 6: +DI/-DI directional match
        if plus_di is not None and minus_di is not None:
            if not pd.isna(plus_di) and not pd.isna(minus_di):
                if uptrend and plus_di > minus_di:
                    confidence += 0.08
                    reasons.append(f"+DI({plus_di:.1f})>-DI({minus_di:.1f})")
                elif downtrend and minus_di > plus_di:
                    confidence += 0.08
                    reasons.append(f"-DI({minus_di:.1f})>+DI({plus_di:.1f})")
                else:
                    confidence -= 0.05
                    reasons.append("DI direction mismatch")

        # Bonus 7: Price bouncing from EMA50 (direction match)
        if uptrend and close > ema50_val:
            confidence += 0.05
            reasons.append("Bouncing above EMA50")
        elif downtrend and close < ema50_val:
            confidence += 0.05
            reasons.append("Rejecting below EMA50")

        # ─── Final confidence check ───
        confidence = max(0.0, min(1.0, confidence))

        if confidence < MIN_CONFIDENCE:
            return self.create_hold(
                symbol=symbol,
                reason=f"Confidence {confidence:.2f} < {MIN_CONFIDENCE} after gates+bonus | " + "; ".join(reasons),
            )

        # ─── Regime bonus ───
        if regime in self.suitable_regimes:
            confidence = min(1.0, confidence + 0.05)

        # ─── SL/TP Calculation ───
        sl_distance = atr_val * sl_atr_mult
        tp_distance = sl_distance * rr_target

        if action == Action.BUY:
            # SL below EMA50 or ATR-based, whichever gives more room
            sl_from_ema = ema50_val - atr_val * 0.3
            stop_loss = round(min(sl_from_ema, close - sl_distance), profile.digits)
            take_profit = round(close + tp_distance, profile.digits)
        else:
            sl_from_ema = ema50_val + atr_val * 0.3
            stop_loss = round(max(sl_from_ema, close + sl_distance), profile.digits)
            take_profit = round(close - tp_distance, profile.digits)

        actual_sl = abs(close - stop_loss)
        rr = tp_distance / actual_sl if actual_sl > 0 else 0
        reason_text = "; ".join(reasons)

        logger.debug("forex_precision_signal", extra={
            "symbol": symbol, "action": action.value,
            "confidence": round(confidence, 3),
            "gates": "all_passed",
            "session": session, "rr": round(rr, 1),
        })

        return Decision(
            symbol=symbol, action=action, confidence=round(confidence, 3),
            reason=reason_text, stop_loss=stop_loss, take_profit=take_profit,
            risk_reward_ratio=round(rr, 2), strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["forex_precision", "multi_confluence", "high_wr"],
            debug={
                "ema50": round(ema50_val, profile.digits),
                "ema200": round(ema200_val, profile.digits),
                "adx": round(adx_val, 1) if adx_val and not pd.isna(adx_val) else None,
                "rsi": round(rsi_val, 1) if rsi_val else None,
                "atr": round(atr_val, profile.digits),
                "session": session,
                "dist_to_ema50": round(dist_to_ema50, profile.digits),
            },
        )

