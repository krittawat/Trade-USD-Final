from .base_strategy import BaseStrategy, StrategyDecision
from typing import Dict, Any, Optional

class LiveDecisionStrategy(BaseStrategy):
    """
    Modular implementation of the SMA 14/70 + RSI Strategy.
    """
    RSI_BUY_MIN = 35
    RSI_BUY_MAX = 55
    RSI_SELL_MIN = 45
    RSI_SELL_MAX = 65
    TP_ATR_MULT = 4.0   # Wide TP for 1:2 R:R (was 0.3)
    SL_ATR_MULT = 2.0   # Safe SL to avoid stop hunts (was 0.5)
    SMA_OVERLAP_PCT = 0.1

    def analyze(self, **kwargs) -> StrategyDecision:
        close = kwargs.get('close')
        sma14 = kwargs.get('sma14')
        sma70 = kwargs.get('sma70')
        rsi = kwargs.get('rsi')
        rsi_prev = kwargs.get('rsi_prev')
        atr = kwargs.get('atr')
        equity = kwargs.get('equity', 10000.0)
        
        # New "Realistic" filters
        adx = kwargs.get('adx', 0)
        bb_upper = kwargs.get('bb_upper', 0)
        bb_lower = kwargs.get('bb_lower', 0)
        bb_middle = kwargs.get('bb_middle', 1)
        
        # Basic signal logic
        rsi_turning_up = rsi > rsi_prev
        rsi_turning_down = rsi < rsi_prev
        
        signal = "NO_TRADE"
        reason = "Conditions not met"
        tp = None
        sl = 0.0
        confidence = 0.0

        # Filter: ADX (Trend Strength)
        if adx < 20:
            # Too weak trend, skip
            return StrategyDecision(signal="NO_TRADE", reason=f"Weak Trend (ADX {adx:.1f})", confidence=0.0)

        # Filter: Bollinger Squeeze
        bb_width = (bb_upper - bb_lower) / bb_middle * 100
        if bb_width < 0.15:
            return StrategyDecision(signal="NO_TRADE", reason=f"BB Squeeze ({bb_width:.2f}%)", confidence=0.0)
        
        # Long Logic
        if close > sma70 and sma14 > sma70:
            if self.RSI_BUY_MIN <= rsi <= self.RSI_BUY_MAX and rsi_turning_up:
                # Extra check: not overextended
                if close < bb_upper:
                    signal = "BUY"
                    tp = close + (atr * self.TP_ATR_MULT)
                    reason = f"TREND LONG: SMA14/70, RSI {rsi:.1f}, ADX {adx:.1f}"
                    confidence = 0.8
                else:
                    reason = "Overextended above BB Upper"
        
        # Short Logic
        elif close < sma70 and sma14 < sma70:
            if self.RSI_SELL_MIN <= rsi <= self.RSI_SELL_MAX and rsi_turning_down:
                # Extra check: not overextended
                if close > bb_lower:
                    signal = "SELL"
                    tp = close - (atr * self.TP_ATR_MULT)
                    reason = f"TREND SHORT: SMA14/70, RSI {rsi:.1f}, ADX {adx:.1f}"
                    confidence = 0.8
                else:
                    reason = "Overextended below BB Lower"

        return StrategyDecision(
            signal=signal,
            entry_price=close if signal != "NO_TRADE" else None,
            sl=sl if signal != "NO_TRADE" else None,
            tp=tp if signal != "NO_TRADE" else None,
            reason=reason,
            confidence=confidence,
            risk_pct=1.0 # Default
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": "Live Decision SMA/RSI",
            "config": {
                "rsi_buy": f"{self.RSI_BUY_MIN}-{self.RSI_BUY_MAX}",
                "rsi_sell": f"{self.RSI_SELL_MIN}-{self.RSI_SELL_MAX}",
                "tp_atr": self.TP_ATR_MULT
            }
        }
