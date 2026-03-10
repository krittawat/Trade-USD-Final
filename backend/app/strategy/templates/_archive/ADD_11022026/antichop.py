"""
Anti-Chop Strategy Engine
Advanced logic to avoid choppy markets and smooth equity curve.

CORE FILTERS:
1. Trend Bias (M15 EMA 50/200)
2. Anti-Chop (EMA Slope, ATR Compression, Range Expansion)
3. Volatility (ATR-based SL/TP)
"""
import pandas as pd
import numpy as np
import logging
from typing import Dict, Optional
from datetime import datetime

from app.strategy.antigravity import EntrySignal, Signal
from app.data.indicators import IndicatorEngine
from app.risk.risk_manager import RiskManager

logger = logging.getLogger("AntiChopStrategy")

class AntiChopStrategy:
    # Strategy Constants
    MIN_EMA_SLOPE = 0.15     # Slightly steeper required slope
    ATR_COMPRESSION_RATIO = 0.75 # Tighter compression check
    RANGE_LOOKBACK = 10      # More candles to check for chop
    MIN_ADX = 22             # Higher conviction required
    STRONG_TREND_ADX = 32    # Strong trend threshold
    
    def __init__(self, risk_manager: RiskManager = None):
        self.indicator_engine = IndicatorEngine()
        self.risk_manager = risk_manager or RiskManager()

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> EntrySignal:
        """
        Analyze market for high-quality, non-choppy setups.
        """
        if len(df) < 200:
            return self._no_signal("Insufficient data")

        # 1. Ensure Indicators
        if 'EMA_200' not in df.columns:
            df = self.indicator_engine.calculate_all(df)
            
        indicators = self.indicator_engine.get_latest_indicators(df)
        
        # 2. Anti-Chop Filters (CRITICAL)
        is_choppy, chop_reason = self._check_chop_conditions(df, indicators)
        if is_choppy:
            return self._no_signal(f"CHOP: {chop_reason}")

        # 3. Determine Bias (Trend)
        bias = self._check_trend_bias(df, indicators)
        if bias == "NEUTRAL":
            return self._no_signal("NEUTRAL: No clear trend bias")
            
        # 4. Check Entries
        if bias == "BULLISH" and direction in ("AUTO", "BUY", "BOTH"):
            return self._check_buy_setup(df, indicators)
        elif bias == "BEARISH" and direction in ("AUTO", "SELL", "BOTH"):
            return self._check_sell_setup(df, indicators)
            
        return self._no_signal("Waiting for pullback setup")

    def _check_chop_conditions(self, df: pd.DataFrame, indicators: Dict) ->(bool, str):
        """
        Returns (True, Reason) if market is choppy.
        """
        # A. ATR Compression Filter
        current_atr = indicators.get('atr', 0)
        if 'ATR_14' in df.columns:
            # Check if current ATR is significantly lower than recent average (suggests lack of range)
            avg_atr = df['ATR_14'].rolling(window=30).mean().iloc[-1]
            if avg_atr > 0 and current_atr < (avg_atr * self.ATR_COMPRESSION_RATIO):
                return True, f"Low Volatility (ATR {current_atr:.2f} < {avg_atr*self.ATR_COMPRESSION_RATIO:.2f})"
        
        # B. EMA Slope Filter (M5 EMA 50)
        ema_series = df['EMA_50']
        # Looking at more history to ensure it's not a temporary spike
        slope = (ema_series.iloc[-1] - ema_series.iloc[-10]) / 10
        if abs(slope) < self.MIN_EMA_SLOPE:
             return True, f"Flat Trend (EMA Slope {slope:.4f})"
             
        # C. ADX Chop Filter
        adx = indicators.get('adx', 0)
        if adx < self.MIN_ADX:
            return True, f"Weak Trend Intensity (ADX {adx:.1f})"

        # D. Price Range Overlap (Check if last 5 candles are in a tight range)
        last_5 = df.tail(self.RANGE_LOOKBACK)
        range_pct = (last_5['high'].max() - last_5['low'].min()) / indicators['close']
        if range_pct < 0.001: # 0.1% range - too tight
            return True, "Price Congestion (Tight Range)"

        return False, ""

    def _check_trend_bias(self, df: pd.DataFrame, indicators: Dict) -> str:
        """
        Multi-Layer Trend Filter
        """
        close = indicators['close']
        ema_50 = indicators['ema_50']
        ema_200 = indicators['ema_200']
        vwap = indicators.get('vwap', 0)
        
        # Bullish: Price > EMA 50 > EMA 200 and Price > VWAP
        if close > ema_50 > ema_200:
            if vwap > 0 and close > vwap:
                return "BULLISH"
            elif vwap == 0:
                return "BULLISH"
                
        # Bearish: Price < EMA 50 < EMA 200 and Price < VWAP
        elif close < ema_50 < ema_200:
            if vwap > 0 and close < vwap:
                return "BEARISH"
            elif vwap == 0:
                return "BEARISH"
            
        return "NEUTRAL"

    def _check_buy_setup(self, df: pd.DataFrame, indicators: Dict) -> EntrySignal:
        close = indicators['close']
        ema_21 = indicators['ema_21']
        ema_50 = indicators['ema_50']
        rsi = indicators['rsi']
        atr = indicators['atr']
        macd_hist = indicators.get('macd_hist', 0)
        vol_ratio = indicators.get('volume_ratio', 1.0)
        adx = indicators.get('adx', 0)
        
        confidence = 0
        reasons = []
        
        # 1. Entry Zone (EMA 21 Pullback)
        dist_ema21 = (close - ema_21) / close
        if -0.001 <= dist_ema21 <= 0.002: # Within 0.2% of EMA21
            confidence += 40
            reasons.append("EMA 21 Pullback Zone")
        elif close <= ema_21 and close > ema_50: # Siting between 21 and 50
            confidence += 30
            reasons.append("EMA 21-50 Support Zone")
        
        # 2. RSI Recovery (Avoid overbought entry, prefer reversal from mid)
        if 45 <= rsi <= 65:
            confidence += 20
            reasons.append(f"RSI Momentum Balanced ({rsi:.0f})")
            
        # 3. MACD Momentum (Prefer positive histogram growth)
        if macd_hist > indicators.get('macd_hist_prev', 0):
            confidence += 15
            reasons.append("MACD Histogram Expansion")
            
        # 4. Volume Confirmation
        if vol_ratio > 1.1:
            confidence += 15
            reasons.append(f"Volume Surge x{vol_ratio:.1f}")
        
        # 5. ADX Strength
        if adx > self.STRONG_TREND_ADX:
            confidence += 10
            reasons.append(f"Strong Trend Power (ADX {adx:.0f})")

        # Dynamic SL/TP calculation
        is_btc = "BTC" in df.attrs.get('symbol', '').upper()
        
        # SL: ATR-based with multiplier
        sl_mult = 2.0 if is_btc else 1.8
        sl = close - (atr * sl_mult)
        
        # TP: Smart RR based on ADX
        # If trend is strong, aim for higher RR
        tp_mult = 3.5 if adx > self.STRONG_TREND_ADX else 2.5
        if is_btc: tp_mult += 1.0 # Wider for BTC
        
        tp = close + (atr * tp_mult)
        
        return EntrySignal(
            signal=Signal.BUY if confidence >= 60 else Signal.WAIT,
            confidence=float(confidence),
            entry_method="ANTICHOP_PULLBACK_V2",
            entry_price=float(close),
            stop_loss=float(sl),
            take_profit=float(tp),
            reasons=reasons
        )

    def _check_sell_setup(self, df: pd.DataFrame, indicators: Dict) -> EntrySignal:
        close = indicators['close']
        ema_21 = indicators['ema_21']
        ema_50 = indicators['ema_50']
        rsi = indicators['rsi']
        atr = indicators['atr']
        macd_hist = indicators.get('macd_hist', 0)
        vol_ratio = indicators.get('volume_ratio', 1.0)
        adx = indicators.get('adx', 0)
        
        confidence = 0
        reasons = []
        
        # 1. Entry Zone
        dist_ema21 = (ema_21 - close) / close
        if -0.001 <= dist_ema21 <= 0.002:
            confidence += 40
            reasons.append("EMA 21 Pullback Zone")
        elif close >= ema_21 and close < ema_50:
            confidence += 30
            reasons.append("EMA 21-50 Support Zone")
                 
        if 35 <= rsi <= 55:
            confidence += 20
            reasons.append(f"RSI Momentum Balanced ({rsi:.0f})")
            
        if macd_hist < indicators.get('macd_hist_prev', 0):
            confidence += 15
            reasons.append("MACD Histogram Expansion")
            
        if vol_ratio > 1.1:
            confidence += 15
            reasons.append(f"Volume Surge x{vol_ratio:.1f}")

        if adx > self.STRONG_TREND_ADX:
            confidence += 10
            reasons.append(f"Strong Trend Power (ADX {adx:.0f})")

        is_btc = "BTC" in df.attrs.get('symbol', '').upper()
        
        sl_mult = 2.0 if is_btc else 1.8
        sl = close + (atr * sl_mult)
        
        tp_mult = 3.5 if adx > self.STRONG_TREND_ADX else 2.5
        if is_btc: tp_mult += 1.0
        
        tp = close - (atr * tp_mult)
        
        return EntrySignal(
            signal=Signal.SELL if confidence >= 60 else Signal.WAIT,
            confidence=float(confidence),
            entry_method="ANTICHOP_PULLBACK_V2",
            entry_price=float(close),
            stop_loss=float(sl),
            take_profit=float(tp),
            reasons=reasons
        )

    def _no_signal(self, reason: str) -> EntrySignal:
        return EntrySignal(Signal.NONE, 0, "NONE", 0, 0, 0, [reason])

# Global Instance
antichop_strategy = AntiChopStrategy()
