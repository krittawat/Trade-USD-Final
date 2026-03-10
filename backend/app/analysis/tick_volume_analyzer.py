"""
Tick Volume + OHLC Microstructure Analyzer — Pre-Trade Advisory Layer.

วิเคราะห์ tick volume ร่วมกับ OHLCV data ก่อนเข้าเทรด:
    1. Volume Trend       — EMA(vol,10) vs EMA(vol,30)
    2. Volume Climax      — vol > 3x avg + long wick → exhaustion
    3. Volume Dry-up      — vol < 0.3x avg → no participation
    4. Buying/Selling Pressure — (close-low)/(high-low)
    5. Body Conviction    — |close-open|/(high-low)
    6. Accumulation/Distribution — CLV * volume rolling sum
    7. Volume-Price Divergence — new high but vol declining

กฎ:
    - Advisory เท่านั้น — ไม่ override Risk Engine
    - Fail-safe: ถ้า data ไม่พอ → is_valid=False, score=0
    - ไม่เก็บ state ข้ามรอบ — stateless per call
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.analysis.bull_bear_power import BullBearPowerEngine

logger = get_logger(__name__)


# ═══════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════

MIN_BARS = 35          # Minimum candles required for analysis
VOL_EMA_FAST = 10      # Fast volume EMA
VOL_EMA_SLOW = 30      # Slow volume EMA
VOL_AVG_PERIOD = 20    # Average volume period
CLIMAX_MULT = 3.0      # Volume > 3x avg = climax
DRYUP_MULT = 0.3       # Volume < 0.3x avg = dry-up
BODY_CONVICTION_STRONG = 0.60   # Body > 60% of range = strong conviction
PRESSURE_LOOKBACK = 5  # Bars for pressure averaging
AD_LOOKBACK = 20       # Bars for AD line trend
DIVERGENCE_LOOKBACK = 10  # Bars for divergence check


# ═══════════════════════════════════════════════
# Signal Dataclass
# ═══════════════════════════════════════════════

@dataclass
class TickVolumeSignal:
    """Result of tick volume + OHLC microstructure analysis."""
    is_valid: bool = False          # Enough data?
    score: int = 0                  # -20 to +20 (negative = caution, positive = boost)
    current_volume: float = 0.0     # Metric: current candle volume
    volume_trend: str = "FLAT"      # "RISING", "FALLING", "FLAT"
    buying_pressure: float = 0.5    # 0.0 - 1.0
    selling_pressure: float = 0.5   # 0.0 - 1.0
    is_climax: bool = False         # Volume exhaustion
    is_dryup: bool = False          # No participation
    has_divergence: bool = False    # Price-volume divergence
    body_conviction: float = 0.0    # 0.0 - 1.0 (body/range ratio)
    ad_line_trend: str = "NEUTRAL"  # "BULLISH", "BEARISH", "NEUTRAL"
    reasons: list[str] = field(default_factory=list)
    
    # ─── Bull/Bear Power ───
    bull_power: float = 0.0
    bear_power: float = 0.0
    net_power: float = 0.0
    power_score: int = 0
    power_verdict: str = "NEUTRAL"



# ═══════════════════════════════════════════════
# Analyzer
# ═══════════════════════════════════════════════

class TickVolumeAnalyzer:
    """
    Stateless analyzer — computes microstructure signals from OHLCV candles.

    Usage:
        analyzer = TickVolumeAnalyzer()
        signal = analyzer.analyze(candles)
        # signal.score  →  -20 to +20
        # signal.is_climax  →  should consider blocking entry
    """

    def __init__(self):
        self._bb_engine = BullBearPowerEngine()

    def analyze(self, candles: pd.DataFrame) -> TickVolumeSignal:
        """
        Analyze OHLCV candles for tick volume microstructure signals.

        Args:
            candles: DataFrame with columns [open, high, low, close, tick_volume/volume]
                     Must have at least MIN_BARS rows.

        Returns:
            TickVolumeSignal with all computed metrics.
        """
        signal = TickVolumeSignal()

        # ─── Validate input ───
        if candles is None or len(candles) < MIN_BARS:
            signal.reasons.append(
                f"Insufficient data ({len(candles) if candles is not None else 0} < {MIN_BARS})"
            )
            return signal

        # ─── Extract arrays ───
        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)

        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        if vol_col not in candles.columns:
            signal.reasons.append("No volume column found")
            return signal

        vol = candles[vol_col].astype(float)

        signal.is_valid = True
        signal.current_volume = float(vol.iloc[-1])
        score = 0
        reasons: list[str] = []

        # ═══════════════════════════════════════
        # 1. Volume Trend (EMA fast vs slow)
        # ═══════════════════════════════════════
        vol_ema_fast = vol.ewm(span=VOL_EMA_FAST, adjust=False).mean()
        vol_ema_slow = vol.ewm(span=VOL_EMA_SLOW, adjust=False).mean()

        fast_val = float(vol_ema_fast.iloc[-1])
        slow_val = float(vol_ema_slow.iloc[-1])

        if slow_val > 0:
            ratio = fast_val / slow_val
            if ratio > 1.15:
                signal.volume_trend = "RISING"
                score += 3
                reasons.append(f"VolTrend RISING ({ratio:.2f}x)")
            elif ratio < 0.85:
                signal.volume_trend = "FALLING"
                score -= 2
                reasons.append(f"VolTrend FALLING ({ratio:.2f}x)")
            else:
                signal.volume_trend = "FLAT"

        # ═══════════════════════════════════════
        # 2. Volume Climax Detection
        # ═══════════════════════════════════════
        vol_avg = float(vol.rolling(VOL_AVG_PERIOD).mean().iloc[-1])
        current_vol = float(vol.iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])
        current_close = float(close.iloc[-1])
        current_open = float(open_.iloc[-1])

        total_range = current_high - current_low
        body = abs(current_close - current_open)

        if vol_avg > 0 and current_vol > vol_avg * CLIMAX_MULT:
            # Climax = high volume + long wicks (uncertainty)
            if total_range > 0:
                wick_ratio = 1.0 - (body / total_range)
                if wick_ratio > 0.5:  # More than 50% wicks
                    signal.is_climax = True
                    score -= 10
                    reasons.append(
                        f"CLIMAX: vol {current_vol/vol_avg:.1f}x avg, "
                        f"wick {wick_ratio:.0%} (exhaustion risk)"
                    )
                else:
                    # High volume with strong body = momentum, not climax
                    score += 3
                    reasons.append(
                        f"HighVol momentum: {current_vol/vol_avg:.1f}x avg, "
                        f"strong body"
                    )

        # ═══════════════════════════════════════
        # 3. Volume Dry-up Detection
        # ═══════════════════════════════════════
        if vol_avg > 0 and current_vol < vol_avg * DRYUP_MULT:
            signal.is_dryup = True
            score -= 5
            reasons.append(
                f"DRY-UP: vol {current_vol/vol_avg:.2f}x avg (no participation)"
            )

        # ═══════════════════════════════════════
        # 4. Buying / Selling Pressure
        # ═══════════════════════════════════════
        buying_pressures = []
        selling_pressures = []
        lookback = min(PRESSURE_LOOKBACK, len(candles))

        for i in range(-lookback, 0):
            h = float(high.iloc[i])
            l = float(low.iloc[i])
            c = float(close.iloc[i])
            rng = h - l
            if rng > 0:
                bp = (c - l) / rng
                sp = (h - c) / rng
                buying_pressures.append(bp)
                selling_pressures.append(sp)

        if buying_pressures:
            signal.buying_pressure = round(
                float(np.mean(buying_pressures)), 3
            )
            signal.selling_pressure = round(
                float(np.mean(selling_pressures)), 3
            )

            # Strong buying pressure
            if signal.buying_pressure > 0.65:
                score += 3
                reasons.append(f"BuyPressure {signal.buying_pressure:.0%}")
            elif signal.selling_pressure > 0.65:
                score += 3
                reasons.append(f"SellPressure {signal.selling_pressure:.0%}")

        # ═══════════════════════════════════════
        # 5. Body Conviction (OHLC Spread)
        # ═══════════════════════════════════════
        if total_range > 0:
            signal.body_conviction = round(body / total_range, 3)

            if signal.body_conviction > BODY_CONVICTION_STRONG:
                score += 3
                reasons.append(
                    f"BodyConviction {signal.body_conviction:.0%} (decisive)"
                )
            elif signal.body_conviction < 0.20:
                score -= 2
                reasons.append(
                    f"BodyConviction {signal.body_conviction:.0%} (doji/indecision)"
                )

        # ═══════════════════════════════════════
        # 6. Accumulation/Distribution Line
        # ═══════════════════════════════════════
        rng_series = high - low
        # CLV = ((close - low) - (high - close)) / (high - low)
        # Avoid division by zero
        safe_rng = rng_series.replace(0, np.nan)
        clv = ((close - low) - (high - close)) / safe_rng
        clv = clv.fillna(0.0)
        ad_flow = (clv * vol).fillna(0.0)

        # Rolling AD line over lookback
        ad_sum = ad_flow.rolling(AD_LOOKBACK).sum()
        if len(ad_sum) >= 5:
            ad_recent = ad_sum.iloc[-5:]
            ad_start = float(ad_recent.iloc[0])
            ad_end = float(ad_recent.iloc[-1])

            if not (pd.isna(ad_start) or pd.isna(ad_end)):
                ad_diff = ad_end - ad_start
                # Normalize by average volume to make threshold meaningful
                if vol_avg > 0:
                    ad_normalized = ad_diff / (vol_avg * 5)
                    if ad_normalized > 0.3:
                        signal.ad_line_trend = "BULLISH"
                        score += 2
                        reasons.append(f"AD Line BULLISH ({ad_normalized:.2f})")
                    elif ad_normalized < -0.3:
                        signal.ad_line_trend = "BEARISH"
                        score += 2
                        reasons.append(f"AD Line BEARISH ({ad_normalized:.2f})")
                    else:
                        signal.ad_line_trend = "NEUTRAL"

        # ═══════════════════════════════════════
        # 7. Volume-Price Divergence
        # ═══════════════════════════════════════
        div_lookback = min(DIVERGENCE_LOOKBACK, len(candles) - 1)
        if div_lookback >= 5:
            recent_close = close.iloc[-div_lookback:]
            recent_vol = vol.iloc[-div_lookback:]

            price_making_high = float(close.iloc[-1]) >= float(
                recent_close.iloc[:-1].max()
            )
            price_making_low = float(close.iloc[-1]) <= float(
                recent_close.iloc[:-1].min()
            )

            # Volume trend over same period
            vol_first_half = float(recent_vol.iloc[:div_lookback // 2].mean())
            vol_second_half = float(recent_vol.iloc[div_lookback // 2:].mean())

            if vol_first_half > 0:
                vol_change = vol_second_half / vol_first_half

                # Price new high but volume declining → bearish divergence
                if price_making_high and vol_change < 0.7:
                    signal.has_divergence = True
                    score -= 5
                    reasons.append(
                        f"DIVERGENCE: price new high, vol declining "
                        f"({vol_change:.2f}x)"
                    )

                # Price new low but volume declining → bullish divergence
                elif price_making_low and vol_change < 0.7:
                    signal.has_divergence = True
                    score -= 5
                    reasons.append(
                        f"DIVERGENCE: price new low, vol declining "
                        f"({vol_change:.2f}x)"
                    )

        # ═══════════════════════════════════════
        # Final Score (clamp -20 to +20)
        # ═══════════════════════════════════════
        signal.score = max(-20, min(20, score))
        signal.reasons = reasons

        # ═══════════════════════════════════════
        # 8. Bull/Bear Power Analysis
        # ═══════════════════════════════════════
        try:
            bb_result = self._bb_engine.analyze(candles)
            if bb_result.is_valid:
                signal.bull_power = bb_result.bull_power
                signal.bear_power = bb_result.bear_power
                signal.net_power = bb_result.net_power
                signal.power_score = bb_result.score
                signal.power_verdict = bb_result.verdict
                if bb_result.reasons:
                    signal.reasons.extend(bb_result.reasons)
        except Exception as e:
            logger.debug("bull_bear_engine_error", extra={"error": str(e)})

        logger.debug("tick_volume_analysis", extra={
            "score": signal.score,
            "volume_trend": signal.volume_trend,
            "is_climax": signal.is_climax,
            "is_dryup": signal.is_dryup,
            "has_divergence": signal.has_divergence,
            "buying_pressure": signal.buying_pressure,
            "body_conviction": signal.body_conviction,
            "ad_line_trend": signal.ad_line_trend,
            "reasons_count": len(reasons),
        })

        return signal
