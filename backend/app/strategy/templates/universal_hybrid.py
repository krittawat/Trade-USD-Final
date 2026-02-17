
import pandas as pd
import pandas_ta as ta
import numpy as np
import logging
from typing import Dict, Any, Tuple, Optional, List
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta

from .base_strategy import BaseStrategy, StrategyDecision


# Setup Logging
logger = logging.getLogger("UniversalHybrid")

class MarketRegime(Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"

@dataclass
class TradeSetup:
    signal: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    reasons: List[str]

class UniversalHybridStrategy(BaseStrategy):
    """
    Antigravity Universal Hybrid Strategy (V3)
    
    CORE LOGIC ("The Fusion"):
    1. Trend (H1/M15): Defined by EMA 50/200 & Market Structure.
    2. Setup (M5/M1): 
       - SMC: Liquidity Sweeps (Institutional Scalp)
       - Indicators: RSI Pullback + Stoch Oversold (Sniper Pro)
    3. Confirmation:
       - Volume spike detection
    
    RISK MANAGEMENT:
    - ATR-based SL/TP
    - Structure-aware stop placement
    """

    def __init__(self):
        self.name = "UNIVERSAL_HYBRID"
        self.params = {
            "risk_percent": 2.0,
            "min_confidence": 75,
        }
        self.daily_pnl = 0.0

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "style": "Trend + SMC Sweep + Sniper",
            "min_confidence": self.params['min_confidence'],
        }

    def analyze(self, df: pd.DataFrame, symbol: str = "XAUUSD", **kwargs) -> StrategyDecision:
        """
        Main Analysis Loop
        """
        if len(df) < 200:
             return StrategyDecision(signal="NO_TRADE", reason="Initializing Data...")

        # 1. Market Regime Analysis (Trend)
        trend, trend_strength = self._analyze_trend(df)
        
        # 2. SMC Structure Analysis (Sweeps)
        structure = self._analyze_structure(df)
        
        # 3. Sniper Indicators
        indicators = self._analyze_indicators(df, trend)
        
        # 4. Correlation Check (Optional but recommended)
        # Note: In backtest mode, we might not have real-time XAG data easily joined here
        # so we assume neutral unless fed.
        
        # 5. Signal Synthesis
        signal = "NONE"
        confidence = 50.0
        reasons = []
        
        current_price = df['close'].iloc[-1]
        atr = df.ta.atr(length=14).iloc[-1]
        
        # --- BUY SCENARIO ---
        if trend == "BULLISH":
            # A. SMC Sweep Buy (Aggressive)
            if structure['sweep'] == "LOW":
                confidence += 20
                reasons.append("SMC: Liquidity Sweep (Low)")
            
            # B. Sniper Pullback Buy (Conservative)
            if indicators['rsi_setup'] == "BUY":
                confidence += 15
                reasons.append(f"Sniper: RSI Pullback ({indicators['rsi']:.1f})")
                
            if indicators['stoch_setup'] == "BUY":
                confidence += 10
                reasons.append("Sniper: Stoch Oversold")
                
            # C. Volume/Momentum
            if indicators['volume_spike']:
                confidence += 10
                reasons.append("Volume: Spike Detected")

            # Decision
            if confidence >= self.params['min_confidence']:
                signal = "BUY"
                
        # --- SELL SCENARIO ---
        elif trend == "BEARISH":
            if structure['sweep'] == "HIGH":
                confidence += 20
                reasons.append("SMC: Liquidity Sweep (High)")
                
            if indicators['rsi_setup'] == "SELL":
                confidence += 15
                reasons.append(f"Sniper: RSI Throwback ({indicators['rsi']:.1f})")
                
            if indicators['stoch_setup'] == "SELL":
                confidence += 10
                reasons.append("Sniper: Stoch Overbought")
                
            if indicators['volume_spike']:
                confidence += 10
                reasons.append("Volume: Spike Detected")
                
            if confidence >= self.params['min_confidence']:
                signal = "SELL"

        if signal == "NONE":
            return StrategyDecision(signal="WAIT", reason=f"Scanning... Trend:{trend} Conf:{confidence}")

        # 6. Risk Calculation
        sl, tp = self._calculate_sl_tp(signal, current_price, atr, structure)
        
        return StrategyDecision(
            signal=signal,
            confidence=confidence / 100.0,
            entry_price=current_price,
            sl=sl,
            tp=tp,
            reason=" | ".join(reasons) if reasons else "Universal Hybrid",
        )

    def _analyze_trend(self, df: pd.DataFrame) -> Tuple[str, float]:
        """
        Determines the dominant trend using M15/H1 proximities.
        Uses EMA 50 & 200.
        """
        ema_50 = df.ta.ema(length=50).iloc[-1]
        ema_200 = df.ta.ema(length=200).iloc[-1]
        close = df['close'].iloc[-1]
        
        trend = "RANGING"
        strength = 0.0
        
        if close > ema_50 > ema_200:
            trend = "BULLISH"
            strength = 1.0
        elif close < ema_50 < ema_200:
            trend = "BEARISH"
            strength = 1.0
        elif close > ema_200:
            trend = "WEAK_BULL"
            strength = 0.5
        elif close < ema_200:
            trend = "WEAK_BEAR"
            strength = 0.5
            
        return trend, strength

    def _analyze_structure(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Detects recent highs/lows and if they were swept.
        """
        window = 20
        recent = df.iloc[-window:-1] # Exclude current forming
        
        recent_low = recent['low'].min()
        recent_high = recent['high'].max()
        
        current = df.iloc[-1]
        prev = df.iloc[-2]
        
        sweep = "NONE"
        
        # Bullish Sweep: Price dipped below Low but closed above
        if df['low'].iloc[-2] < recent_low and df['close'].iloc[-2] > recent_low:
             sweep = "LOW" # Confirmed on prev candle
        elif current['low'] < recent_low and current['close'] > recent_low:
             sweep = "LOW" # Happening now
             
        # Bearish Sweep: Price spiked above High but closed below
        if df['high'].iloc[-2] > recent_high and df['close'].iloc[-2] < recent_high:
             sweep = "HIGH"
        elif current['high'] > recent_high and current['close'] < recent_high:
             sweep = "HIGH"
             
        return {"sweep": sweep, "support": recent_low, "resistance": recent_high}

    def _analyze_indicators(self, df: pd.DataFrame, trend: str) -> Dict[str, Any]:
        """
        Calculates RSI, Stoch, Volume for Sniper entries.
        """
        rsi = df.ta.rsi(length=14).iloc[-1]
        stoch = df.ta.stoch(k=14, d=3, smooth_k=3)
        stoch_k = stoch['STOCHk_14_3_3'].iloc[-1] if stoch is not None else 50
        
        # Volume Spike
        vol_avg = df['tick_volume'].rolling(20).mean().iloc[-1]
        vol_curr = df['tick_volume'].iloc[-1]
        is_spike = vol_curr > (vol_avg * 1.5)
        
        rsi_setup = "NONE"
        stoch_setup = "NONE"
        
        # Dynamic RSI thresholds based on trend
        if "BULL" in trend:
            if rsi < 40: rsi_setup = "BUY" # Pullback in uptrend
        elif "BEAR" in trend:
            if rsi > 60: rsi_setup = "SELL" # Pullback in downtrend
            
        if stoch_k < 20: stoch_setup = "BUY"
        elif stoch_k > 80: stoch_setup = "SELL"
        
        return {
            "rsi": rsi,
            "rsi_setup": rsi_setup,
            "stoch_k": stoch_k,
            "stoch_setup": stoch_setup,
            "volume_spike": is_spike
        }

    def _calculate_sl_tp(self, signal: str, price: float, atr: float, structure: Dict) -> Tuple[float, float]:
        """
        Calculates Risk-Based SL and TP.
        SL: Behind structure (Swing High/Low) + ATR Buffer.
        TP: 1.5R to 3R.
        """
        sl = 0.0
        tp = 0.0
        
        risk_reward = 2.0
        
        if signal == "BUY":
            structure_sl = structure['support']
            atr_sl = price - (atr * 2.5)
            
            dist_structure = price - structure_sl
            if dist_structure > 0 and dist_structure < (atr * 5):
                sl = structure_sl - (atr * 0.5)
            else:
                sl = atr_sl
                
            risk = price - sl
            tp = price + (risk * risk_reward)
            
        elif signal == "SELL":
            structure_sl = structure['resistance']
            atr_sl = price + (atr * 2.5)
            
            dist_structure = structure_sl - price
            if dist_structure > 0 and dist_structure < (atr * 5):
                sl = structure_sl + (atr * 0.5)
            else:
                sl = atr_sl
                
            risk = sl - price
            tp = price - (risk * risk_reward)
            
        return sl, tp


universal_hybrid_strategy = UniversalHybridStrategy()

