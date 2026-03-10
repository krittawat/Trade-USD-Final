"""
Anti-Chop Strategy Engine
Advanced logic to avoid choppy markets and smooth equity curve.

CORE FILTERS:
1. Trend Bias (EMA 50/200)
2. Anti-Chop (EMA Slope, ATR Compression, Range Expansion)
3. Volatility (ATR-based SL/TP)
"""
import pandas as pd
import app.analysis.indicators as ind
import numpy as np
import logging
from typing import Dict, Optional, Tuple
from .base_strategy import BaseStrategy, StrategyDecision

logger = logging.getLogger("AntiChopStrategy")


class AntiChopStrategy(BaseStrategy):
    # Strategy Constants
    MIN_EMA_SLOPE = 0.15     # Slightly steeper required slope
    ATR_COMPRESSION_RATIO = 0.75  # Tighter compression check
    RANGE_LOOKBACK = 10      # More candles to check for chop
    MIN_ADX = 22             # Higher conviction required
    STRONG_TREND_ADX = 32    # Strong trend threshold

    def __init__(self):
        self.name = "ANTICHOP"
        self.params = {}

    def get_status(self) -> Dict:
        return {
            "name": self.name,
            "style": "Anti-Chop Trend Pullback",
            "min_adx": self.MIN_ADX,
            "strong_adx": self.STRONG_TREND_ADX,
        }

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO", **kwargs) -> StrategyDecision:
        """
        Analyze market for high-quality, non-choppy setups.
        """
        if df is None or len(df) < 200:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient data (need 200+)")

        # 1. Ensure Indicators
        df = self._ensure_indicators(df)
        indicators = self._get_latest(df)

        # 2. Anti-Chop Filters (CRITICAL)
        is_choppy, chop_reason = self._check_chop_conditions(df, indicators)
        if is_choppy:
            return StrategyDecision(signal="WAIT", reason=f"CHOP: {chop_reason}")

        # 3. Determine Bias (Trend)
        bias = self._check_trend_bias(indicators)
        if bias == "NEUTRAL":
            return StrategyDecision(signal="WAIT", reason="NEUTRAL: No clear trend bias")

        # 4. Check Entries
        if bias == "BULLISH" and direction in ("AUTO", "BUY", "BOTH"):
            return self._check_buy_setup(df, indicators)
        elif bias == "BEARISH" and direction in ("AUTO", "SELL", "BOTH"):
            return self._check_sell_setup(df, indicators)

        return StrategyDecision(signal="WAIT", reason="Waiting for pullback setup")

    def _ensure_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all required indicators using pandas_ta."""
        if 'EMA_50' not in df.columns:
            df['EMA_50'] = ta.ema(df['close'], length=50)
        if 'EMA_200' not in df.columns:
            df['EMA_200'] = ta.ema(df['close'], length=200)
        if 'EMA_21' not in df.columns:
            df['EMA_21'] = ta.ema(df['close'], length=21)
        if 'RSI_14' not in df.columns:
            df['RSI_14'] = ta.rsi(df['close'], length=14)
        if 'ATR_14' not in df.columns:
            df['ATR_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        if 'ADX_14' not in df.columns:
            adx = ta.adx(df['high'], df['low'], df['close'], length=14)
            if adx is not None:
                df['ADX_14'] = adx['ADX_14']
            else:
                df['ADX_14'] = 0
        # MACD
        if 'MACD_hist' not in df.columns:
            macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
            if macd is not None:
                df['MACD_hist'] = macd.iloc[:, 1]  # Histogram
            else:
                df['MACD_hist'] = 0
        # VWAP (manual robust calc)
        if 'vwap' not in df.columns:
            vol = df['tick_volume'] if 'tick_volume' in df.columns else df.get('volume', pd.Series([1]*len(df)))
            tp = (df['high'] + df['low'] + df['close']) / 3
            cum_vol = vol.cumsum()
            cum_pv = (tp * vol).cumsum()
            df['vwap'] = cum_pv / cum_vol.replace(0, 1)
        # Volume ratio
        if 'Vol_Ratio' not in df.columns:
            vol = df['tick_volume'] if 'tick_volume' in df.columns else df.get('volume', pd.Series([1]*len(df)))
            vol_avg = vol.rolling(20).mean()
            df['Vol_Ratio'] = vol / vol_avg.replace(0, 1)
        return df

    def _get_latest(self, df: pd.DataFrame) -> Dict:
        """Extract the latest indicator values."""
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        return {
            'close': float(last['close']),
            'ema_21': float(last.get('EMA_21', 0)),
            'ema_50': float(last.get('EMA_50', 0)),
            'ema_200': float(last.get('EMA_200', 0)),
            'rsi': float(last.get('RSI_14', 50)),
            'atr': float(last.get('ATR_14', 0)),
            'adx': float(last.get('ADX_14', 0)),
            'macd_hist': float(last.get('MACD_hist', 0)),
            'macd_hist_prev': float(prev.get('MACD_hist', 0)),
            'vwap': float(last.get('vwap', 0)),
            'volume_ratio': float(last.get('Vol_Ratio', 1.0)),
        }

    def _check_chop_conditions(self, df: pd.DataFrame, indicators: Dict) -> Tuple[bool, str]:
        """
        Returns (True, Reason) if market is choppy.
        """
        # A. ATR Compression Filter
        current_atr = indicators.get('atr', 0)
        if 'ATR_14' in df.columns:
            avg_atr = df['ATR_14'].rolling(window=30).mean().iloc[-1]
            if avg_atr > 0 and current_atr < (avg_atr * self.ATR_COMPRESSION_RATIO):
                return True, f"Low Volatility (ATR {current_atr:.2f} < {avg_atr*self.ATR_COMPRESSION_RATIO:.2f})"

        # B. EMA Slope Filter (EMA 50)
        ema_series = df['EMA_50']
        slope = (ema_series.iloc[-1] - ema_series.iloc[-10]) / 10
        if abs(slope) < self.MIN_EMA_SLOPE:
            return True, f"Flat Trend (EMA Slope {slope:.4f})"

        # C. ADX Chop Filter
        adx = indicators.get('adx', 0)
        if adx < self.MIN_ADX:
            return True, f"Weak Trend Intensity (ADX {adx:.1f})"

        # D. Price Range Overlap
        last_n = df.tail(self.RANGE_LOOKBACK)
        range_pct = (last_n['high'].max() - last_n['low'].min()) / indicators['close']
        if range_pct < 0.001:
            return True, "Price Congestion (Tight Range)"

        return False, ""

    def _check_trend_bias(self, indicators: Dict) -> str:
        close = indicators['close']
        ema_50 = indicators['ema_50']
        ema_200 = indicators['ema_200']
        vwap = indicators.get('vwap', 0)

        if close > ema_50 > ema_200:
            if vwap > 0 and close > vwap:
                return "BULLISH"
            elif vwap == 0:
                return "BULLISH"
        elif close < ema_50 < ema_200:
            if vwap > 0 and close < vwap:
                return "BEARISH"
            elif vwap == 0:
                return "BEARISH"
        return "NEUTRAL"

    def _check_buy_setup(self, df: pd.DataFrame, indicators: Dict) -> StrategyDecision:
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
        if -0.001 <= dist_ema21 <= 0.002:
            confidence += 40
            reasons.append("EMA 21 Pullback Zone")
        elif close <= ema_21 and close > ema_50:
            confidence += 30
            reasons.append("EMA 21-50 Support Zone")

        # 2. RSI Recovery
        if 45 <= rsi <= 65:
            confidence += 20
            reasons.append(f"RSI Balanced ({rsi:.0f})")

        # 3. MACD Momentum
        if macd_hist > indicators.get('macd_hist_prev', 0):
            confidence += 15
            reasons.append("MACD Hist Expanding")

        # 4. Volume Confirmation
        if vol_ratio > 1.1:
            confidence += 15
            reasons.append(f"Volume x{vol_ratio:.1f}")

        # 5. ADX Strength
        if adx > self.STRONG_TREND_ADX:
            confidence += 10
            reasons.append(f"Strong ADX ({adx:.0f})")

        # Dynamic SL/TP
        sl_mult = 1.8
        sl = close - (atr * sl_mult)
        tp_mult = 3.5 if adx > self.STRONG_TREND_ADX else 2.5
        tp = close + (atr * tp_mult)

        signal = "BUY" if confidence >= 60 else "WAIT"
        return StrategyDecision(
            signal=signal,
            entry_price=close,
            sl=sl,
            tp=tp,
            reason=" | ".join(reasons) if reasons else "Scanning...",
            confidence=float(confidence) / 100.0,
        )

    def _check_sell_setup(self, df: pd.DataFrame, indicators: Dict) -> StrategyDecision:
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

        dist_ema21 = (ema_21 - close) / close
        if -0.001 <= dist_ema21 <= 0.002:
            confidence += 40
            reasons.append("EMA 21 Pullback Zone")
        elif close >= ema_21 and close < ema_50:
            confidence += 30
            reasons.append("EMA 21-50 Resistance Zone")

        if 35 <= rsi <= 55:
            confidence += 20
            reasons.append(f"RSI Balanced ({rsi:.0f})")

        if macd_hist < indicators.get('macd_hist_prev', 0):
            confidence += 15
            reasons.append("MACD Hist Expanding")

        if vol_ratio > 1.1:
            confidence += 15
            reasons.append(f"Volume x{vol_ratio:.1f}")

        if adx > self.STRONG_TREND_ADX:
            confidence += 10
            reasons.append(f"Strong ADX ({adx:.0f})")

        sl_mult = 1.8
        sl = close + (atr * sl_mult)
        tp_mult = 3.5 if adx > self.STRONG_TREND_ADX else 2.5
        tp = close - (atr * tp_mult)

        signal = "SELL" if confidence >= 60 else "WAIT"
        return StrategyDecision(
            signal=signal,
            entry_price=close,
            sl=sl,
            tp=tp,
            reason=" | ".join(reasons) if reasons else "Scanning...",
            confidence=float(confidence) / 100.0,
        )


# Global Instance
antichop_strategy = AntiChopStrategy()
