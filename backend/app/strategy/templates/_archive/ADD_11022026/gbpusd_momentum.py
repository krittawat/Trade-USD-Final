"""
GBPUSD Momentum Strategy
========================
Optimized for GBPUSD Volatility Breakouts

Features:
- Impulse detection (Large candles)
- Support/Resistance breakout logic (Donchian/Bollinger)
- Volume spike confirmation (if available)
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

class GbpUsdMomentumStrategy(BaseStrategy):
    """
    Momentum/Breakout strategy for GBPUSD
    """
    
    # Configuration
    BB_LENGTH = 20
    BB_STD = 2.0
    ATR_PERIOD = 14
    MOMENTUM_PERIOD = 10
    
    # Risk Management
    SL_ATR_MULT = 1.8
    TP_ATR_MULT = 2.5
    RISK_PER_TRADE = 0.01 # 1%
    
    def __init__(self):
        self.name = "GBPUSD_MOMENTUM"
        self.params = {}
    
    def update_parameters(self, params: dict):
        self.params.update(params)
        
    def get_status(self):
        return {
            "name": self.name,
            "style": "Momentum Breakout",
            "timeframe": "M15/H1",
            "params": self.params
        }
        
    def analyze(self, df: pd.DataFrame, symbol: str = "GBPUSD", **kwargs) -> StrategyDecision:
        if df is None or len(df) < 60:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient data")
            
        # Calculate indicators
        if 'atr' not in df.columns:
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=self.ATR_PERIOD)
        
        # Bollinger Bands
        if 'bb_upper' not in df.columns:
            bb = ta.bbands(df['close'], length=self.BB_LENGTH, std=self.BB_STD)
            if bb is not None:
                # pandas_ta returns columns like BBL_20_2.0, BBM_20_2.0, BBU_20_2.0
                # We need to map them or access dynamically
                # Assuming standard names from pandas_ta
                # default names: BBL_length_std, BBM..., BBU...
                cols = bb.columns
                df['bb_upper'] = bb[cols[2]] # Upper
                df['bb_lower'] = bb[cols[0]] # Lower
                df['bb_mid'] = bb[cols[1]]   # Mid
            
        r = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Validation
        if pd.isna(r.get('bb_upper')) or pd.isna(r.get('atr')):
             return StrategyDecision(signal="NO_TRADE", reason="Invalid indicators")
             
        close = float(r['close'])
        open_price = float(r['open'])
        atr = float(r['atr'])
        bb_upper = float(r['bb_upper'])
        bb_lower = float(r['bb_lower'])
        
        # Logic
        buy_score = 0
        sell_score = 0
        reasons = []
        
        # 1. Breakout from BB
        if close > bb_upper and open_price < bb_upper:
            # Candle crossed and closed above upper band
            buy_score += 60
            reasons.append("Bollinger Breakout UP")
            
        if close < bb_lower and open_price > bb_lower:
            # Candle crossed and closed below lower band
            sell_score += 60
            reasons.append("Bollinger Breakout DOWN")
            
        # 2. Candle Size (Impulse)
        candle_body = abs(close - open_price)
        if candle_body > atr: # Strong candle
            if close > open_price:
                buy_score += 10
            else:
                sell_score += 10

        # Decision
        min_score = 60
        signal = "NO_TRADE"
        sl = 0.0
        tp = 0.0
        
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
            reason=f"GBPUSD Momentum: {', '.join(reasons)}",
            confidence=max(buy_score, sell_score)/100.0,
            risk_pct=self.RISK_PER_TRADE
        )
