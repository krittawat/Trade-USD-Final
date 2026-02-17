"""
Mobile Pro Strategy - Optimized for M5 Scalping
Inspired by Mobile Trading Setup with EMA 9/21/50, PSAR (0.01, 0.3), MACD (25, 50, 9), and RSI (14).
"""
import pandas as pd
import logging
from typing import Dict, Optional
from .live_decision_engine import DecisionResult, SignalType as Signal, Confidence, SetupQuality, Bias

logger = logging.getLogger("MobileProStrategy")

class MobileProStrategy:
    def __init__(self):
        pass

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> DecisionResult:
        if len(df) < 50:
            return self._no_trade("Insufficient data")

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # 1. EMAs (9, 21, 50)
        ema9 = last.get('EMA_9', 0)
        ema21 = last.get('EMA_21', 0)
        ema50 = last.get('EMA_50', 0)
        close = last['close']

        # 2. Parabolic SAR (Custom 0.01, 0.3)
        psar_is_long = last.get('PSAR_IsLong', False)
        
        # 3. MACD (25, 50, 9)
        macd_hist = last.get('MACDh_25_50_9', 0)
        
        # 4. RSI (14)
        rsi = last.get('RSI_14', 50)
        
        # 5. ATR for Risk
        atr = last.get('ATR_14', 0)

        # BIAS DETERMINATION
        if close > ema50 and ema9 > ema21:
            bias = Bias.BULL.value
        elif close < ema50 and ema9 < ema21:
            bias = Bias.BEAR.value
        else:
            bias = Bias.NEUTRAL.value

        # BUY SIGNAL
        if bias == Bias.BULL.value and direction in ("AUTO", "BUY"):
            # Conditions: Price > EMA 9/21/50, PSAR Long, MACD Hist > 0, RSI > 50
            if close > ema9 and psar_is_long and macd_hist > 0 and rsi > 50:
                return DecisionResult(
                    bias=bias,
                    signal=Signal.BUY.value,
                    confidence=Confidence.HIGH.value if rsi > 55 else Confidence.MEDIUM.value,
                    entry_price=close,
                    entry_reason=f"MobilePro BULL: EMA Align, PSAR UP, MACD+, RSI {rsi:.1f}",
                    tp=round(close + (atr * 2), 2),
                    sl=round(close - (atr * 1.5), 2),
                    risk_pct=1.0,
                    r_multiple=1.3,
                    setup_quality=SetupQuality.A.value,
                    rule_violation=False,
                    notes=f"ATR: {atr:.2f}",
                    warning=None
                )

        # SELL SIGNAL
        if bias == Bias.BEAR.value and direction in ("AUTO", "SELL"):
            if close < ema9 and not psar_is_long and macd_hist < 0 and rsi < 50:
                return DecisionResult(
                    bias=bias,
                    signal=Signal.SELL.value,
                    confidence=Confidence.HIGH.value if rsi < 45 else Confidence.MEDIUM.value,
                    entry_price=close,
                    entry_reason=f"MobilePro BEAR: EMA Align, PSAR DOWN, MACD-, RSI {rsi:.1f}",
                    tp=round(close - (atr * 2), 2),
                    sl=round(close + (atr * 1.5), 2),
                    risk_pct=1.0,
                    r_multiple=1.3,
                    setup_quality=SetupQuality.A.value,
                    rule_violation=False,
                    notes=f"ATR: {atr:.2f}",
                    warning=None
                )

        return self._no_trade(bias, f"Conditions not met. EMA9:{ema9:.1f}, PSAR:{'UP' if psar_is_long else 'DN'}")

    def _no_trade(self, bias: str, reason: str) -> DecisionResult:
        return DecisionResult(
            bias=bias,
            signal=Signal.NO_TRADE.value,
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
            warning=None
        )
