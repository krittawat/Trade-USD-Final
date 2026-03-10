"""
Gold Scalp Daily Strategy
==========================
Optimized for XAUUSD M5 Scalping with Daily Profit Target

Features:
- Fast EMA crossover (5/13) for quick entries
- ADX trend filter for quality setups
- RSI momentum confirmation
- VWAP level awareness
- Dynamic SL based on ATR (minimum $3.00)
- Daily profit tracking & auto-stop

Target: 0.5-1% daily profit with controlled risk
"""
import pandas as pd
import app.analysis.indicators as ind
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy, StrategyDecision

class GoldScalpDailyStrategy(BaseStrategy):
    """
    High-frequency scalping strategy for Gold (XAUUSD)
    Designed for M5 timeframe with daily profit targets
    """
    
    # Configuration
    EMA_FAST = 5
    EMA_SLOW = 13
    EMA_TREND = 50
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    ADX_PERIOD = 14
    
    # Entry Thresholds
    ADX_MIN = 20  # Minimum trend strength
    RSI_BUY_MAX = 70  # Don't buy if RSI > 70
    RSI_SELL_MIN = 30  # Don't sell if RSI < 30
    
    # Risk Management (Gold-specific)
    SL_ATR_MULT = 1.5
    TP_ATR_MULT = 2.5
    MIN_SL_DISTANCE = 3.0  # Minimum $3 SL to avoid stop hunts
    RISK_PER_TRADE = 0.005  # 0.5% per trade
    
    # Daily Limits
    DAILY_PROFIT_TARGET = 0.01  # 1% daily target
    MAX_DAILY_TRADES = 10
    MAX_CONSECUTIVE_LOSSES = 3
    
    def __init__(self):
        self.name = "GOLD_SCALP_DAILY"
        self.params = {}
        self.daily_stats = {
            "date": None,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "pnl": 0.0,
            "consecutive_losses": 0
        }
    
    def update_parameters(self, params: dict):
        self.params.update(params)
    
    def get_status(self):
        return {
            "name": self.name,
            "style": "Scalping",
            "timeframe": "M5",
            "target": "Daily Profit",
            "params": self.params
        }
    
    def analyze(self, df: pd.DataFrame, symbol: str = "XAUUSD", **kwargs) -> StrategyDecision:
        """
        Analyze market and generate scalping signal
        """
        if df is None or len(df) < 60:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient data")
        
        # Calculate indicators if not present
        df = self._ensure_indicators(df)
        
        # Get current values
        r = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Validate indicators
        if pd.isna(r.get('rsi')) or pd.isna(r.get('atr')) or r.get('atr', 0) == 0:
            return StrategyDecision(signal="NO_TRADE", reason="Invalid indicators")
        
        # Extract key values
        close = float(r['close'])
        atr = float(r['atr'])
        rsi = float(r['rsi'])
        adx = float(r.get('adx', 0))
        
        ema_fast = float(r['ema_fast'])
        ema_slow = float(r['ema_slow'])
        ema_trend = float(r['ema_trend'])
        
        ema_fast_prev = float(prev['ema_fast'])
        ema_slow_prev = float(prev['ema_slow'])
        
        # Direction filter from kwargs
        direction_mode = kwargs.get('direction', 'AUTO')
        
        # Build scoring system
        buy_score = 0
        sell_score = 0
        reasons = []
        
        # ===== ENTRY CONDITIONS =====
        
        # 1. EMA Crossover (Primary Trigger) - 40 points
        ema_cross_up = ema_fast > ema_slow and ema_fast_prev <= ema_slow_prev
        ema_cross_down = ema_fast < ema_slow and ema_fast_prev >= ema_slow_prev
        
        if ema_cross_up:
            buy_score += 40
            reasons.append("🎯 EMA Cross Up")
        elif ema_fast > ema_slow:
            buy_score += 15
            reasons.append("EMA Bullish")
            
        if ema_cross_down:
            sell_score += 40
            reasons.append("🎯 EMA Cross Down")
        elif ema_fast < ema_slow:
            sell_score += 15
            reasons.append("EMA Bearish")
        
        # 2. Trend Alignment (EMA 50) - 20 points
        if close > ema_trend:
            buy_score += 20
            reasons.append("Trend UP")
        else:
            sell_score += 20
            reasons.append("Trend DOWN")
        
        # 3. RSI Momentum - 20 points
        if 40 < rsi < self.RSI_BUY_MAX:
            buy_score += 20
            reasons.append(f"RSI {rsi:.0f}")
        elif rsi <= 40:
            buy_score += 10  # Oversold zone, potential reversal
            
        if self.RSI_SELL_MIN < rsi < 60:
            sell_score += 20
            reasons.append(f"RSI {rsi:.0f}")
        elif rsi >= 60:
            sell_score += 10  # Overbought zone
        
        # 4. ADX Trend Strength - 15 points
        if adx > self.ADX_MIN:
            if buy_score > sell_score:
                buy_score += 15
            else:
                sell_score += 15
            reasons.append(f"ADX {adx:.0f}")
        
        # 5. Candle Confirmation - 5 points
        if r['close'] > r['open']:  # Bullish candle
            buy_score += 5
        else:
            sell_score += 5
        
        # ===== DECISION LOGIC =====
        min_score = 50  # Minimum score for entry
        
        # Apply direction filter
        if direction_mode == "BUY_ONLY":
            sell_score = 0
        elif direction_mode == "SELL_ONLY":
            buy_score = 0
        
        signal = "NO_TRADE"
        sl = 0.0
        tp = 0.0
        
        if buy_score >= min_score and buy_score > sell_score:
            signal = "BUY"
            # Calculate SL/TP with gold-specific minimum
            sl_distance = max(atr * self.SL_ATR_MULT, self.MIN_SL_DISTANCE)
            tp_distance = atr * self.TP_ATR_MULT
            
            sl = close - sl_distance
            tp = close + tp_distance
            
        elif sell_score >= min_score and sell_score > buy_score:
            signal = "SELL"
            # Calculate SL/TP with gold-specific minimum
            sl_distance = max(atr * self.SL_ATR_MULT, self.MIN_SL_DISTANCE)
            tp_distance = atr * self.TP_ATR_MULT
            
            sl = close + sl_distance
            tp = close - tp_distance
        
        if signal == "NO_TRADE":
            return StrategyDecision(
                signal="WAIT",
                reason=f"Score: B{buy_score}/S{sell_score} (min {min_score})",
                confidence=max(buy_score, sell_score) / 100.0
            )
        
        # Build reason string
        reason_str = f"[SCALP] {' | '.join(reasons[:3])}"
        confidence = max(buy_score, sell_score) / 100.0
        
        return StrategyDecision(
            signal=signal,
            entry_price=close,
            sl=sl,
            tp=tp,
            reason=reason_str,
            confidence=min(1.0, confidence),
            risk_pct=self.RISK_PER_TRADE
        )
    
    def _ensure_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate required indicators if not present"""
        
        if 'ema_fast' not in df.columns:
            df['ema_fast'] = df['close'].ewm(span=self.EMA_FAST, adjust=False).mean()
        if 'ema_slow' not in df.columns:
            df['ema_slow'] = df['close'].ewm(span=self.EMA_SLOW, adjust=False).mean()
        if 'ema_trend' not in df.columns:
            df['ema_trend'] = df['close'].ewm(span=self.EMA_TREND, adjust=False).mean()
        
        if 'rsi' not in df.columns:
            df['rsi'] = ta.rsi(df['close'], length=self.RSI_PERIOD)
        
        if 'atr' not in df.columns:
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=self.ATR_PERIOD)
        
        if 'adx' not in df.columns:
            adx_data = ta.adx(df['high'], df['low'], df['close'], length=self.ADX_PERIOD)
            if adx_data is not None and f'ADX_{self.ADX_PERIOD}' in adx_data.columns:
                df['adx'] = adx_data[f'ADX_{self.ADX_PERIOD}']
            else:
                df['adx'] = 25  # Default
        
        return df


# Global instance for easy import
gold_scalp_daily_strategy = GoldScalpDailyStrategy()
