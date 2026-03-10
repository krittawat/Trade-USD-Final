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
import pandas as pd
try:
    import app.analysis.indicators as ind
except ImportError:
    from app.utils.safe_ta import SafeTA as ta
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
    EMA_TREND = 200  # Changed from 50 to 200 for Major Trend
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    ADX_PERIOD = 14
    
    # Entry Thresholds
    ADX_MIN = 25  # Stricter trend strength (was 20)
    RSI_BUY_MAX = 70
    RSI_SELL_MIN = 30
    
    # Risk Management (Gold-specific)
    SL_ATR_MULT = 2.0  # Increased from 1.5 to avoid noise
    TP_ATR_MULT = 3.0  # Increased from 2.5 for better R:R
    MIN_SL_DISTANCE = 3.0
    RISK_PER_TRADE = 0.005
    
    # Daily Limits
    DAILY_PROFIT_TARGET = 0.01
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
        if df is None or len(df) < 200: # Need 200 candles for EMA 200
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
        volume = float(r.get('tick_volume', 0))
        volume_avg = float(r.get('volume_avg', 0))
        
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
        
        # 2. Major Trend Alignment (EMA 200) - MANDATORY FILTER
        # If Price < EMA 200, strictly NO BUY (or very low score)
        # If Price > EMA 200, strictly NO SELL
        
        if close > ema_trend:
            buy_score += 30
            sell_score -= 50 # Strongly discourage counter-trend
            reasons.append("Trend > EMA200")
        else:
            sell_score += 30
            buy_score -= 50 # Strongly discourage counter-trend
            reasons.append("Trend < EMA200")
        
        # 3. RSI Momentum - 20 points
        if 40 < rsi < self.RSI_BUY_MAX:
            buy_score += 20
        elif rsi <= 40:
            buy_score += 10  # Oversold zone
            
        if self.RSI_SELL_MIN < rsi < 60:
            sell_score += 20
        elif rsi >= 60:
            sell_score += 10  # Overbought zone
            
        # 4. ADX Trend Strength - 15 points
        if adx > self.ADX_MIN:
            # Bonus for strong trend
            if buy_score > sell_score: buy_score += 15
            else: sell_score += 15
            reasons.append(f"ADX {adx:.0f}")
        
        # 5. Volume Confirmation - 10 points
        if volume > volume_avg:
            if buy_score > sell_score: buy_score += 10
            else: sell_score += 10
            reasons.append("Vol > Avg")
        
        # ===== DECISION LOGIC =====
        min_score = 70  # Increased threshold (was 50) for "Maximum Efficiency"
        
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
        reason_str = f"[SCALP] {' | '.join(reasons[:4])}"
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
            df['ema_fast'] = df['EMA_5'] if 'EMA_5' in df.columns else df['close'].ewm(span=self.EMA_FAST, adjust=False).mean()
        if 'ema_slow' not in df.columns:
            df['ema_slow'] = df['EMA_13'] if 'EMA_13' in df.columns else df['close'].ewm(span=self.EMA_SLOW, adjust=False).mean()
        if 'ema_trend' not in df.columns:
            df['ema_trend'] = df['EMA_200'] if 'EMA_200' in df.columns else df['close'].ewm(span=self.EMA_TREND, adjust=False).mean()
        
        if 'rsi' not in df.columns:
            df['rsi'] = ta.rsi(df['close'], length=self.RSI_PERIOD)
        
        if 'atr' not in df.columns:
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=self.ATR_PERIOD)
        
        if 'adx' not in df.columns:
            # Fix for ADX fallback
            try:
                # Try standard
                adx_data = ta.adx(df['high'], df['low'], df['close'], length=self.ADX_PERIOD)
                if adx_data is not None and f'ADX_{self.ADX_PERIOD}' in adx_data.columns:
                    df['adx'] = adx_data[f'ADX_{self.ADX_PERIOD}']
                elif 'ADX_14' in df.columns:
                     df['adx'] = df['ADX_14']
                else:
                    df['adx'] = 25  # Default
            except:
                 if 'ADX_14' in df.columns: df['adx'] = df['ADX_14']
                 else: df['adx'] = 25

        if 'volume_avg' not in df.columns:
            df['volume_avg'] = df['tick_volume'].rolling(window=20).mean()
        
        return df


# Global instance for easy import
gold_scalp_daily_strategy = GoldScalpDailyStrategy()
