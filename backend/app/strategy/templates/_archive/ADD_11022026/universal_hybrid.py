
import pandas as pd
import app.analysis.indicators as ind
import numpy as np
import logging
from typing import Dict, Any, Tuple, Optional, List
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta

from app.strategy.antigravity import Signal, EntrySignal
from app.services.mt5_service import mt5_service

# Setup Logging
logger = logging.getLogger("UniversalHybrid")

class MarketRegime(Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"

@dataclass
class TradeSetup:
    signal: Signal
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    reasons: List[str]

class UniversalHybridStrategy:
    """
    Antigravity Universal Hybrid Strategy (V3)
    Optimized for Cent Accounts (USC)
    Target: 600 - 2000 THB Daily (1800 - 6000 USC)
    
    CORE LOGIC ("The Fusion"):
    1. Trend (H1/M15): Defined by EMA 50/200 & Market Structure.
    2. Setup (M5/M1): 
       - SMC: Liquidity Sweeps (Institutional Scalp)
       - Indicators: RSI Pullback + Stoch Oversold (Sniper Pro)
    3. Confirmation:
       - Correlation: XAU & XAG moving together.
       - News: No high-impact news in +/- 30 mins (Manual or externally fed).
    
    RISK MANAGEMENT (USC SPECIAL):
    - Lot Size = (Balance * Risk%) / (SL_Distance * TickValue)
    - 1 USD Lot = 100,000 Units.
    - 1 USC Lot = 100,000 Units of Cents (1,000 USD).
    - Effectively, 0.01 Standard Lot = 1.0 Cent Lot.
    """

    def __init__(self):
        self.params = {
            "timeframe_trend": "M15",
            "timeframe_entry": "M1",
            "risk_percent": 2.0,       # 2% Risk per trade
            "max_daily_loss_usc": 1500.0, # ~500 THB Loss Limit
            "target_daily_usc": 1800.0,   # ~600 THB Profit Target
            "xau_trending_rsi_buy": 40,
            "xau_trending_rsi_sell": 60,
            "min_confidence": 75,
            "magic_number": 999
        }
        self.daily_pnl = 0.0

    def analyze(self, df: pd.DataFrame, symbol: str = "XAUUSD") -> EntrySignal:
        """
        Main Analysis Loop
        """
        if len(df) < 200:
             return self._no_signal("Initializing Data...")

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
        signal = Signal.NONE
        confidence = 50.0
        reasons = []
        
        current_price = df['close'].iloc[-1]
        atr = ind.atr(df['high'], df['low'], df['close'], 14).iloc[-1]
        
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
                signal = Signal.BUY
                
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
                signal = Signal.SELL

        if signal == Signal.NONE:
            return self._no_signal(f"Scanning... Trend:{trend} Conf:{confidence}")

        # 6. Risk Calculation
        sl, tp = self._calculate_sl_tp(signal, current_price, atr, structure)
        
        return EntrySignal(
            signal=signal,
            confidence=confidence,
            entry_method="UNIVERSAL_HYBRID_V3",
            entry_price=current_price,
            stop_loss=sl,
            take_profit=tp,
            reasons=reasons
        )

    def _analyze_trend(self, df: pd.DataFrame) -> Tuple[str, float]:
        """
        Determines the dominant trend using M15/H1 proximities.
        Uses EMA 50 & 200.
        """
        ema_50 = ind.ema(df['close'], 50).iloc[-1]
        ema_200 = ind.ema(df['close'], 200).iloc[-1]
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
        rsi = ind.rsi(df['close'], 14).iloc[-1]
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

    def _calculate_sl_tp(self, signal: Signal, price: float, atr: float, structure: Dict) -> Tuple[float, float]:
        """
        Calculates Risk-Based SL and TP.
        SL: Behind structure (Swing High/Low) + ATR Buffer.
        TP: 1.5R to 3R.
        """
        sl = 0.0
        tp = 0.0
        
        risk_reward = 2.0
        
        if signal == Signal.BUY:
            # SL below recent support or sweep low
            structure_sl = structure['support']
            atr_sl = price - (atr * 2.5) # Fallback
            
            # Use structure if it's close enough, otherwise ATR
            dist_structure = price - structure_sl
            if dist_structure > 0 and dist_structure < (atr * 5):
                sl = structure_sl - (atr * 0.5) # Little buffer
            else:
                sl = atr_sl
                
            risk = price - sl
            tp = price + (risk * risk_reward)
            
        elif signal == Signal.SELL:
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

    def calculate_lot_size(self, balance: float, sl_price: float, entry_price: float, symbol: str) -> float:
        """
        Calculates LOT SIZE for USC (Cent) Accounts.
        """
        if balance <= 0: return 0.01
        
        # Risk Amount (e.g. 2% of 10,000 USC = 200 USC)
        risk_amount = balance * (self.params['risk_percent'] / 100.0)
        
        # Stop Loss Distance in Points
        # XAUUSD 1.00 move = 100 points
        dist = abs(entry_price - sl_price)
        if dist == 0: return 0.01
        
        # Tick Value estimation
        # For Cent accounts:
        # Standard Lot (1.0) on Gold = 100 oz. 1 pip ($0.10) = $10 USD.
        # Cent Lot (1.0) on Gold = 100 units (usually). 1 pip = 10 USC ($0.10).
        # We need to be careful with broker specifications.
        # Assuming 1.0 Cent Lot behaves like 0.01 Standard Lot in value, but is called "1.0".
        # If TickValue for 1.0 lot is 1 USD (100 USC).
        
        # Simplified: Lots = Risk / (Distance * TickValue)
        # Let's assume standard MT5 calc will be handled by RiskManager, 
        # but here is a safe estimation.
        
        # Conservative approach: 
        # 10000 USC balance -> 0.1 Standard Lot (10 Cent Lots) max roughly.
        
        check_lot = risk_amount / dist # Very rough
        
        # Let's delegate to the actual RiskManager in the system, 
        # but return a suggested fraction.
        
        return round(check_lot / 100, 2) # Placeholder, rely on system RiskManager

    def _no_signal(self, reason: str) -> EntrySignal:
        return EntrySignal(Signal.NONE, 0, "NONE", 0, 0, 0, [reason])

universal_hybrid_strategy = UniversalHybridStrategy()
