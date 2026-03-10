"""
Antigravity Alpha V6 Strategy — Institutional Efficiency Version.

Combines:
1. Liquidity Hunter (Smart Money Sweeps)
2. Fair Value Gap (FVG) Confluence
3. Multi-Timeframe Trend Filtering (EMA 20/200)

Execution Logic:
- Wait for a Liquidity Sweep (PDH/PDL, Asia H/L, or Equal levels).
- Confirm with a Displacement candle and an active FVG zone.
- Enter on FVG retest or Displacement confirmation.
- SL set behind the Sweep wick (Extreme Wick SL).
- TP target 1:3 RR minimum.
"""

import pandas as pd
import numpy as np
from typing import Optional, List

from app.strategy.base import BaseStrategy
from app.domain.models import Decision, SymbolProfile
from app.domain.enums import Action, RegimeType
from app.core.logging import get_logger
from app.analysis.fvg import detect_fvg_zones, get_active_fvg, FVGZone
from app.brain.liquidity_hunter import LiquidityHunter, LiquiditySignal

logger = get_logger(__name__)

# Parameters
EMA_FAST = 20
EMA_SLOW = 200
ATR_PERIOD = 14
RR_MIN = 3.0
CONFIDENCE_THRESHOLD = 0.65

class AlphaV6Strategy(BaseStrategy):
    """
    Alpha V6: High-Efficiency Institutional Strategy.
    Optimized for XAUUSD, XAGUSD, BTCUSD.
    """
    name = "alpha_v6"
    timeframe = "M15"
    suitable_regimes = [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN, RegimeType.HIGH_VOLATILITY]

    def __init__(self):
        self.hunter = LiquidityHunter()

    def analyze(self, candles: pd.DataFrame, profile: SymbolProfile, regime: RegimeType = RegimeType.UNKNOWN, **kwargs) -> Decision:
        symbol = profile.symbol if profile else "XAUUSD"
        
        # 1. Data Check
        if len(candles) < 200:
            return self.create_hold(symbol, f"Insufficient data: {len(candles)}")

        # 2. Indicators
        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        
        ema_f = close.ewm(span=EMA_FAST, adjust=False).mean()
        ema_s = close.ewm(span=EMA_SLOW, adjust=False).mean()
        
        current_close = float(close.iloc[-1])
        trend_up = ema_f.iloc[-1] > ema_s.iloc[-1]
        trend_down = ema_f.iloc[-1] < ema_s.iloc[-1]
        
        # ATR for SL calculations
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(ATR_PERIOD).mean().iloc[-1]

        # 3. Liquidity Sweep Detection
        # Use a larger window for session/pool detection if available
        liq_signal = self.hunter.scan(candles, atr=atr)
        
        # 4. FVG Detection
        fvg_zones = detect_fvg_zones(candles, lookback=40, atr_value=atr)
        active_fvg = get_active_fvg(fvg_zones, current_close, tolerance=atr * 0.2)

        # 5. Logic Assembly
        action = Action.HOLD
        confidence = 0.0
        reasons = []
        sl = None
        tp = None

        # BUY Setup: Bullish Trend + Liquidity Sweep Low + Bullish Displacement/FVG
        if trend_up and liq_signal.sweep_detected and liq_signal.sweep_direction == "BUY":
            if liq_signal.confidence >= CONFIDENCE_THRESHOLD:
                # Extra confirmation: Is there a Bullish FVG or Displacement?
                has_bull_fvg = any(z.direction == "BULLISH" and not z.filled for z in fvg_zones[:5])
                
                if has_bull_fvg or liq_signal.displacement_strength > 0.4:
                    action = Action.BUY
                    confidence = liq_signal.confidence
                    reasons.append(f"SweepLows({liq_signal.confidence})")
                    if has_bull_fvg: reasons.append("BullFVG")
                    
                    # SL below sweep low
                    sweep_low = liq_signal.invalidation_price
                    sl = sweep_low if sweep_low > 0 else (current_close - atr * 1.5)
                    
                    # Dynamic TP based on RR
                    risk = current_close - sl
                    tp = current_close + (risk * RR_MIN)

        # SELL Setup: Bearish Trend + Liquidity Sweep High + Bearish Displacement/FVG
        elif trend_down and liq_signal.sweep_detected and liq_signal.sweep_direction == "SELL":
            if liq_signal.confidence >= CONFIDENCE_THRESHOLD:
                has_bear_fvg = any(z.direction == "BEARISH" and not z.filled for z in fvg_zones[:5])
                
                if has_bear_fvg or liq_signal.displacement_strength > 0.4:
                    action = Action.SELL
                    confidence = liq_signal.confidence
                    reasons.append(f"SweepHighs({liq_signal.confidence})")
                    if has_bear_fvg: reasons.append("BearFVG")
                    
                    # SL above sweep high
                    sweep_high = liq_signal.invalidation_price
                    sl = sweep_high if sweep_high > 0 else (current_close + atr * 1.5)
                    
                    # Dynamic TP based on RR
                    risk = sl - current_close
                    tp = current_close - (risk * RR_MIN)

        if action == Action.HOLD:
            hold_reason = "No Sweep" if not liq_signal.sweep_detected else f"Low Conf({liq_signal.confidence})"
            return self.create_hold(symbol, hold_reason)

        return Decision(
            symbol=symbol,
            action=action,
            confidence=confidence,
            reason=" | ".join(reasons),
            stop_loss=sl,
            take_profit=tp,
            strategy_name=self.name,
            timeframe=self.timeframe,
            risk_reward_ratio=RR_MIN
        )
