"""
Liquidity Hunter — Smart Money Manipulation Detection.

ตรวจจับพฤติกรรม Smart Money:
    1. Liquidity Pool Mapping    — หา equal highs/lows, PDH/PDL, Asia H/L
    2. Sweep Detection           — ราคากวาด liquidity แล้วกลับตัว
    3. Displacement Detection    — แท่งเทียนยาวแรง (displacement) หลัง sweep
    4. Session Open Trap         — กับดัก London/NY open
    5. News Spike Fake Move      — ข่าวกระชากแล้วกลับ

Output: LiquiditySignal with zones, sweep status, displacement strength
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Parameters
# ═══════════════════════════════════════════════════════════════════
EQUAL_LEVEL_TOLERANCE_ATR = 0.1    # Prices within 10% of ATR are "equal"
EQUAL_LEVEL_MIN_TOUCHES = 2        # Minimum touches to form pool
POOL_LOOKBACK = 50                 # Bars to scan for liquidity pools

SWEEP_WICK_MIN_ATR = 0.3          # Sweep wick must be >= 30% of ATR
DISPLACEMENT_BODY_MIN_ATR = 0.8   # Displacement candle body >= 80% of ATR
DISPLACEMENT_MAX_BARS = 3          # Displacement must happen within 3 bars of sweep

SESSION_TRAP_MAX_BARS = 3          # Session trap: spike + snapback within 3 bars
NEWS_SPIKE_ATR_MULT = 1.5         # News spike: wick > 1.5x ATR
NEWS_SPIKE_WICK_PCT = 0.70        # And wick > 70% of candle range


# ═══════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════

@dataclass
class LiquidityZone:
    """A liquidity resting zone (cluster of equal levels)."""
    price: float
    zone_type: str  # "EQUAL_HIGHS" | "EQUAL_LOWS" | "PDH" | "PDL" | "ASIA_HIGH" | "ASIA_LOW"
    touches: int = 0
    swept: bool = False
    bar_indices: list[int] = field(default_factory=list)


@dataclass
class LiquiditySignal:
    """Output of the Liquidity Hunter."""
    sweep_detected: bool = False
    sweep_direction: str = "NONE"  # BUY (sweep lows → buy) | SELL (sweep highs → sell) | NONE
    displacement_strength: float = 0.0
    displacement_direction: str = "NONE"
    re_acceptance: bool = False
    liquidity_zones: list[LiquidityZone] = field(default_factory=list)
    trap_type: str = "NONE"  # SESSION_TRAP | NEWS_SPIKE | NONE
    confidence: float = 0.0
    entry_price: float = 0.0
    invalidation_price: float = 0.0
    reasons: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# Liquidity Hunter Engine
# ═══════════════════════════════════════════════════════════════════

class LiquidityHunter:
    """
    Smart Money Manipulation Detector.

    Usage:
        hunter = LiquidityHunter()
        signal = hunter.scan(candles, session="LONDON")

        if signal.sweep_detected and signal.confidence >= 0.65:
            # Execute trade in signal.sweep_direction
    """

    def __init__(
        self,
        pool_lookback: int = POOL_LOOKBACK,
        equal_level_tolerance_atr: float = EQUAL_LEVEL_TOLERANCE_ATR,
    ):
        self.pool_lookback = pool_lookback
        self.equal_level_tolerance_atr = equal_level_tolerance_atr

    def scan(
        self,
        candles: pd.DataFrame,
        session: str = "CLOSED",
        atr: float | None = None,
    ) -> LiquiditySignal:
        """
        Full liquidity scan — detect manipulation patterns.

        Args:
            candles: OHLCV DataFrame (minimum 60 bars)
            session: Current market session
            atr: Pre-computed ATR (optional)

        Returns:
            LiquiditySignal with complete analysis
        """
        if candles is None or len(candles) < 60:
            return LiquiditySignal(reasons=["Insufficient data"])

        try:
            high = candles["high"].values.astype(float)
            low = candles["low"].values.astype(float)
            close = candles["close"].values.astype(float)
            open_ = candles["open"].values.astype(float)
        except Exception:
            return LiquiditySignal(reasons=["Invalid data"])

        n = len(close)

        # Compute ATR if not provided
        if atr is None or atr <= 0:
            atr = self._compute_atr(high, low, close)

        if atr <= 0:
            return LiquiditySignal(reasons=["ATR is zero"])

        details: dict = {"atr": round(atr, 5)}
        reasons: list[str] = []

        # ─── Step 1: Map Liquidity Zones ───
        zones = self._find_liquidity_zones(high, low, close, open_, n, atr)
        details["zones_found"] = len(zones)

        # ─── Step 2: Detect Sweep ───
        sweep_result = self._detect_sweep(high, low, close, open_, n, atr, zones)
        sweep_detected = sweep_result["detected"]
        sweep_direction = sweep_result["direction"]
        sweep_wick = sweep_result["sweep_wick"]

        if sweep_detected:
            reasons.append(f"SWEEP {sweep_direction}: wick={sweep_wick:.5f}")

        # ─── Step 3: Detect Displacement ───
        displacement = self._detect_displacement(
            high, low, close, open_, n, atr, sweep_direction
        )
        disp_strength = displacement["strength"]
        disp_direction = displacement["direction"]

        if disp_strength > 0:
            reasons.append(f"DISPLACEMENT: strength={disp_strength:.2f}, dir={disp_direction}")

        # ─── Step 4: Check Re-acceptance ───
        re_acceptance = False
        if sweep_detected and disp_strength > 0:
            re_acceptance = self._check_re_acceptance(
                high, low, close, n, sweep_result, zones
            )
            if re_acceptance:
                reasons.append("RE-ACCEPTANCE confirmed")

        # ─── Step 5: Session Trap Detection ───
        trap_type = "NONE"
        if session in ("LONDON", "NEW_YORK"):
            trap = self._detect_session_trap(high, low, close, open_, n, atr)
            if trap["detected"]:
                trap_type = "SESSION_TRAP"
                reasons.append(f"SESSION_TRAP: {session} open trap detected")

        # ─── Step 6: News Spike Fake Move ───
        news_spike = self._detect_news_spike(high, low, close, open_, n, atr)
        if news_spike["detected"]:
            trap_type = "NEWS_SPIKE" if trap_type == "NONE" else trap_type
            reasons.append(f"NEWS_SPIKE: wick_ratio={news_spike['wick_ratio']:.2f}")

        # ─── Step 7: Compute Confidence ───
        confidence = 0.0
        if sweep_detected:
            confidence += 0.35
        if disp_strength >= 0.5:
            confidence += 0.25
        elif disp_strength > 0:
            confidence += 0.15
        if re_acceptance:
            confidence += 0.20
        if trap_type != "NONE":
            confidence += 0.10
        if session in ("LONDON", "NEW_YORK", "OVERLAP"):
            confidence += 0.05

        confidence = min(1.0, confidence)

        # ─── Step 8: Entry + Invalidation ───
        entry_price = 0.0
        invalidation_price = 0.0

        if sweep_detected and confidence >= 0.6:
            entry_price = float(close[-1])
            if sweep_direction == "BUY":
                # Invalidation below sweep low
                invalidation_price = float(np.min(low[-5:])) - atr * 0.3
            elif sweep_direction == "SELL":
                # Invalidation above sweep high
                invalidation_price = float(np.max(high[-5:])) + atr * 0.3

        details["sweep_result"] = sweep_result
        details["displacement"] = displacement
        details["re_acceptance"] = re_acceptance
        details["trap_type"] = trap_type

        logger.debug("liquidity_scan_complete", extra={
            "sweep": sweep_detected,
            "direction": sweep_direction,
            "confidence": round(confidence, 3),
            "zones": len(zones),
            "session": session,
        })

        return LiquiditySignal(
            sweep_detected=sweep_detected,
            sweep_direction=sweep_direction,
            displacement_strength=round(disp_strength, 3),
            displacement_direction=disp_direction,
            re_acceptance=re_acceptance,
            liquidity_zones=zones,
            trap_type=trap_type,
            confidence=round(confidence, 3),
            entry_price=entry_price,
            invalidation_price=invalidation_price,
            reasons=reasons,
            details=details,
        )

    # ─── Internal: ATR ───

    def _compute_atr(self, high: np.ndarray, low: np.ndarray,
                     close: np.ndarray, period: int = 14) -> float:
        """Compute latest ATR value."""
        if len(close) < 2:
            return 0.0
        tr1 = high[1:] - low[1:]
        tr2 = np.abs(high[1:] - close[:-1])
        tr3 = np.abs(low[1:] - close[:-1])
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        if len(tr) < period:
            return float(np.mean(tr)) if len(tr) > 0 else 0.0
        # Simple mean of last `period` TR values
        return float(np.mean(tr[-period:]))

    # ─── Internal: Liquidity Zones ───

    def _find_liquidity_zones(
        self, high: np.ndarray, low: np.ndarray,
        close: np.ndarray, open_: np.ndarray,
        n: int, atr: float,
    ) -> list[LiquidityZone]:
        """Find equal highs/lows clusters — liquidity pools."""
        zones: list[LiquidityZone] = []
        tolerance = atr * self.equal_level_tolerance_atr
        lookback = min(self.pool_lookback, n)

        # Scan for equal highs
        highs_window = high[-lookback:]
        for i in range(len(highs_window)):
            ref_price = highs_window[i]
            touches = 0
            touch_indices = []
            for j in range(i + 1, len(highs_window)):
                if abs(highs_window[j] - ref_price) <= tolerance:
                    touches += 1
                    touch_indices.append(n - lookback + j)
            if touches >= EQUAL_LEVEL_MIN_TOUCHES:
                # Check not already covered by existing zone
                already_exists = any(
                    abs(z.price - ref_price) <= tolerance and z.zone_type == "EQUAL_HIGHS"
                    for z in zones
                )
                if not already_exists:
                    zones.append(LiquidityZone(
                        price=float(ref_price),
                        zone_type="EQUAL_HIGHS",
                        touches=touches,
                        bar_indices=touch_indices,
                    ))

        # Scan for equal lows
        lows_window = low[-lookback:]
        for i in range(len(lows_window)):
            ref_price = lows_window[i]
            touches = 0
            touch_indices = []
            for j in range(i + 1, len(lows_window)):
                if abs(lows_window[j] - ref_price) <= tolerance:
                    touches += 1
                    touch_indices.append(n - lookback + j)
            if touches >= EQUAL_LEVEL_MIN_TOUCHES:
                already_exists = any(
                    abs(z.price - ref_price) <= tolerance and z.zone_type == "EQUAL_LOWS"
                    for z in zones
                )
                if not already_exists:
                    zones.append(LiquidityZone(
                        price=float(ref_price),
                        zone_type="EQUAL_LOWS",
                        touches=touches,
                        bar_indices=touch_indices,
                    ))

        # Prior Day High/Low (simple: max/min of last 288 M5 bars ≈ 1 day)
        day_bars = min(288, n - 1)
        if day_bars > 50:
            pdh = float(np.max(high[-(day_bars + 1):-1]))
            pdl = float(np.min(low[-(day_bars + 1):-1]))
            zones.append(LiquidityZone(price=pdh, zone_type="PDH", touches=1))
            zones.append(LiquidityZone(price=pdl, zone_type="PDL", touches=1))

        # Asia Session High/Low (approx bars 0-96 in a 288-bar day window)
        asia_bars = min(96, day_bars)
        if asia_bars > 20 and n > asia_bars:
            asia_high = float(np.max(high[-day_bars:-(day_bars - asia_bars)]))
            asia_low = float(np.min(low[-day_bars:-(day_bars - asia_bars)]))
            zones.append(LiquidityZone(price=asia_high, zone_type="ASIA_HIGH", touches=1))
            zones.append(LiquidityZone(price=asia_low, zone_type="ASIA_LOW", touches=1))

        return zones

    # ─── Internal: Sweep Detection ───

    def _detect_sweep(
        self, high: np.ndarray, low: np.ndarray,
        close: np.ndarray, open_: np.ndarray,
        n: int, atr: float, zones: list[LiquidityZone],
    ) -> dict:
        """
        Detect if last few bars swept a liquidity zone.

        A sweep occurs when:
        1. Price wicks beyond a liquidity level (takes stops)
        2. Then closes back inside the range
        """
        result = {"detected": False, "direction": "NONE", "sweep_wick": 0.0,
                  "swept_zone": None, "sweep_bar": -1}

        for bar_i in range(max(0, n - 5), n):
            bar_high = float(high[bar_i])
            bar_low = float(low[bar_i])
            bar_close = float(close[bar_i])
            bar_open = float(open_[bar_i])

            for zone in zones:
                # Sweep above equal highs → SELL signal (swept buy-side liquidity)
                if zone.zone_type in ("EQUAL_HIGHS", "PDH", "ASIA_HIGH"):
                    wick_above = bar_high - max(bar_close, bar_open)
                    if bar_high > zone.price and bar_close < zone.price:
                        if wick_above >= atr * SWEEP_WICK_MIN_ATR:
                            zone.swept = True
                            result = {
                                "detected": True,
                                "direction": "SELL",
                                "sweep_wick": wick_above,
                                "swept_zone": zone,
                                "sweep_bar": bar_i,
                            }

                # Sweep below equal lows → BUY signal (swept sell-side liquidity)
                if zone.zone_type in ("EQUAL_LOWS", "PDL", "ASIA_LOW"):
                    wick_below = min(bar_close, bar_open) - bar_low
                    if bar_low < zone.price and bar_close > zone.price:
                        if wick_below >= atr * SWEEP_WICK_MIN_ATR:
                            zone.swept = True
                            result = {
                                "detected": True,
                                "direction": "BUY",
                                "sweep_wick": wick_below,
                                "swept_zone": zone,
                                "sweep_bar": bar_i,
                            }

        return result

    # ─── Internal: Displacement Detection ───

    def _detect_displacement(
        self, high: np.ndarray, low: np.ndarray,
        close: np.ndarray, open_: np.ndarray,
        n: int, atr: float, expected_direction: str,
    ) -> dict:
        """
        Detect displacement candle after sweep.

        Displacement = strong directional candle (body >= 0.8 ATR)
        in the opposite direction of the sweep.
        """
        result = {"strength": 0.0, "direction": "NONE", "bar_index": -1}

        for bar_i in range(max(0, n - DISPLACEMENT_MAX_BARS), n):
            body = abs(close[bar_i] - open_[bar_i])
            if body < atr * DISPLACEMENT_BODY_MIN_ATR:
                continue

            is_bullish = close[bar_i] > open_[bar_i]
            is_bearish = close[bar_i] < open_[bar_i]

            if expected_direction == "BUY" and is_bullish:
                strength = min(1.0, body / (atr * 1.5))
                if strength > result["strength"]:
                    result = {"strength": round(strength, 3), "direction": "BUY", "bar_index": bar_i}

            elif expected_direction == "SELL" and is_bearish:
                strength = min(1.0, body / (atr * 1.5))
                if strength > result["strength"]:
                    result = {"strength": round(strength, 3), "direction": "SELL", "bar_index": bar_i}

        return result

    # ─── Internal: Re-acceptance Check ───

    def _check_re_acceptance(
        self, high: np.ndarray, low: np.ndarray,
        close: np.ndarray, n: int,
        sweep_result: dict, zones: list[LiquidityZone],
    ) -> bool:
        """
        Check if price has re-accepted back into the prior range
        after the sweep and displacement.
        """
        swept_zone = sweep_result.get("swept_zone")
        if swept_zone is None:
            return False

        # Price should be back inside the zone
        last_close = float(close[-1])

        if sweep_result["direction"] == "SELL":
            # After sweeping highs, price should close below the zone
            return last_close < swept_zone.price
        elif sweep_result["direction"] == "BUY":
            # After sweeping lows, price should close above the zone
            return last_close > swept_zone.price

        return False

    # ─── Internal: Session Trap ───

    def _detect_session_trap(
        self, high: np.ndarray, low: np.ndarray,
        close: np.ndarray, open_: np.ndarray,
        n: int, atr: float,
    ) -> dict:
        """
        Detect session open trap — spike + snapback within 3 bars.

        Common at London/NY open: initial spike in one direction
        then sharp reversal.
        """
        if n < SESSION_TRAP_MAX_BARS + 2:
            return {"detected": False}

        # Check last SESSION_TRAP_MAX_BARS bars
        check_start = max(0, n - SESSION_TRAP_MAX_BARS)

        # Look for spike bar followed by reversal
        for i in range(check_start, n - 1):
            bar_range = high[i] - low[i]
            body = abs(close[i] - open_[i])
            if bar_range <= 0:
                continue

            wick_pct = (bar_range - body) / bar_range

            # Spike bar: large range + big wick
            if bar_range > atr * 1.2 and wick_pct > 0.5:
                # Next bar should reverse
                next_i = i + 1
                if next_i < n:
                    # Bullish spike then bearish reversal
                    if (high[i] > high[i - 1] and close[next_i] < open_[i]):
                        return {"detected": True, "type": "BULL_TRAP", "bar": i}
                    # Bearish spike then bullish reversal
                    if (low[i] < low[i - 1] and close[next_i] > open_[i]):
                        return {"detected": True, "type": "BEAR_TRAP", "bar": i}

        return {"detected": False}

    # ─── Internal: News Spike ───

    def _detect_news_spike(
        self, high: np.ndarray, low: np.ndarray,
        close: np.ndarray, open_: np.ndarray,
        n: int, atr: float,
    ) -> dict:
        """
        Detect news spike fake move.

        Pattern: Large wick (>1.5x ATR) + full body reversal next bar.
        """
        if n < 3:
            return {"detected": False, "wick_ratio": 0.0}

        for i in range(max(0, n - 3), n):
            bar_range = high[i] - low[i]
            if bar_range <= 0:
                continue

            upper_wick = high[i] - max(close[i], open_[i])
            lower_wick = min(close[i], open_[i]) - low[i]
            max_wick = max(upper_wick, lower_wick)
            wick_ratio = max_wick / bar_range

            if max_wick > atr * NEWS_SPIKE_ATR_MULT and wick_ratio > NEWS_SPIKE_WICK_PCT:
                return {"detected": True, "wick_ratio": round(wick_ratio, 3), "bar": i}

        return {"detected": False, "wick_ratio": 0.0}
