"""
USDJPY Trend Strategy
=====================
Optimized for USDJPY Trend Following (M15/H1)

Features:
- Trend alignment using multiple EMAs (9, 21, 50)
- Pullback entry on lower timeframe concepts
- Trailing stop focus for catching big moves
"""
import pandas as pd
import pandas as pd
try:
    import app.analysis.indicators as ind
except ImportError:
    from app.utils.safe_ta import SafeTA as ta
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy, StrategyDecision

class UsdJpyTrendStrategy(BaseStrategy):
    """
    Trend Following strategy for USDJPY
    """
    
    # Configuration
    EMA_FAST = 9
    EMA_MID = 21
    EMA_SLOW = 50
    ATR_PERIOD = 14
    
    # Risk Management — ปรับ SL กว้างขึ้นสำหรับ Forex trend
    SL_ATR_MULT = 3.0      # เดิม 2.0 → pullback ลึกโดน sweep
    TP_ATR_MULT = 4.5      # RR = 1.5
    RISK_PER_TRADE = 0.01   # 1%
    
    def __init__(self):
        self.name = "USDJPY_TREND"
        self.params = {}
    
    def update_parameters(self, params: dict):
        self.params.update(params)
        
    def get_status(self):
        return {
            "name": self.name,
            "style": "Trend Following",
            "timeframe": "M15",
            "params": self.params
        }
        
    def analyze(self, df: pd.DataFrame, symbol: str = "USDJPY", **kwargs) -> StrategyDecision:
        if df is None or len(df) < 60:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient data")
            
        # Calculate indicators
        if 'ema_fast' not in df.columns:
            df['ema_fast'] = ta.ema(df['close'], length=self.EMA_FAST)
        if 'ema_mid' not in df.columns:
            df['ema_mid'] = ta.ema(df['close'], length=self.EMA_MID)
        if 'ema_slow' not in df.columns:
            df['ema_slow'] = ta.ema(df['close'], length=self.EMA_SLOW)
        if 'atr' not in df.columns:
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=self.ATR_PERIOD)
            
        r = df.iloc[-1]
        
        # Validation
        if pd.isna(r['ema_slow']) or pd.isna(r['atr']):
             return StrategyDecision(signal="NO_TRADE", reason="Invalid indicators")
             
        close = float(r['close'])
        atr = float(r['atr'])
        
        ema_fast = float(r['ema_fast'])
        ema_mid = float(r['ema_mid'])
        ema_slow = float(r['ema_slow'])
        
        # Logic
        buy_score = 0
        sell_score = 0
        reasons = []
        
        # 1. Trend Alignment
        bullish_alignment = ema_fast > ema_mid > ema_slow
        bearish_alignment = ema_fast < ema_mid < ema_slow
        
        if bullish_alignment:
            buy_score += 50
            reasons.append("EMA Alignment Bullish")
            # Check for pullback? Or breakout?
            if close > ema_fast: 
                buy_score += 10 # Momentum
            
        if bearish_alignment:
            sell_score += 50
            reasons.append("EMA Alignment Bearish")
            if close < ema_fast:
                sell_score += 10 # Momentum

        # Decision
        min_score = 60
        signal = "NO_TRADE"
        sl = 0.0
        tp = 0.0
        
        # USDJPY pip is 0.01 roughly
        sl_dist = atr * self.SL_ATR_MULT
        tp_dist = atr * self.TP_ATR_MULT
        
        if buy_score >= min_score:
            signal = "BUY"
            sl = close - sl_dist
            tp = close + tp_dist
            
        elif sell_score >= min_score:
            signal = "SELL"
            sl = close + sl_dist
            tp = close - tp_dist
            
        if signal == "NO_TRADE":
             return StrategyDecision(
                signal="WAIT",
                reason=f"Score: B{buy_score}/S{sell_score}",
                confidence=max(buy_score, sell_score)/100.0
            )

        return StrategyDecision(
            signal=signal,
            entry_price=close,
            sl=sl,
            tp=tp,
            reason=f"USDJPY Trend: {', '.join(reasons)}",
            confidence=max(buy_score, sell_score)/100.0,
            risk_pct=self.RISK_PER_TRADE
        )
