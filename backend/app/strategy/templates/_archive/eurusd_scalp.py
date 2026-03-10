"""
EURUSD Scalping Strategy
========================
Optimized for EURUSD M5/M15 Scalping with EMA Cross + RSI

Features:
- Fast EMA crossover (5/13) for quick entries
- RSI filter to avoid overbought/oversold extremes unless reversal
- Tight spreads allow for smaller targets
- Risk: Fixed risk per trade (0.5-1%), tight SL
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

class EurUsdScalpStrategy(BaseStrategy):
    """
    Scalping strategy for EURUSD
    """
    
    # Configuration
    EMA_FAST = 5
    EMA_SLOW = 13
    EMA_TREND = 50
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    
    # Entry Thresholds
    RSI_BUY_MIN = 30
    RSI_BUY_MAX = 70
    RSI_SELL_MIN = 30
    RSI_SELL_MAX = 70
    
    # Risk Management — ปรับ SL กว้างขึ้นเพื่อรอด noise M5
    SL_ATR_MULT = 3.0      # เดิม 1.5 → แคบเกินโดน sweep
    TP_ATR_MULT = 4.5      # RR = 1.5 (4.5/3.0)
    MIN_SL_PIPS = 8.0      # Minimum 8 pips SL (เดิม 5 แคบเกิน)
    RISK_PER_TRADE = 0.005  # 0.5%
    
    def __init__(self):
        self.name = "EURUSD_SCALP"
        self.params = {}
    
    def update_parameters(self, params: dict):
        self.params.update(params)
        
    def get_status(self):
        return {
            "name": self.name,
            "style": "Scalping",
            "timeframe": "M5",
            "params": self.params
        }
        
    def analyze(self, df: pd.DataFrame, symbol: str = "EURUSD", **kwargs) -> StrategyDecision:
        if df is None or len(df) < 60:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient data")
            
        # Calculate indicators
        if 'ema_fast' not in df.columns:
            df['ema_fast'] = ta.ema(df['close'], length=self.EMA_FAST)
        if 'ema_slow' not in df.columns:
            df['ema_slow'] = ta.ema(df['close'], length=self.EMA_SLOW)
        if 'ema_trend' not in df.columns:
            df['ema_trend'] = ta.ema(df['close'], length=self.EMA_TREND)
        if 'rsi' not in df.columns:
            df['rsi'] = ta.rsi(df['close'], length=self.RSI_PERIOD)
        if 'atr' not in df.columns:
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=self.ATR_PERIOD)
            
        r = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Validation
        if pd.isna(r['rsi']) or pd.isna(r['atr']):
             return StrategyDecision(signal="NO_TRADE", reason="Invalid indicators")
             
        close = float(r['close'])
        atr = float(r['atr'])
        rsi = float(r['rsi'])
        
        ema_fast = float(r['ema_fast'])
        ema_slow = float(r['ema_slow'])
        ema_trend = float(r['ema_trend'])
        
        ema_fast_prev = float(prev['ema_fast'])
        ema_slow_prev = float(prev['ema_slow'])
        
        # Logic
        buy_score = 0
        sell_score = 0
        reasons = []
        
        # 1. EMA Cross
        ema_cross_up = ema_fast > ema_slow and ema_fast_prev <= ema_slow_prev
        ema_cross_down = ema_fast < ema_slow and ema_fast_prev >= ema_slow_prev
        
        if ema_cross_up:
            buy_score += 40
            reasons.append("EMA Cross Up")
        elif ema_fast > ema_slow:
            buy_score += 10
            
        if ema_cross_down:
            sell_score += 40
            reasons.append("EMA Cross Down")
        elif ema_fast < ema_slow:
            sell_score += 10
            
        # 2. Trend Filter
        if close > ema_trend:
            buy_score += 20
        else:
            sell_score += 20
            
        # 3. RSI Filter
        if self.RSI_BUY_MIN <= rsi <= self.RSI_BUY_MAX:
            buy_score += 20
        elif rsi < self.RSI_BUY_MIN:
             buy_score += 10 # Oversold bounce potential
             
        if self.RSI_SELL_MIN <= rsi <= self.RSI_SELL_MAX:
            sell_score += 20
        elif rsi > self.RSI_SELL_MAX:
            sell_score += 10 # Overbought pullback potential

        # Decision
        min_score = 60
        signal = "NO_TRADE"
        sl = 0.0
        tp = 0.0
        
        if buy_score >= min_score and buy_score > sell_score:
            signal = "BUY"
            # Approx pip value for EURUSD is 0.0001
            # Ensure Min SL
            sl_pips = max(atr * self.SL_ATR_MULT, self.MIN_SL_PIPS * 0.0001)
            tp_pips = atr * self.TP_ATR_MULT
            
            sl = close - sl_pips
            tp = close + tp_pips
            
        elif sell_score >= min_score and sell_score > buy_score:
            signal = "SELL"
            sl_pips = max(atr * self.SL_ATR_MULT, self.MIN_SL_PIPS * 0.0001)
            tp_pips = atr * self.TP_ATR_MULT
            
            sl = close + sl_pips
            tp = close - tp_pips
            
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
            reason=f"EURUSD Scalp: {', '.join(reasons)}",
            confidence=max(buy_score, sell_score)/100.0,
            risk_pct=self.RISK_PER_TRADE
        )
