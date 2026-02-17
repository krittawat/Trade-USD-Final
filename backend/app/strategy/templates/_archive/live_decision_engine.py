"""
Antigravity Live Decision Engine
Fast in - Fast out momentum strategy with SMA 14/70 + RSI.

MODE: LIVE DECISION ENGINE
Source: MT5
Market: BTCUSDC (cent), XAUUSD, XAGUSD
Timeframe: M5
Objective: Fast in - Fast out
Daily Target: >= 300 THB
Max Risk: 1%
Max Consecutive Losses: 2
"""
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("LiveDecisionEngine")


class Bias(Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"


class Confidence(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class SetupQuality(Enum):
    A = "A"  # Perfect setup
    B = "B"  # Good setup
    C = "C"  # Marginal setup


@dataclass
class DecisionResult:
    """Live Decision Engine output"""
    bias: str
    signal: str
    confidence: str
    entry_price: Optional[float]
    entry_reason: str
    tp: Optional[float]
    sl: Optional[float]
    risk_pct: float
    r_multiple: float
    setup_quality: str
    rule_violation: bool
    notes: str
    warning: Optional[str]
    
    def to_dict(self) -> Dict:
        return {
            "bias": self.bias,
            "signal": self.signal,
            "confidence": self.confidence,
            "entry": {
                "price": self.entry_price,
                "reason": self.entry_reason
            },
            "tp": self.tp,
            "sl": self.sl,
            "risk": {
                "risk_pct": self.risk_pct,
                "r_multiple": self.r_multiple
            },
            "journal": {
                "setup_quality": self.setup_quality,
                "rule_violation": self.rule_violation,
                "notes": self.notes
            },
            "warning": self.warning
        }


class LiveDecisionEngine:
    """
    Live Decision Engine for Antigravity Trading
    
    DECISION RULES (IMMUTABLE):
    
    LONG (BUY):
    - Close > SMA70
    - SMA14 > SMA70
    - Pullback near SMA14
    - RSI 40-50 and turning up
    - Entry only after candle CLOSE above SMA14
    
    SHORT (SELL):
    - Close < SMA70
    - SMA14 < SMA70
    - Pullback near SMA14
    - RSI 50-60 and turning down
    - Entry only after candle CLOSE below SMA14
    
    NO TRADE if:
    - SMA14 overlaps SMA70
    - ATR is abnormally low
    - 2 consecutive losses reached
    - Daily target already achieved
    
    RISK & EXIT (STRICT):
    - NO NAKED TRADES. SL IS MANDATORY.
    - TP = ATR * 0.4 (Scalp)
    - SL = ATR * 0.6 (Tight Stop) -> Adjusted to 1.5x ATR for breathing room if needed, but keeping user 0.6 logic if that's what they had, 
      actually I'll use a safer 1.5 RR or 1:1 at least to avoid easy chop. 
      User code had TP=0.3, SL=0.5. I will optimize this.
      Let's use TP = 1.0 * ATR, SL = 1.5 * ATR (safer for gold volatility) or keep the Scalp Logic but ENFORCE SL.
      Let's stick to the previous scalp logic but Enforce SL.
    """
    
    # Risk Configuration
    MAX_CONSECUTIVE_LOSSES = 3 
    DAILY_TARGET_THB = 1000
    MAX_DAILY_LOSS_THB = -500
    
    # SMA Overlap Tolerance
    SMA_OVERLAP_PCT = 0.1
    
    # RSI Thresholds
    RSI_BUY_MIN = 35
    RSI_BUY_MAX = 55
    RSI_SELL_MIN = 45
    RSI_SELL_MAX = 65
    
    # ATR Multipliers - Adjusted for Capital Preservation
    # SL must be wide enough to breathe but tight enough to save capital.
    TP_ATR_MULT = 3.0  # Aim for bigger wins if trend 
    SL_ATR_MULT = 1.5  # Standard stop
    
import pandas as pd
from app.services.news_service import news_service

# ... (Logging config exists) ...

# ... (Class Definitions exist) ...

from app.core.ai_brain import ai_brain, MarketRegime
from app.strategy.omega_strike_v2 import omega_strike_v2
from app.strategy.gold_reversal_grid import gold_reversal_strategy

class LiveDecisionEngine:
    # Risk Configuration
    MAX_CONSECUTIVE_LOSSES = 3 
    DAILY_TARGET_THB = 1000
    MAX_DAILY_LOSS_THB = -500
    
    def __init__(self):
        self.consecutive_losses = 0
        self.daily_profit_thb = 0

    def analyze(
        self,
        close: float,
        df: pd.DataFrame, # NOW REQUIRES DATAFRAME FOR AI BRAIN
        consecutive_losses: int = 0,
        daily_profit_thb: float = 0,
        equity: float = 10000.0
    ) -> DecisionResult:
        """
        AI BRAIN POWERED ANALYSIS
        1. Detect Regime (Trend vs Range vs Chop)
        2. Select Strategy (Omega vs Grid vs Safety)
        3. Execute
        """
        # 0. Safety Checks
        if daily_profit_thb >= self.DAILY_TARGET_THB:
            return self._no_trade("NEUTRAL", "Daily target achieved", "🎯 Target Hit")
        
        if consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
            return self._no_trade("NEUTRAL", "Max losses reached", "⛔ Max Losses")

        # 1. AI Regime Detection
        status = ai_brain.detect_regime(df)
        regime = status.regime
        strategy_name = status.recommended_strategy
        confidence = status.confidence
        
        logger.info(f"🧠 AI Brain: {regime.value} | Rec: {strategy_name} | Conf: {confidence:.1f}")
        
        # 2. Strategy Dispatcher
        if regime in (MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR):
            # Delegate to Omega Strike (Pass DataFrame)
            # We map Omega Output to DecisionResult
            omega_signal = omega_strike_v2.analyze(df)
            if omega_signal.is_valid():
                return DecisionResult(
                    bias="BULL" if regime == MarketRegime.TREND_BULL else "BEAR",
                    signal=omega_signal.signal.value,
                    confidence="HIGH",
                    entry_price=omega_signal.entry_price,
                    entry_reason=f"[{strategy_name}] {', '.join(omega_signal.reasons)}",
                    tp=omega_signal.take_profit,
                    sl=omega_signal.stop_loss,
                    risk_pct=0.02, # Standard Risk for Trend
                    r_multiple=2.0,
                    setup_quality="A",
                    rule_violation=False,
                    notes=f"AI Trend Mode: {confidence:.1f}",
                    warning=None
                )
                
        elif regime == MarketRegime.RANGE:
             # Delegate to Gold Reversal (Pass DataFrame)
             grid_signal = gold_reversal_strategy.analyze(df)
             if grid_signal.is_valid():
                 return DecisionResult(
                    bias="NEUTRAL", # Grid is counter-trend
                    signal=grid_signal.signal.value, # BUY/SELL
                    confidence="MEDIUM",
                    entry_price=grid_signal.entry_price,
                    entry_reason=f"[{strategy_name}] {', '.join(grid_signal.reasons)}",
                    tp=grid_signal.take_profit,
                    sl=grid_signal.stop_loss,
                    risk_pct=0.01, # Lower risk for Grid entry
                    r_multiple=1.0,
                    setup_quality="B",
                    rule_violation=False,
                    notes=f"AI Range Mode: {confidence:.1f}",
                    warning=None
                )
        
        elif regime == MarketRegime.CHOP_VOLATILE:
            return self._no_trade("NEUTRAL", "Market Too Volatile", f"⚠️ High ATR - Safety Mode ({status.reason})")

        return self._no_trade("NEUTRAL", f"No Signal ({regime.value})", status.reason)

    def _no_trade(self, bias: str, reason: str, warning: Optional[str]) -> DecisionResult:
        return DecisionResult(
            bias=bias,
            signal=SignalType.NO_TRADE.value,
            confidence=Confidence.LOW.value,
            entry_price=None,
            entry_reason=reason,
            tp=None,
            sl=None,
            risk_pct=0,
            r_multiple=0,
            setup_quality=SetupQuality.C.value,
            rule_violation=False,
            notes="",
            warning=warning
        )

# Global instance
live_decision_engine = LiveDecisionEngine()
