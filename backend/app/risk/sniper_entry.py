"""
Sniper Entry Filter — Precision Entry Quality Check.

ตรวจคุณภาพจุดเข้าก่อนส่ง order:
    1. Pullback Quality  — ไม่ไล่ราคา, เข้าใกล้ EMA
    2. Candle Confirm     — มี pattern ยืนยัน (engulfing, pin bar, hammer)
    3. Volume Surge       — volume สูงกว่าค่าเฉลี่ย
    4. Key Level          — เข้าใกล้ support/resistance

Score 0-100, min 55 to pass.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

from app.core.logging import get_logger
from app.domain.enums import Action

logger = get_logger(__name__)


@dataclass
class SniperResult:
    """Result of sniper entry precision check."""
    score: int = 0
    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


class SniperEntryFilter:
    """
    4-Check Entry Precision Filter.
    
    ตรวจคุณภาพจุดเข้า — ถ้า score < min_score → block entry.
    """

    def __init__(self, min_score: int = 55):
        self.min_score = min_score

    def check_precision(
        self,
        candles: pd.DataFrame,
        action: Action,
        atr: float | None = None,
        power_verdict: str = "NEUTRAL",
        power_score: int = 0,
    ) -> SniperResult:
        """
        Run all 4 precision checks.
        
        Args:
            candles: OHLCV DataFrame (M5)
            action: BUY or SELL
            atr: ATR value (optional, will calculate if not provided)
        
        Returns:
            SniperResult with score, pass/fail, and reasons
        """
        if candles is None or len(candles) < 30:
            return SniperResult(score=0, passed=False, reasons=["insufficient_data_blocked"])

        try:
            close = candles["close"].astype(float)
            high = candles["high"].astype(float)
            low = candles["low"].astype(float)
            open_ = candles["open"].astype(float)
            
            c = float(close.iloc[-1])
            h = float(high.iloc[-1])
            lo = float(low.iloc[-1])
            o = float(open_.iloc[-1])
            
            # Previous candle
            pc = float(close.iloc[-2])
            po = float(open_.iloc[-2])
            ph = float(high.iloc[-2])
            pl = float(low.iloc[-2])

            # Calculate ATR if not provided
            if atr is None or atr <= 0:
                tr = np.maximum(
                    high.values[1:] - low.values[1:],
                    np.maximum(
                        np.abs(high.values[1:] - close.values[:-1]),
                        np.abs(low.values[1:] - close.values[:-1])
                    )
                )
                atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.mean(tr))

            is_buy = action == Action.BUY

            score = 0
            reasons = []

            # ─── Check 1: Pullback Quality (0-30 pts) ───
            s1, r1 = self._check_pullback(close, c, atr, is_buy)
            score += s1
            reasons.extend(r1)

            # ─── Check 2: Candle Confirmation (0-25 pts) ───
            s2, r2 = self._check_candle(c, o, h, lo, pc, po, ph, pl, atr, is_buy)
            score += s2
            reasons.extend(r2)

            # ─── Check 3: Volume Surge (0-25 pts) ───
            s3, r3 = self._check_volume(candles)
            score += s3
            reasons.extend(r3)

            # ─── Check 4: Key Level Proximity (0-20 pts) ───
            s4, r4 = self._check_key_level(candles, c, atr, is_buy)
            score += s4
            reasons.extend(r4)

            # ─── Check 5: Bull/Bear Power Alignment (-10 to 25 pts) ───
            s5, r5 = self._check_power_alignment(is_buy, power_verdict, power_score)
            score += s5
            reasons.extend(r5)

            passed = score >= self.min_score

            result = SniperResult(
                score=score,
                passed=passed,
                reasons=reasons,
                details={
                    "pullback": s1,
                    "candle": s2,
                    "volume": s3,
                    "key_level": s4,
                    "power_alignment": s5,
                    "threshold": self.min_score,
                },
            )

            logger.info("sniper_entry_check", extra={
                "action": action.value,
                "score": score,
                "passed": passed,
                "pullback": s1,
                "candle": s2,
                "volume": s3,
                "key_level": s4,
                "power_alignment": s5,
            })

            return result

        except Exception as e:
            logger.error("sniper_entry_error", extra={"error": str(e)})
            return SniperResult(score=0, passed=False, reasons=[f"error_blocked:{e}"])

    # ─────────────────────────────────────────────
    # Check 1: Pullback Quality (0-30 pts)
    # ─────────────────────────────────────────────
    def _check_pullback(
        self,
        close: pd.Series,
        current: float,
        atr: float,
        is_buy: bool,
    ) -> tuple[int, list[str]]:
        """
        ไม่ไล่ราคา — เข้าเมื่อราคาย่อตัวมาใกล้ EMA.
        
        BUY:  ราคาควรอยู่ใกล้ EMA21 (ไม่ห่างเกิน 1 ATR)
        SELL: ราคาควรอยู่ใกล้ EMA21 (ไม่ห่างเกิน 1 ATR)
        """
        pts = 0
        reasons = []

        ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]

        dist_to_ema21 = abs(current - ema21) / max(atr, 1e-10)

        if dist_to_ema21 < 0.3:
            # ราคาอยู่ตรง EMA → สมบูรณ์แบบ
            pts = 30
            reasons.append("pullback:perfect_at_ema")
        elif dist_to_ema21 < 0.7:
            pts = 20
            reasons.append("pullback:near_ema")
        elif dist_to_ema21 < 1.0:
            pts = 10
            reasons.append("pullback:ok_distance")
        elif dist_to_ema21 < 1.5:
            pts = 5
            reasons.append("pullback:extended")
        else:
            pts = 0
            reasons.append("pullback:chasing")

        # Bonus: price bouncing off EMA (crossed from below/above)
        if is_buy and current > ema21 and float(close.iloc[-2]) <= ema21:
            pts = min(pts + 10, 30)
            reasons.append("pullback:bounce_up")
        elif not is_buy and current < ema21 and float(close.iloc[-2]) >= ema21:
            pts = min(pts + 10, 30)
            reasons.append("pullback:bounce_down")

        return pts, reasons

    # ─────────────────────────────────────────────
    # Check 2: Candle Confirmation (0-25 pts)
    # ─────────────────────────────────────────────
    def _check_candle(
        self,
        c: float, o: float, h: float, lo: float,
        pc: float, po: float, ph: float, pl: float,
        atr: float, is_buy: bool,
    ) -> tuple[int, list[str]]:
        """
        ตรวจ candlestick pattern ยืนยัน:
        - Engulfing
        - Pin Bar / Hammer
        - Strong body (momentum candle)
        """
        pts = 0
        reasons = []
        body = abs(c - o)
        prev_body = abs(pc - po)
        candle_range = h - lo
        
        if candle_range < 1e-10:
            return 5, ["candle:doji_neutral"]

        body_ratio = body / candle_range

        # ── Engulfing ──
        if is_buy:
            # Bullish engulfing: current body engulfs previous
            if c > o and body > prev_body and c > ph and lo < pl:
                pts += 20
                reasons.append("candle:bull_engulf")
            elif c > o and body > prev_body * 0.8:
                pts += 10
                reasons.append("candle:bull_engulf_partial")
        else:
            # Bearish engulfing
            if c < o and body > prev_body and lo < pl and h > ph:
                pts += 20
                reasons.append("candle:bear_engulf")
            elif c < o and body > prev_body * 0.8:
                pts += 10
                reasons.append("candle:bear_engulf_partial")

        # ── Pin Bar / Hammer ──
        if is_buy:
            lower_wick = min(c, o) - lo
            if lower_wick > body * 2 and lower_wick > candle_range * 0.5:
                pts += 15
                reasons.append("candle:hammer")
        else:
            upper_wick = h - max(c, o)
            if upper_wick > body * 2 and upper_wick > candle_range * 0.5:
                pts += 15
                reasons.append("candle:shooting_star")

        # ── Strong Momentum Candle ──
        if body_ratio > 0.7 and body > atr * 0.3:
            if (is_buy and c > o) or (not is_buy and c < o):
                pts += 10
                reasons.append("candle:strong_momentum")

        # ── Baseline: at least a decent body ──
        if pts == 0:
            if body_ratio > 0.4:
                pts = 5
                reasons.append("candle:decent_body")
            else:
                reasons.append("candle:weak")

        return min(pts, 25), reasons

    # ─────────────────────────────────────────────
    # Check 3: Volume Surge (0-25 pts)
    # ─────────────────────────────────────────────
    def _check_volume(self, candles: pd.DataFrame) -> tuple[int, list[str]]:
        """
        Volume สูงกว่าค่าเฉลี่ย = ยืนยันว่าเคลื่อนไหวจริง.
        """
        pts = 0
        reasons = []

        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        if vol_col not in candles.columns:
            return 10, ["volume:no_data_neutral"]

        vol = candles[vol_col].astype(float)
        if len(vol) < 20:
            return 10, ["volume:insufficient_data"]

        current_vol = float(vol.iloc[-1])
        avg_vol = float(vol.iloc[-20:].mean())

        if avg_vol <= 0:
            return 10, ["volume:zero_avg"]

        ratio = current_vol / avg_vol

        if ratio >= 2.0:
            pts = 25
            reasons.append(f"volume:surge_{ratio:.1f}x")
        elif ratio >= 1.5:
            pts = 20
            reasons.append(f"volume:high_{ratio:.1f}x")
        elif ratio >= 1.2:
            pts = 15
            reasons.append(f"volume:above_avg_{ratio:.1f}x")
        elif ratio >= 0.8:
            pts = 8
            reasons.append(f"volume:normal_{ratio:.1f}x")
        else:
            pts = 0
            reasons.append(f"volume:low_{ratio:.1f}x")

        return pts, reasons

    # ─────────────────────────────────────────────
    # Check 4: Key Level Proximity (0-20 pts)
    # ─────────────────────────────────────────────
    def _check_key_level(
        self,
        candles: pd.DataFrame,
        current: float,
        atr: float,
        is_buy: bool,
    ) -> tuple[int, list[str]]:
        """
        ตรวจว่าราคาอยู่ใกล้ support/resistance ไหม.
        
        BUY:  ดีถ้าอยู่ใกล้ support (swing low)
        SELL: ดีถ้าอยู่ใกล้ resistance (swing high)
        """
        pts = 0
        reasons = []

        # Use last 50 bars for swing levels
        lookback = min(50, len(candles))
        recent = candles.iloc[-lookback:]
        
        swing_high = float(recent["high"].max())
        swing_low = float(recent["low"].min())
        
        # Find recent swing levels using rolling window
        high = recent["high"].astype(float)
        low = recent["low"].astype(float)
        
        # Local support/resistance (rolling 10-bar extremes)
        if len(high) >= 10:
            local_highs = high.rolling(10).max().dropna()
            local_lows = low.rolling(10).min().dropna()
            
            # Most recent local levels
            recent_resistance = float(local_highs.iloc[-1]) if len(local_highs) > 0 else swing_high
            recent_support = float(local_lows.iloc[-1]) if len(local_lows) > 0 else swing_low
        else:
            recent_resistance = swing_high
            recent_support = swing_low

        if is_buy:
            # BUY near support is good
            dist_to_support = abs(current - recent_support) / max(atr, 1e-10)
            if dist_to_support < 0.5:
                pts = 20
                reasons.append("level:at_support")
            elif dist_to_support < 1.0:
                pts = 12
                reasons.append("level:near_support")
            elif dist_to_support < 2.0:
                pts = 5
                reasons.append("level:away_from_support")
            else:
                pts = 0
                reasons.append("level:no_support_nearby")
        else:
            # SELL near resistance is good
            dist_to_resistance = abs(current - recent_resistance) / max(atr, 1e-10)
            if dist_to_resistance < 0.5:
                pts = 20
                reasons.append("level:at_resistance")
            elif dist_to_resistance < 1.0:
                pts = 12
                reasons.append("level:near_resistance")
            elif dist_to_resistance < 2.0:
                pts = 5
                reasons.append("level:away_from_resistance")
            else:
                pts = 0
                reasons.append("level:no_resistance_nearby")

        return pts, reasons
        
    # ─────────────────────────────────────────────
    # Check 5: Bull/Bear Power Alignment (-10 to 25 pts)
    # ─────────────────────────────────────────────
    def _check_power_alignment(
        self,
        is_buy: bool,
        verdict: str,
        power_score: int,
    ) -> tuple[int, list[str]]:
        """
        ตรวจสอบความสอดคล้องของทิศทางการเทรดกับ Bull/Bear Power
        """
        pts = 0
        reasons = []

        if is_buy:
            if verdict == "STRONG_BULL":
                pts = 25
                reasons.append(f"power:strong_aligned_bull({power_score})")
            elif verdict == "BULL":
                pts = 15
                reasons.append(f"power:aligned_bull({power_score})")
            elif verdict == "NEUTRAL":
                pts = 5
                reasons.append(f"power:neutral({power_score})")
            elif verdict == "BEAR":
                pts = 0
                reasons.append(f"power:against_bear({power_score})")
            elif verdict == "STRONG_BEAR":
                pts = -10
                reasons.append(f"power:strongly_against_bear({power_score})")
        else:
            if verdict == "STRONG_BEAR":
                pts = 25
                reasons.append(f"power:strong_aligned_bear({power_score})")
            elif verdict == "BEAR":
                pts = 15
                reasons.append(f"power:aligned_bear({power_score})")
            elif verdict == "NEUTRAL":
                pts = 5
                reasons.append(f"power:neutral({power_score})")
            elif verdict == "BULL":
                pts = 0
                reasons.append(f"power:against_bull({power_score})")
            elif verdict == "STRONG_BULL":
                pts = -10
                reasons.append(f"power:strongly_against_bull({power_score})")
                
        return pts, reasons
