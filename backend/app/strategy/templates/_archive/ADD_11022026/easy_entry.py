"""
Easy Entry/Exit Trend Strategy (Python Port)
Based on Pine Script by countseven12
Features:
- MACD (12, 26, 9)
- Bollinger Bands on MACD (Length 10, Dev 1.0)
- Golden/Death Cross (SMA 50/200)
"""

import pandas as pd
import pandas as pd
try:
    import app.analysis.indicators as ind
except ImportError:
    from app.utils.safe_ta import SafeTA as ta
from .base_strategy import BaseStrategy, StrategyDecision

class EasyEntryStrategy(BaseStrategy):
    def __init__(self):
        self.name = "EasyEntry"
        # MACD
        self.fast = 12
        self.slow = 26
        self.signal = 9
        # BB on MACD
        self.bb_len = 10
        self.bb_dev = 1.0
        # MA
        self.ma_fast = 50
        self.ma_slow = 200
        # Risk
        self.sl_atr_mult = 2.0
        self.tp_atr_mult = 3.0

    def get_status(self):
        return {"name": self.name}

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> StrategyDecision:
        if df.empty or len(df) < 200:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient Data")

        # 1. Indicators
        # MACD
        macd = ta.macd(df['close'], fast=self.fast, slow=self.slow, signal=self.signal)
        if macd is None or macd.empty: return StrategyDecision(signal="NO_TRADE", reason="MACD Error")

        # Columns: MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9
        macd_line = macd[f"MACD_{self.fast}_{self.slow}_{self.signal}"]
        
        # BB on MACD Line (Explicit calc)
        macd_sma = macd_line.rolling(self.bb_len).mean()
        macd_std = macd_line.rolling(self.bb_len).std()
        upper = macd_sma + (macd_std * self.bb_dev)
        lower = macd_sma - (macd_std * self.bb_dev)
        
        # SMA Trends
        sma_fast_line = ta.sma(df['close'], length=self.ma_fast)
        sma_slow_line = ta.sma(df['close'], length=self.ma_slow)
        
        # ATR for SL
        atr_series = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        # ADX for Chop Filter
        adx_series = ta.adx(df['high'], df['low'], df['close'], length=14)
        
        # 2. Logic (Last Closed Candle)
        i = -1
        curr_macd = macd_line.iloc[i]
        curr_upper = upper.iloc[i]
        curr_lower = lower.iloc[i]
        
        curr_fast_ma = sma_fast_line.iloc[i]
        curr_slow_ma = sma_slow_line.iloc[i]
        
        atr = atr_series.iloc[i] if not pd.isna(atr_series.iloc[i]) else df['close'].iloc[i] * 0.005
        # ADX Check
        adx_val = 0
        if adx_series is not None and not adx_series.empty:
            adx_val = adx_series['ADX_14'].iloc[i]
            
        close = df['close'].iloc[i]
        
        reasons = []
        confidence = 0.0
        final_signal = "NO_TRADE"
        
        # Chop Filter
        if adx_val < 20:
             return StrategyDecision(signal="NO_TRADE", reason=f"Choppy Market (ADX {adx_val:.1f} < 20)")
        
        reasons.append(f"Trend Strong (ADX {adx_val:.1f})")
        
        # Trend Filter (Golden/Death Cross context)
        is_bull_trend = curr_fast_ma > curr_slow_ma
        is_bear_trend = curr_fast_ma < curr_slow_ma
        
        # MACD Momentum Signal
        # "Easy Entry" Logic: 
        # Buy when MACD > Upper Band (Strong Momentum)
        # Sell when MACD < Lower Band (Strong Down Momentum)
        
        is_macd_bull = curr_macd > curr_upper
        is_macd_bear = curr_macd < curr_lower
        
        # Direction Check
        check_buy = direction in ("AUTO", "BUY", "BOTH")
        check_sell = direction in ("AUTO", "SELL", "BOTH")
        
        if check_buy and is_macd_bull and is_bull_trend:
            final_signal = "BUY"
            reasons.append("MACD Breakout + Golden Trend")
            confidence = 85.0
                
        elif check_sell and is_macd_bear and is_bear_trend:
            final_signal = "SELL"
            reasons.append("MACD Breakdown + Death Trend")
            confidence = 85.0
        
        sl = 0.0
        tp = 0.0
        
        if final_signal == "BUY":
             dist = atr * self.sl_atr_mult
             sl = close - dist
             tp = close + (dist * (self.tp_atr_mult / self.sl_atr_mult)) # Maintain Ratio
        elif final_signal == "SELL":
             dist = atr * self.sl_atr_mult
             sl = close + dist
             tp = close - (dist * (self.tp_atr_mult / self.sl_atr_mult))

        return StrategyDecision(
            signal=final_signal,
            entry_price=close,
            sl=float(sl),
            tp=float(tp),
            reason=", ".join(reasons),
            confidence=confidence
        )

easy_entry_strategy = EasyEntryStrategy()
