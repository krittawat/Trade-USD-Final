"""
Easy Fusion Strategy (The Hybrid)
Combines the aggressive entry of EASY_ENTRY with the disciplined filtering of EASY_TREND.

Logic:
1. TREND (Safety): SMA 50 > SMA 200 (Golden Cross Check)
2. FILTER (Safety): ADX > 20 (No Lazy Markets)
3. TRIGGER (Speed): MACD Breakout > Bollinger Upper Band (Momentum Explosion)
4. EXIT (Profit): Dynamic ATR Trailing Stop (Let profits run)
"""

import pandas as pd
import pandas_ta as ta
from .base_strategy import BaseStrategy, StrategyDecision

class EasyFusionStrategy(BaseStrategy):
    def __init__(self):
        self.name = "EasyFusion"
        # MACD (From EasyEntry)
        self.fast = 12
        self.slow = 26
        self.signal = 9
        # BB on MACD
        self.bb_len = 10
        self.bb_dev = 1.0
        # MA Trend
        self.ma_fast = 50
        self.ma_slow = 200
        # ADX (From EasyTrend)
        self.adx_threshold = 20
        # Risk (Dynamic)
        self.sl_atr_mult = 2.0
        self.tp_atr_mult = 4.0 # Higher TP to allow trailing to work

    def get_status(self):
        return {"name": self.name}

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> StrategyDecision:
        if df.empty or len(df) < 200:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient Data")

        # === 1. INDICATORS ===
        # MACD
        macd = ta.macd(df['close'], fast=self.fast, slow=self.slow, signal=self.signal)
        if macd is None or macd.empty: return StrategyDecision(signal="NO_TRADE", reason="MACD Error")
        macd_line = macd[f"MACD_{self.fast}_{self.slow}_{self.signal}"]
        
        # BB on MACD
        macd_sma = macd_line.rolling(self.bb_len).mean()
        macd_std = macd_line.rolling(self.bb_len).std()
        upper = macd_sma + (macd_std * self.bb_dev)
        lower = macd_sma - (macd_std * self.bb_dev)
        
        # Trend & Filter
        sma_fast_line = ta.sma(df['close'], length=self.ma_fast)
        sma_slow_line = ta.sma(df['close'], length=self.ma_slow)
        adx_series = ta.adx(df['high'], df['low'], df['close'], length=14)
        atr_series = ta.atr(df['high'], df['low'], df['close'], length=14)

        # === 2. LOGIC (Current Candle) ===
        i = -1
        curr_macd = macd_line.iloc[i]
        curr_upper = upper.iloc[i]
        curr_lower = lower.iloc[i]
        
        curr_fast_ma = sma_fast_line.iloc[i]
        curr_slow_ma = sma_slow_line.iloc[i]
        
        adx_val = adx_series['ADX_14'].iloc[i] if adx_series is not None else 0
        atr = atr_series.iloc[i] if not pd.isna(atr_series.iloc[i]) else df['close'].iloc[i] * 0.005
        close = df['close'].iloc[i]

        reasons = []
        confidence = 0.0
        final_signal = "NO_TRADE"
        
        # --- FILTER 1: ADX (The EasyTrend Shield) ---
        if adx_val < self.adx_threshold:
            return StrategyDecision(signal="NO_TRADE", reason=f"Market Too Slow (ADX {adx_val:.1f})")
            
        reasons.append(f"Market Active (ADX {adx_val:.1f})")

        # --- FILTER 2: Major Trend ---
        is_bull_trend = curr_fast_ma > curr_slow_ma
        is_bear_trend = curr_fast_ma < curr_slow_ma

        # --- TRIGGER: MACD Explosive Breakout (The EasyEntry Spear) ---
        is_macd_bull = curr_macd > curr_upper
        is_macd_bear = curr_macd < curr_lower

        check_buy = direction in ("AUTO", "BUY", "BOTH")
        check_sell = direction in ("AUTO", "SELL", "BOTH")

        if check_buy and is_macd_bull and is_bull_trend:
            final_signal = "BUY"
            reasons.append("FUSION BUY: Trend + Momentum")
            confidence = 90.0
        
        elif check_sell and is_macd_bear and is_bear_trend:
            final_signal = "SELL"
            reasons.append("FUSION SELL: Trend + Momentum")
            confidence = 90.0

        # === 3. RISK MANAGEMENT ===
        sl = 0.0
        tp = 0.0
        
        if final_signal != "NO_TRADE":
            dist = atr * self.sl_atr_mult
            if final_signal == "BUY":
                sl = close - dist
                tp = close + (dist * (self.tp_atr_mult / self.sl_atr_mult))
            else:
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

easy_fusion_strategy = EasyFusionStrategy()
