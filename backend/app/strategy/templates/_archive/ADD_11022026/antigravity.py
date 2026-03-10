"""
Antigravity Strategy Engine
Momentum-based intraday trading strategy.

CORE PHILOSOPHY:
- Momentum over prediction
- Risk first, profit second
- Small consistent gains beat large volatile wins
"""
import pandas as pd
import logging
from typing import Optional, Dict, Tuple, Any
from dataclasses import dataclass
from enum import Enum

from app.data.indicators import IndicatorEngine
from app.risk.risk_manager import RiskManager

logger = logging.getLogger("AntigravityStrategy")


class Signal(Enum):
    NONE = "NONE"
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"


@dataclass
class EntrySignal:
    """Entry signal with details"""
    signal: Signal
    confidence: float  # 0-100
    entry_method: str  # "PULLBACK_EMA9", "PULLBACK_EMA21", "BREAKOUT"
    entry_price: float
    stop_loss: float
    take_profit: float
    reasons: list
    
    def is_valid(self, min_conf: float = 60) -> bool:
        return self.signal in (Signal.BUY, Signal.SELL) and self.confidence >= min_conf


class AntigravityStrategy:
    """
    Antigravity Momentum Strategy
    
    ENTRY BUY CONDITIONS:
    1. Price above EMA 9, 21, 50
    2. EMA 9 > EMA 21 > EMA 50
    3. RSI(14) > 55 OR MACD histogram expanding
    4. Volume above 20-period average
    5. Price above VWAP
    
    ENTRY SELL CONDITIONS:
    Inverse of BUY conditions
    
    ENTRY METHODS:
    - Pullback to EMA 9 or EMA 21
    - OR breakout of previous candle with volume spike
    
    STOP LOSS:
    - Default: beyond EMA 21
    - Alternative: ATR-based SL
    
    TAKE PROFIT:
    - TP1: 0.3-0.5%
    - TP2: structure or trailing EMA 9
    """
    
    # Strategy parameters
    RSI_BUY_THRESHOLD = 55
    RSI_SELL_THRESHOLD = 45
    VOLUME_THRESHOLD = 1.0  # Above average
    MIN_CONFIDENCE = 60
    
    def __init__(self, risk_manager: RiskManager = None):
        self.indicator_engine = IndicatorEngine()
        self.risk_manager = risk_manager or RiskManager()
        
    def update_parameters(self, params: Dict[str, Any]):
        """Dynamic update for optimization"""
        for k, v in params.items():
            if hasattr(self, k):
                # Ensure we cast to correct types
                val = v
                if k.endswith('_THRESHOLD') or k.endswith('_CONFIDENCE'):
                    val = float(v)
                setattr(self, k, val)
        
    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> EntrySignal:
        """
        Analyze market data and generate entry signal.
        
        Args:
            df: DataFrame with OHLCV data (must have indicators calculated)
            direction: "AUTO", "BUY", "SELL", or "BOTH"
        
        Returns:
            EntrySignal with signal details
        """
        if len(df) < 50:
            return self._no_signal("Insufficient data")
        
        # Ensure indicators are calculated
        if 'EMA_9' not in df.columns:
            df = self.indicator_engine.calculate_all(df)
        
        indicators = self.indicator_engine.get_latest_indicators(df)
        
        # Check for BUY signal
        if direction in ("AUTO", "BUY", "BOTH"):
            buy_signal = self._check_buy_conditions(df, indicators)
            if buy_signal.is_valid():
                return buy_signal
        
        # Check for SELL signal
        if direction in ("AUTO", "SELL", "BOTH"):
            sell_signal = self._check_sell_conditions(df, indicators)
            if sell_signal.is_valid():
                return sell_signal
        
        return self._no_signal("No valid signal")
    
    def _check_buy_conditions(self, df: pd.DataFrame, indicators: Dict) -> EntrySignal:
        """Check all BUY entry conditions"""
        confidence = 50  # Start neutral
        reasons = []
        entry_method = "NONE"
        
        close = indicators['close']
        ema_9 = indicators['ema_9']
        ema_21 = indicators['ema_21']
        ema_50 = indicators['ema_50']
        rsi = indicators['rsi']
        vwap = indicators['vwap']
        
        # 1. Price above EMAs (+15 each)
        if self.indicator_engine.check_price_above_emas(indicators, "BUY"):
            confidence += 15
            reasons.append("✅ Price > EMA 9,21,50")
        else:
            confidence -= 20
            reasons.append("❌ Price below EMAs")
        
        # 2. EMA alignment (+20)
        ema_aligned = self.indicator_engine.check_ema_alignment(indicators, "BUY")
        if ema_aligned:
            confidence += 20
            reasons.append("✅ EMA 9 > 21 > 50 (Trend aligned)")
        else:
            confidence -= 15
            reasons.append("❌ EMA misaligned")
        
        # 2.1 Trend Strength (ADX) (+15)
        adx = indicators.get('adx', 0)
        if adx > 25:
            confidence += 15
            reasons.append(f"✅ Strong Trend (ADX {adx:.1f} > 25)")
        elif adx < 20:
            confidence -= 20
            reasons.append(f"❌ Weak Trend/Sideways (ADX {adx:.1f} < 20)")
        
        # 2.2 Volatility Context (Bollinger Squeeze)
        bb_upper = indicators.get('bb_upper', 0)
        bb_lower = indicators.get('bb_lower', 0)
        bb_width = (bb_upper - bb_lower) / indicators.get('bb_middle', 1) * 100
        
        if bb_width < 0.2: # Very tight squeeze
            confidence -= 30
            reasons.append(f"❌ BB Squeeze (Width {bb_width:.2f}%) - Avoid Chop")
        elif close > bb_upper:
            # Overextended if RSI is also high
            if rsi > 75:
                confidence -= 20
                reasons.append("⚠️ Overextended above BB Upper + RSI High")
            else:
                confidence += 5
                reasons.append("✅ BB Upper Breakout")
        
        # 3. RSI > 55 OR MACD expanding (+15)
        rsi_ok = rsi > self.RSI_BUY_THRESHOLD
        macd_ok = self.indicator_engine.check_macd_expanding(indicators, "BUY")
        
        if rsi_ok:
            confidence += 10
            reasons.append(f"✅ RSI {rsi:.1f} > {self.RSI_BUY_THRESHOLD}")
        if macd_ok:
            confidence += 10
            reasons.append("✅ MACD histogram expanding")
        if not rsi_ok and not macd_ok:
            confidence -= 10
            reasons.append(f"❌ RSI {rsi:.1f} weak, MACD not expanding")
        
        # 4. Volume above average (+10)
        if self.indicator_engine.check_volume_above_average(indicators):
            confidence += 10
            vol_ratio = indicators['volume_ratio']
            reasons.append(f"✅ Volume {vol_ratio:.1f}x average")
        else:
            confidence -= 5
            reasons.append("⚠️ Low volume")
        
        # 5. Price above VWAP (+10)
        if self.indicator_engine.check_price_vs_vwap(indicators, "BUY"):
            confidence += 10
            reasons.append("✅ Price > VWAP")
        else:
            confidence -= 5
            reasons.append("⚠️ Price below VWAP")
        
        # Entry method detection
        if self.indicator_engine.is_pullback_to_ema(df, 9):
            entry_method = "PULLBACK_EMA9"
            confidence += 10
            reasons.append("🎯 Pullback to EMA 9 detected")
        elif self.indicator_engine.is_pullback_to_ema(df, 21):
            entry_method = "PULLBACK_EMA21"
            confidence += 5
            reasons.append("🎯 Pullback to EMA 21 detected")
        elif self.indicator_engine.is_breakout_with_volume(df) == "BUY":
            entry_method = "BREAKOUT"
            confidence += 10
            reasons.append("🎯 Volume breakout detected")
        else:
            entry_method = "TREND_CONTINUATION"
        
        # Calculate SL/TP
        atr = indicators['atr']
        
        # BTC SPECIFIC: Use ATR-based SL if symbol is BTC (more room for volatility)
        is_btc = "BTC" in df.attrs.get('symbol', '').upper()
        if is_btc:
            sl = close - (2.1 * atr)
            tp = close + (3.5 * atr)  # Aim for higher RR on BTC trends
            reasons.append("🛡️ BTC Volatility Adjusted SL (2.1x ATR)")
        else:
            sl = self.risk_manager.calculate_sl_from_ema(close, ema_21, "BUY")
            tp = self.risk_manager.calculate_tp(close, "BUY", 0.4)
        
        # Clamp confidence and cast to float for JSON serialization
        confidence = float(max(0, min(100, confidence)))
        
        return EntrySignal(
            signal=Signal.BUY if confidence >= self.MIN_CONFIDENCE else Signal.WAIT,
            confidence=float(confidence),
            entry_method=entry_method,
            entry_price=float(close),
            stop_loss=float(sl),
            take_profit=float(tp),
            reasons=reasons
        )
    
    def _check_sell_conditions(self, df: pd.DataFrame, indicators: Dict) -> EntrySignal:
        """Check all SELL entry conditions (inverse of BUY)"""
        confidence = 50
        reasons = []
        entry_method = "NONE"
        
        close = indicators['close']
        ema_9 = indicators['ema_9']
        ema_21 = indicators['ema_21']
        ema_50 = indicators['ema_50']
        rsi = indicators['rsi']
        vwap = indicators['vwap']
        
        # 1. Price below EMAs
        if self.indicator_engine.check_price_above_emas(indicators, "SELL"):
            confidence += 15
            reasons.append("✅ Price < EMA 9,21,50")
        else:
            confidence -= 20
            reasons.append("❌ Price above EMAs")
        
        # 2. EMA alignment (inverted) (+20)
        ema_aligned = self.indicator_engine.check_ema_alignment(indicators, "SELL")
        if ema_aligned:
            confidence += 20
            reasons.append("✅ EMA 9 < 21 < 50 (Downtrend)")
        else:
            confidence -= 15
            reasons.append("❌ EMA misaligned")
            
        # 2.1 Trend Strength (ADX) (+15)
        adx = indicators.get('adx', 0)
        if adx > 25:
            confidence += 15
            reasons.append(f"✅ Strong Trend (ADX {adx:.1f} > 25)")
        elif adx < 20:
            confidence -= 20
            reasons.append(f"❌ Weak Trend/Sideways (ADX {adx:.1f} < 20)")

        # 2.2 Volatility Context (Bollinger Squeeze)
        bb_upper = indicators.get('bb_upper', 0)
        bb_lower = indicators.get('bb_lower', 0)
        bb_middle = indicators.get('bb_middle', 1)
        bb_width = (bb_upper - bb_lower) / bb_middle * 100
        
        if bb_width < 0.2:
            confidence -= 30
            reasons.append(f"❌ BB Squeeze (Width {bb_width:.2f}%) - Avoid Chop")
        elif close < bb_lower:
            if rsi < 25:
                confidence -= 20
                reasons.append("⚠️ Overextended below BB Lower + RSI Low")
            else:
                confidence += 5
                reasons.append("✅ BB Lower Breakout")
        
        # 3. RSI < 45 OR MACD expanding down
        rsi_ok = rsi < self.RSI_SELL_THRESHOLD
        macd_ok = self.indicator_engine.check_macd_expanding(indicators, "SELL")
        
        if rsi_ok:
            confidence += 10
            reasons.append(f"✅ RSI {rsi:.1f} < {self.RSI_SELL_THRESHOLD}")
        if macd_ok:
            confidence += 10
            reasons.append("✅ MACD histogram expanding down")
        if not rsi_ok and not macd_ok:
            confidence -= 10
            reasons.append(f"❌ RSI {rsi:.1f} not bearish, MACD not expanding")
        
        # 4. Volume above average
        if self.indicator_engine.check_volume_above_average(indicators):
            confidence += 10
            reasons.append(f"✅ Volume {indicators['volume_ratio']:.1f}x average")
        else:
            confidence -= 5
            reasons.append("⚠️ Low volume")
        
        # 5. Price below VWAP
        if self.indicator_engine.check_price_vs_vwap(indicators, "SELL"):
            confidence += 10
            reasons.append("✅ Price < VWAP")
        else:
            confidence -= 5
            reasons.append("⚠️ Price above VWAP")
        
        # Entry method
        if self.indicator_engine.is_pullback_to_ema(df, 9):
            entry_method = "PULLBACK_EMA9"
            confidence += 10
            reasons.append("🎯 Pullback to EMA 9 detected")
        elif self.indicator_engine.is_pullback_to_ema(df, 21):
            entry_method = "PULLBACK_EMA21"
            confidence += 5
            reasons.append("🎯 Pullback to EMA 21 detected")
        elif self.indicator_engine.is_breakout_with_volume(df) == "SELL":
            entry_method = "BREAKOUT"
            confidence += 10
            reasons.append("🎯 Volume breakdown detected")
        else:
            entry_method = "TREND_CONTINUATION"
        
        # Calculate SL/TP
        atr = indicators['atr']
        
        # BTC SPECIFIC: Use ATR-based SL if symbol is BTC
        is_btc = "BTC" in df.attrs.get('symbol', '').upper()
        if is_btc:
            sl = close + (2.1 * atr)
            tp = close - (3.5 * atr)
            reasons.append("🛡️ BTC Volatility Adjusted SL (2.1x ATR)")
        else:
            sl = self.risk_manager.calculate_sl_from_ema(close, ema_21, "SELL")
            tp = self.risk_manager.calculate_tp(close, "SELL", 0.4)
        
        # Clamp confidence and cast to float
        confidence = float(max(0, min(100, confidence)))
        
        return EntrySignal(
            signal=Signal.SELL if confidence >= self.MIN_CONFIDENCE else Signal.WAIT,
            confidence=confidence,
            entry_method=entry_method,
            entry_price=float(close),
            stop_loss=float(sl),
            take_profit=float(tp),
            reasons=reasons
        )
    
    def _no_signal(self, reason: str) -> EntrySignal:
        """Return empty signal"""
        return EntrySignal(
            signal=Signal.NONE,
            confidence=0,
            entry_method="NONE",
            entry_price=0,
            stop_loss=0,
            take_profit=0,
            reasons=[reason]
        )
    
    def get_trend_filter(self, df_m15: pd.DataFrame) -> str:
        """
        Get M15 trend filter for higher timeframe confirmation.
        Returns "BULLISH", "BEARISH", or "NEUTRAL"
        """
        if len(df_m15) < 50:
            return "NEUTRAL"
        
        if 'EMA_21' not in df_m15.columns:
            df_m15 = self.indicator_engine.calculate_ema(df_m15, [21, 50])
        
        last = df_m15.iloc[-1]
        close = last['close']
        ema_21 = last.get('EMA_21', close)
        ema_50 = last.get('EMA_50', close)
        
        if close > ema_21 > ema_50:
            return "BULLISH"
        elif close < ema_21 < ema_50:
            return "BEARISH"
        return "NEUTRAL"


# Global instance
antigravity_strategy = AntigravityStrategy()
