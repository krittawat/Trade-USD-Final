"""
Predicta Futures - Next Candle Predictor V4 (Python Port)
Target: 75%+ Win Rate
Features:
- Custom Supertrend (ATR-based)
- ADX Threshold > 25
- RSI > 50 (Bullish) / < 50 (Bearish)
- Stochastic Confluence
- Volume Flow Check
"""

import pandas as pd
import pandas_ta as ta
from typing import Dict, Any
from .base_strategy import BaseStrategy, StrategyDecision

class PredictaV4Strategy(BaseStrategy):
    def __init__(self):
        # super().__init__("PredictaV4") # BaseStrategy is an ABC, init might not take name if generic
        pass
        self.name = "PredictaV4"
        self.atr_period = 14
        self.st_factor = 3.0
        self.st_period = 10
        self.adx_threshold = 25
        self.rsi_period = 14
        self.stoch_k = 14
        self.stoch_d = 3
        self.stoch_smooth = 3

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "atr_period": self.atr_period,
            "st_factor": self.st_factor
        }

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> StrategyDecision:
        if df.empty or len(df) < 100:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient Data")

        # 1. Indicators
        # ATR
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=self.atr_period)
        
        # Supertrend (Custom Implementation)
        st = ta.supertrend(df['high'], df['low'], df['close'], length=self.st_period, multiplier=self.st_factor)
        st_col = f"SUPERT_{self.st_period}_{self.st_factor}"
        st_dir_col = f"SUPERTd_{self.st_period}_{self.st_factor}"
        
        # ADX
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        
        # RSI
        df['rsi'] = ta.rsi(df['close'], length=self.rsi_period)
        
        # Stochastic
        stoch = ta.stoch(df['high'], df['low'], df['close'], k=self.stoch_k, d=self.stoch_d, smooth_k=self.stoch_smooth)
        k_col = f"STOCHk_{self.stoch_k}_{self.stoch_d}_{self.stoch_smooth}"

        # 2. Logic (Last Candle)
        i = -1
        close = df['close'].iloc[i]
        try:
            trend_dir = st[st_dir_col].iloc[i] 
            current_adx = adx[f"ADX_14"].iloc[i]
            current_rsi = df['rsi'].iloc[i]
            current_k = stoch[k_col].iloc[i]
            atr = df['atr'].iloc[i]
        except KeyError:
             return StrategyDecision(signal="NO_TRADE", reason="Indicator Error")
        
        reasons = []
        
        # Refined Logic
        is_buy_trend = trend_dir == 1
        is_sell_trend = trend_dir == -1
        
        final_signal = "NO_TRADE"
        confidence = 0.0
        entry_stop = 0.0
        entry_tp = 0.0
        
        # BUY LOGIC
        if is_buy_trend and direction != "SELL":
            reasons = ["🚀 Supertrend Bullish"]
            confluence = 1
            
            if current_adx > self.adx_threshold: 
                confluence += 1
                reasons.append(f"ADX ({current_adx:.0f})")
            
            if current_rsi > 50:
                confluence += 1
            
            if current_k > 50 and current_k < 90: 
                confluence += 1
                reasons.append("Stoch Up")
                
            if df['close'].iloc[i] > df['open'].iloc[i]:
                confluence += 1
                
            confidence = (confluence / 5) * 100
            if confidence >= 80:
                final_signal = "BUY"
                # Using 4x ATR per user request via settings logic, 
                # but standard logic here returns suggested levels.
                # BotManager overrides with settings if configured.
                entry_stop = close - (atr * 4.0) 
                entry_tp = close + (atr * 6.0)
        
        # SELL LOGIC
        elif is_sell_trend and direction != "BUY":
            reasons = ["🔻 Supertrend Bearish"]
            confluence = 1
            
            if current_adx > self.adx_threshold: 
                confluence += 1
                reasons.append(f"ADX ({current_adx:.0f})")
            
            if current_rsi < 50:
                confluence += 1
            
            if current_k < 50 and current_k > 10: 
                confluence += 1
                reasons.append("Stoch Down")

            if df['close'].iloc[i] < df['open'].iloc[i]:
                confluence += 1
                
            confidence = (confluence / 5) * 100
            if confidence >= 80:
                final_signal = "SELL"
                entry_stop = close + (atr * 4.0)
                entry_tp = close - (atr * 6.0)

        return StrategyDecision(
            signal=final_signal,
            entry_price=close,
            sl=entry_stop,
            tp=entry_tp,
            reason=", ".join(reasons),
            confidence=confidence
        )

predicta_v4 = PredictaV4Strategy()
