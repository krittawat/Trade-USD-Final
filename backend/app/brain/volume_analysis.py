"""
Volume Analysis Module — Tick Volume Intelligence.

3 core analyses:
    1. Volume Spike Detection — ยืนยัน momentum (volume > 1.5x avg)
    2. Low Volume Fakeout     — block breakout ที่ volume ต่ำ
    3. Delta Volume          — วัด buying/selling pressure

ใช้ใน strategy หลัก: gold_evolution, silver_evolution, gold_elite, silver_elite, btc_elite
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class VolumeSignal:
    """Result of volume analysis."""
    # Scores (for strategy integration)
    buy_score: int = 0
    sell_score: int = 0
    buy_reasons: list[str] = field(default_factory=list)
    sell_reasons: list[str] = field(default_factory=list)
    
    # Filters
    is_fakeout: bool = False
    fakeout_reason: str = ""
    
    # Raw data
    volume_ratio: float = 1.0      # current_vol / avg_vol
    delta_volume: float = 0.0      # cumulative delta
    delta_direction: str = "NEUTRAL"  # BUY_PRESSURE / SELL_PRESSURE / NEUTRAL
    pressure_strength: float = 0.0    # 0.0-1.0


class VolumeAnalyzer:
    """
    Tick Volume Analyzer — 3 analyses for trade quality.
    
    Usage in strategy:
        vol = VolumeAnalyzer()
        signal = vol.analyze(candles)
        # Use signal.buy_score / sell_score in total scoring
        # Use signal.is_fakeout to block weak entries
    """

    def __init__(
        self,
        spike_threshold: float = 1.5,     # Volume must be > 1.5x avg for spike
        fakeout_threshold: float = 0.6,   # Volume < 0.6x avg = potential fakeout
        delta_lookback: int = 10,          # Bars to compute delta volume
        avg_period: int = 20,              # Period for average volume
    ):
        self.spike_threshold = spike_threshold
        self.fakeout_threshold = fakeout_threshold
        self.delta_lookback = delta_lookback
        self.avg_period = avg_period

    def analyze(self, candles: pd.DataFrame) -> VolumeSignal:
        """
        Run all 3 volume analyses on OHLCV candles.
        
        Args:
            candles: DataFrame with columns: open, high, low, close, tick_volume/volume
            
        Returns:
            VolumeSignal with scores, fakeout flag, and delta info
        """
        result = VolumeSignal()
        
        if candles is None or len(candles) < self.avg_period + 5:
            return result
            
        try:
            # Detect volume column
            vol_col = self._get_vol_col(candles)
            if vol_col is None:
                return result
            
            vol = candles[vol_col].astype(float)
            close = candles["close"].astype(float)
            open_ = candles["open"].astype(float)
            
            # ── 1. Volume Spike Detection ──
            s1_buy, s1_sell, r1_buy, r1_sell, ratio = self._check_spike(vol, close, open_)
            result.buy_score += s1_buy
            result.sell_score += s1_sell
            result.buy_reasons.extend(r1_buy)
            result.sell_reasons.extend(r1_sell)
            result.volume_ratio = ratio
            
            # ── 2. Low Volume Fakeout Detection ──
            is_fakeout, fakeout_reason = self._check_fakeout(vol, close, open_)
            result.is_fakeout = is_fakeout
            result.fakeout_reason = fakeout_reason
            if is_fakeout:
                result.buy_reasons.append(fakeout_reason)
                result.sell_reasons.append(fakeout_reason)
            
            # ── 3. Delta Volume Analysis ──
            delta, direction, strength = self._check_delta(vol, close, open_)
            result.delta_volume = delta
            result.delta_direction = direction
            result.pressure_strength = strength
            
            # Delta scoring
            s3_buy, s3_sell, r3_buy, r3_sell = self._delta_score(direction, strength)
            result.buy_score += s3_buy
            result.sell_score += s3_sell
            result.buy_reasons.extend(r3_buy)
            result.sell_reasons.extend(r3_sell)
            
        except Exception as e:
            logger.debug("volume_analysis_error", extra={"error": str(e)})
            
        return result

    # ─────────────────────────────────────────────
    # Analysis 1: Volume Spike (0-10 pts)
    # ─────────────────────────────────────────────
    def _check_spike(
        self,
        vol: pd.Series,
        close: pd.Series,
        open_: pd.Series,
    ) -> tuple[int, int, list[str], list[str], float]:
        """
        Volume spike = ยืนยันว่า momentum จริง.
        
        - Bullish candle + volume spike → BUY score
        - Bearish candle + volume spike → SELL score
        """
        avg_vol = float(vol.iloc[-self.avg_period:].mean())
        if avg_vol <= 0:
            return 0, 0, [], [], 1.0
            
        curr_vol = float(vol.iloc[-1])
        ratio = curr_vol / avg_vol
        
        buy_pts, sell_pts = 0, 0
        buy_r, sell_r = [], []
        
        c = float(close.iloc[-1])
        o = float(open_.iloc[-1])
        is_bull = c > o
        
        if ratio >= 2.0:
            # Major spike
            if is_bull:
                buy_pts = 10
                buy_r.append(f"VOL:spike_{ratio:.1f}x_bull")
            else:
                sell_pts = 10
                sell_r.append(f"VOL:spike_{ratio:.1f}x_bear")
        elif ratio >= self.spike_threshold:
            # Normal spike
            if is_bull:
                buy_pts = 7
                buy_r.append(f"VOL:above_avg_{ratio:.1f}x_bull")
            else:
                sell_pts = 7
                sell_r.append(f"VOL:above_avg_{ratio:.1f}x_bear")
        elif ratio >= 1.2:
            # Slightly above average
            if is_bull:
                buy_pts = 3
                buy_r.append(f"VOL:ok_{ratio:.1f}x")
            else:
                sell_pts = 3
                sell_r.append(f"VOL:ok_{ratio:.1f}x")
        
        return buy_pts, sell_pts, buy_r, sell_r, ratio

    # ─────────────────────────────────────────────
    # Analysis 2: Low Volume Fakeout (filter)
    # ─────────────────────────────────────────────
    def _check_fakeout(
        self,
        vol: pd.Series,
        close: pd.Series,
        open_: pd.Series,
    ) -> tuple[bool, str]:
        """
        breakout + low volume = potential fakeout.
        
        Detects:
            - Price making new high/low in last 10 bars
            - But volume is below average → likely fakeout
        """
        avg_vol = float(vol.iloc[-self.avg_period:].mean())
        if avg_vol <= 0:
            return False, ""
            
        curr_vol = float(vol.iloc[-1])
        ratio = curr_vol / avg_vol
        
        if ratio >= self.fakeout_threshold:
            return False, ""  # Volume is adequate
            
        # Check if price is breaking out (new high/low in last 10 bars)
        c = float(close.iloc[-1])
        recent_high = float(close.iloc[-10:].max())
        recent_low = float(close.iloc[-10:].min())
        
        is_breakout_high = c >= recent_high * 0.999  # Within 0.1% of high
        is_breakout_low = c <= recent_low * 1.001     # Within 0.1% of low
        
        if is_breakout_high or is_breakout_low:
            direction = "high" if is_breakout_high else "low"
            reason = f"VOL:fakeout_{direction}_vol_{ratio:.2f}x"
            return True, reason
            
        return False, ""

    # ─────────────────────────────────────────────
    # Analysis 3: Delta Volume (buying/selling pressure)
    # ─────────────────────────────────────────────
    def _check_delta(
        self,
        vol: pd.Series,
        close: pd.Series,
        open_: pd.Series,
    ) -> tuple[float, str, float]:
        """
        Delta Volume = sum of (vol × direction) over lookback.
        
        Bullish bar → +volume
        Bearish bar → -volume
        
        Returns:
            (cumulative_delta, direction_string, strength_0_to_1)
        """
        lookback = min(self.delta_lookback, len(vol) - 1)
        if lookback < 3:
            return 0.0, "NEUTRAL", 0.0
            
        recent_close = close.iloc[-lookback:].values
        recent_open = open_.iloc[-lookback:].values
        recent_vol = vol.iloc[-lookback:].values
        
        # Delta: +vol for bullish bars, -vol for bearish
        deltas = np.where(recent_close > recent_open, recent_vol, -recent_vol)
        # Doji/equal bars get 0
        deltas = np.where(recent_close == recent_open, 0, deltas)
        
        cum_delta = float(np.sum(deltas))
        total_vol = float(np.sum(np.abs(recent_vol)))
        
        if total_vol <= 0:
            return 0.0, "NEUTRAL", 0.0
            
        # Normalize: what % of volume went to buy vs sell
        strength = abs(cum_delta) / total_vol  # 0.0 to 1.0
        
        if strength < 0.15:
            direction = "NEUTRAL"
        elif cum_delta > 0:
            direction = "BUY_PRESSURE"
        else:
            direction = "SELL_PRESSURE"
            
        return round(cum_delta, 2), direction, round(strength, 3)

    def _delta_score(
        self,
        direction: str,
        strength: float,
    ) -> tuple[int, int, list[str], list[str]]:
        """Convert delta analysis to score points."""
        buy_pts, sell_pts = 0, 0
        buy_r, sell_r = [], []
        
        if direction == "BUY_PRESSURE":
            if strength >= 0.5:
                buy_pts = 5
                buy_r.append(f"VOL:strong_buy_pressure_{strength:.0%}")
            elif strength >= 0.25:
                buy_pts = 3
                buy_r.append(f"VOL:buy_pressure_{strength:.0%}")
        elif direction == "SELL_PRESSURE":
            if strength >= 0.5:
                sell_pts = 5
                sell_r.append(f"VOL:strong_sell_pressure_{strength:.0%}")
            elif strength >= 0.25:
                sell_pts = 3
                sell_r.append(f"VOL:sell_pressure_{strength:.0%}")
                
        return buy_pts, sell_pts, buy_r, sell_r

    @staticmethod
    def _get_vol_col(candles: pd.DataFrame) -> str | None:
        """Find volume column in candles DataFrame."""
        for col in ["tick_volume", "volume", "real_volume"]:
            if col in candles.columns:
                return col
        return None
