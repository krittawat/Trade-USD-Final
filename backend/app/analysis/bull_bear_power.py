"""
Bull/Bear Power Engine — วิเคราะห์แรงซื้อ/แรงขายที่แท้จริง

รวม 5 indicators เพื่อประเมินว่าใครกำจัดตลาดอยู่:
    1. Elder Bull Power = High - EMA(13)
    2. Elder Bear Power = Low - EMA(13)
    3. Volume-Weighted Pressure = Σ(CLV * vol) / Σ(vol)
    4. Momentum Slope = ความชันของ EMA(5)
    5. Consecutive Candle Strength = นับแท่งสีเดียวกันต่อเนื่อง

ผลลัพธ์เป็น Composite Score (-100 ถึง +100) และ Verdict 5 ระดับ
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════
MIN_BARS = 20
ELDER_EMA_PERIOD = 13
SLOPE_EMA_PERIOD = 5
VP_LOOKBACK = 10  # Volume Pressure lookback
MOMENTUM_LOOKBACK = 5

@dataclass
class BullBearResult:
    """Result of Bull/Bear Power analysis."""
    is_valid: bool = False
    
    # Raw metrics
    bull_power: float = 0.0          # Elder Bull Power
    bear_power: float = 0.0          # Elder Bear Power
    volume_pressure: float = 0.0     # -1.0 to 1.0 (Volume-Weighted CLV)
    momentum_slope: float = 0.0      # Normalized slope of fast EMA (-1.0 to 1.0)
    consecutive_strength: int = 0    # Positive = Bull consecutive, Negative = Bear consecutive
    
    # Aggregated outputs
    net_power: float = 0.0           # Combined raw power (bull + bear)
    score: int = 0                   # -100 to +100
    verdict: str = "NEUTRAL"         # STRONG_BULL, BULL, NEUTRAL, BEAR, STRONG_BEAR
    
    reasons: list[str] = field(default_factory=list)


class BullBearPowerEngine:
    """
    Stateless engine returns a composite 
    directional power score from -100 to +100.
    """

    def analyze(self, candles: pd.DataFrame) -> BullBearResult:
        result = BullBearResult()
        
        if candles is None or len(candles) < MIN_BARS:
            result.reasons.append("Insufficient data")
            return result
            
        c = candles["close"].astype(float)
        h = candles["high"].astype(float)
        l = candles["low"].astype(float)
        o = candles["open"].astype(float)
        
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        if vol_col not in candles.columns:
            result.reasons.append("No volume column")
            return result
            
        v = candles[vol_col].astype(float)
        
        reasons = []
        score = 0
        
        # 1. Elder Bull/Bear Power
        ema13 = c.ewm(span=ELDER_EMA_PERIOD, adjust=False).mean()
        
        # Current bar power
        bull_p = h.iloc[-1] - ema13.iloc[-1]  # positive = good for bull
        bear_p = l.iloc[-1] - ema13.iloc[-1]  # negative = good for bear
        
        # Normalize by ATR for context (use 14-period approx ATR)
        tr = pd.concat([
            h - l,
            (h - c.shift()).abs(),
            (l - c.shift()).abs()
        ], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        
        if atr > 0:
            norm_bull = float(bull_p / atr)
            norm_bear = float(bear_p / atr)
        else:
            norm_bull = 0.0
            norm_bear = 0.0
            
        result.bull_power = round(norm_bull, 2)
        result.bear_power = round(norm_bear, 2)
        result.net_power = round(norm_bull + norm_bear, 2)
        
        # Scoring Elder Power (-30 to +30)
        if norm_bull > 0 and norm_bear > 0:
            score += 30
            reasons.append("Elder: Both powers > 0 (Strong Bull)")
        elif norm_bull < 0 and norm_bear < 0:
            score -= 30
            reasons.append("Elder: Both powers < 0 (Strong Bear)")
        else:
            # Mixed state, weigh the net power
            net_scaled = max(-20, min(20, int(result.net_power * 10)))
            score += net_scaled
            reasons.append(f"Elder Net Power: {result.net_power}")

        # 2. Volume-Weighted Pressure (CLV)
        # CLV = [(close - low) - (high - close)] / (high - low)
        rng = h - l
        clv = ((c - l) - (h - c)) / rng.replace(0, np.nan)
        clv = clv.fillna(0.0)
        vp_val = 0.0
        
        vol_sum = v.iloc[-VP_LOOKBACK:].sum()
        if vol_sum > 0:
            vp_val = float((clv.iloc[-VP_LOOKBACK:] * v.iloc[-VP_LOOKBACK:]).sum() / vol_sum)
            
        result.volume_pressure = round(vp_val, 2)
        
        # Scoring VP (-30 to +30)
        vp_score = int(vp_val * 30)
        score += max(-30, min(30, vp_score))
        if abs(vp_score) > 10:
            reasons.append(f"Vol Pressure: {vp_val:.2f}")

        # 3. Momentum Slope
        ema5 = c.ewm(span=SLOPE_EMA_PERIOD, adjust=False).mean()
        slope = (ema5.iloc[-1] - ema5.iloc[-2])
        if atr > 0:
            slope_norm = slope / atr
        else:
            slope_norm = 0.0
            
        # Exponential scaling for clear trends
        result.momentum_slope = round(float(slope_norm), 2)
        slope_score = int(slope_norm * 40)
        score += max(-20, min(20, slope_score))
        if abs(slope_score) > 10:
            reasons.append(f"Momentum Slope: {result.momentum_slope:.2f}")

        # 4. Consecutive Candle Strength
        consecutive = 0
        last_dir = 0 # 1=bull, -1=bear
        
        # Look back up to MOMENTUM_LOOKBACK bars
        for i in range(-1, -MOMENTUM_LOOKBACK - 1, -1):
            _c = c.iloc[i]
            _o = o.iloc[i]
            diff = _c - _o
            if diff > 0:
                current_dir = 1
            elif diff < 0:
                current_dir = -1
            else:
                break # doji breaks sequence
                
            if last_dir == 0:
                last_dir = current_dir
                consecutive = current_dir
            elif last_dir == current_dir:
                consecutive += current_dir
            else:
                break
                
        result.consecutive_strength = consecutive
        
        # Scoring consecutive (-20 to +20)
        cs_score = max(-20, min(20, consecutive * 5))
        score += cs_score
        if abs(consecutive) >= 2:
            reasons.append(f"Consecutive Bars: {consecutive}")
            
        # Final Score & Verdict
        result.score = max(-100, min(100, score))
        result.is_valid = True
        
        if result.score > 60:
            result.verdict = "STRONG_BULL"
        elif result.score > 25:
            result.verdict = "BULL"
        elif result.score < -60:
            result.verdict = "STRONG_BEAR"
        elif result.score < -25:
            result.verdict = "BEAR"
        else:
            result.verdict = "NEUTRAL"
            
        result.reasons = reasons
        
        logger.debug("bull_bear_analyzed", extra={
            "score": result.score,
            "verdict": result.verdict,
            "net_power": result.net_power,
            "vp": result.volume_pressure,
            "slope": result.momentum_slope,
        })
        
        return result
