import requests
import pandas_ta as ta
import pandas as pd
from typing import Dict, Any
from app.strategy.base_strategy import BaseStrategy, StrategyDecision
from app.data.indicators import IndicatorEngine
from app.strategy.antigravity import Signal

class HistoricalAdaptiveStrategy(BaseStrategy):
    """
    Bot 20012069 Strategy: Historical Adaptive
    
    Philosophy:
    1. Analyze Market Regime (Trend vs Range) using longer history (200 bars).
    2. Adapt strategy mode dynamically:
       - Strong Trend: Breakout / EMA Trend Following
       - Range / Chop: Mean Reversion (Bollinger/RSI)
    3. Learn from volatility (ATR) to adjust stops dynamically.
    """

    def __init__(self):
        self.indicator_engine = IndicatorEngine()

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> StrategyDecision:
        if df is None or len(df) < 200:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient History (Need 200+)")

        # 1. Feature Engineering (Historical Context)
        # We need extended indicators to determine regime
        # Adx for trend strength
        df['adx'] = ta.adx(df['high'], df['low'], df['close'], length=14)['ADX_14']
        
        # Bollinger Bands for volatility / range
        bb = ta.bbands(df['close'], length=20, std=2)
        df['bb_upper'] = bb['BBU_20_2.0']
        df['bb_lower'] = bb['BBL_20_2.0']
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['close']
        
        # Long term MA for bias
        df['ema_200'] = ta.ema(df['close'], length=200)
        df['ema_50'] = ta.ema(df['close'], length=50)
        
        # Recent Volatility
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)

        current = df.iloc[-1]
        prev = df.iloc[-2]

        # 2. Determine Market Regime
        is_trending = current['adx'] > 25
        trend_direction = "BULLISH" if current['close'] > current['ema_200'] else "BEARISH"
        volatility_high = current['atr'] > (current['close'] * 0.002) # 0.2% volatility threshold (approx)

        signal = "NO_TRADE"
        confidence = 0.0
        reasons = []
        entry_method = ""
        
        # 3. Adopt Strategy based on Regime
        
        # --- SCENARIO A: STRONG TREND (ADX > 25) ---
        if is_trending:
            reasons.append(f"Regime: TRENDING ({trend_direction}, ADX {current['adx']:.1f})")
            
            if trend_direction == "BULLISH":
                # Pullback Entry: Price dips near EMA 50 but stays above
                # Or Breakout: Close crosses above recent High (simplified here as above EMA 9/21 aligned)
                ema_aligned = current['ema_9'] > current['ema_21'] > current['ema_50']
                
                if ema_aligned and (direction == "AUTO" or direction == "BUY"):
                    signal = "BUY"
                    entry_method = "Trend Following (EMA Alignment)"
                    confidence = 85.0
                    reasons.append("EMA 9/21/50 Aligned Bullish")
            
            elif trend_direction == "BEARISH":
                ema_aligned = current['ema_9'] < current['ema_21'] < current['ema_50']
                
                if ema_aligned and (direction == "AUTO" or direction == "SELL"):
                    signal = "SELL"
                    entry_method = "Trend Following (EMA Alignment)"
                    confidence = 85.0
                    reasons.append("EMA 9/21/50 Aligned Bearish")

        # --- SCENARIO B: RANGING / CHOP (ADX < 25) ---
        else:
            reasons.append(f"Regime: RANGING (ADX {current['adx']:.1f})")
            
            # Mean Reversion using Bollinger Bands
            # Buy at Lower Band, Sell at Upper Band
            
            if current['close'] <= current['bb_lower'] and (direction == "AUTO" or direction == "BUY"):
                # Check for reversal candle (Current Close > Open)
                if current['close'] > current['open']:
                    signal = "BUY"
                    entry_method = "Mean Reversion (BB Lower)"
                    confidence = 70.0
                    reasons.append("Price Rejection at Lower BB")

            elif current['close'] >= current['bb_upper'] and (direction == "AUTO" or direction == "SELL"):
                if current['close'] < current['open']:
                    signal = "SELL"
                    entry_method = "Mean Reversion (BB Upper)"
                    confidence = 70.0
                    reasons.append("Price Rejection at Upper BB")

        # 4. Filter by User Direction Preference
        if direction != "AUTO" and signal != direction and signal != "NO_TRADE":
            signal = "NO_TRADE"
            reasons.append(f"Filtered by Direction {direction}")

        # 5. Risk Calculation (Adaptive SL/TP)
        entry_price = current['close']
        atr = current['atr']
        sl = 0.0
        tp = 0.0
        
        if signal == "BUY":
            # Trending: Wider stop, higher target
            if is_trending:
                sl = entry_price - (atr * 2.0)
                tp = entry_price + (atr * 3.0) 
            else:
                # Ranging: Tight stop, target opposite band (approx 2x ATR)
                sl = entry_price - (atr * 1.5)
                tp = entry_price + (atr * 1.5)
                
        elif signal == "SELL":
            if is_trending:
                sl = entry_price + (atr * 2.0)
                tp = entry_price - (atr * 3.0)
            else:
                sl = entry_price + (atr * 1.5)
                tp = entry_price - (atr * 1.5)

        return StrategyDecision(
            signal=signal,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            reason=f"{entry_method} | {', '.join(reasons)}",
            confidence=confidence,
            risk_pct=1.0
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": "Historical Adaptive",
            "version": "1.0",
            "regime_lookback": 200
        }

historical_adaptive_strategy = HistoricalAdaptiveStrategy()
