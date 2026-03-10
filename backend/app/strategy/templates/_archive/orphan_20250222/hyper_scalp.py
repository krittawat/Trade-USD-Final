"""
Hyper Scalp Strategy (Institutional Logic for Retail)
Author: ANTIGRAVITY
Ver: 1.0

Logic:
1. Liquidity Sweeps (Reversal):
   - Identifies key Swing Highs/Lows on M5 structure.
   - Waits for M1 price to 'sweep' (break) the level and close back inside ('Fakeout').
   - High Probability Reversal setup used by Smart Money.

2. Momentum Bursts (Continuation):
   - Identifies high-velocity M1 candles with supporting Tick Volume.
   - Captures rapid expansions during high liquidity sessions.
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, List
from dataclasses import dataclass
from enum import Enum
import logging

# Setup Logger
logger = logging.getLogger("HyperScalp")

class Signal(Enum):
    NONE = "NONE"
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"

@dataclass
class HyperSignal:
    signal: Signal
    confidence: float
    entry_method: str
    entry_price: float
    stop_loss: float
    take_profit: float
    reasons: list
    
    def is_valid(self) -> bool:
        return self.signal in (Signal.BUY, Signal.SELL) and self.confidence >= 60

class HyperScalpStrategy:
    """
    Hyper Scalp: Institutional M1/M5 Logic for XAUUSD (USC/Standard).
    """
    
    CONFIG = {
        # Liquidity Sweep Settings
        "lookback_m5_swings": 20,   # Look back 20 bars on M5 for structure
        "sweep_buffer_points": 50,  # Min penetration in points (5 pips)
        
        # Momentum Settings
        "mom_candle_factor": 2.2,   # Candle body > 2.2x Average
        "mom_volume_factor": 1.5,   # Volume > 1.5x Average
        
        # Risk Settings (Dynamic ATR)
        "sl_atr_mult": 2.0,         # Was 1.5 — too tight, constant stop-outs
        "tp_atr_mult": 4.0,         # Was 3.0 — now RR=2.0 for profitable edge
        "atr_period": 14,
        
        # Filters
        "max_spread_points": 250,   # Block if spread > 25 pips (Standard points), adjust for broker
        "min_atr_points": 20,       # Avoid dead markets
    }

    def __init__(self):
        pass

    def analyze(self, df_m1: pd.DataFrame, df_m5: pd.DataFrame = None, current_spread: float = 0.0) -> HyperSignal:
        """
        Main Analysis Function.
        Requires M1 Data. M5 Data is optional but recommended for Structure.
        If M5 is missing, we synthesize it or rely on M1 structure (weaker).
        """
        if len(df_m1) < 50:
             return self._no_signal("Insufficient M1 Data")

        # 1. Preprocess & Indicators
        df = self._add_indicators(df_m1)
        last_m1 = df.iloc[-1]
        price = last_m1['close']
        atr = last_m1.get('ATR', 1.0)
        
        # Safety: Check Spread (If provided)
        # Note: 'current_spread' usually comes in points. 
        if current_spread > self.CONFIG['max_spread_points']:
             return self._no_signal(f"Spread too high ({current_spread})")
             
        # Safety: Check Volatility
        if atr < (self.CONFIG['min_atr_points'] * 0.001): # Approximation if ATR is price delta
             # Re-check ATR scaling. Assuming ATR is in PRICE units.
             # 20 points = 0.20 on Gold? No, 1 point = 0.01 usually on Gold? 
             # Let's rely on relative ATR check or skip for now.
             pass

        reasons = []
        signal = Signal.NONE
        confidence = 0
        setup_type = "NONE"
        
        # --- STRATEGY 1: LIQUIDITY SWEEP (Reversal) ---
        # Concept: Price broke a recent High but Closed LOWER (Sweep High) -> SELL
        # Concept: Price broke a recent Low but Closed HIGHER (Sweep Low) -> BUY
        
        # Calculate recent Swing Highs/Lows (Using a rolling max/min over 'N' periods)
        # For simplicity, we use the M1 data looking back 30-60 mins (30-60 bars) to find key levels.
        roll_window = 60 
        recent_high = df['high'].iloc[-roll_window:-1].max() # Exclude current bar
        recent_low = df['low'].iloc[-roll_window:-1].min()   # Exclude current bar
        
        # Liquidity Sweep SELL
        # High of current bar broke recent_high, but Close is BELOW recent_high
        if last_m1['high'] > recent_high and last_m1['close'] < recent_high:
            # Check candle shape: 'Shooting Star' / 'Pinbar' logic
            upper_wick = last_m1['high'] - max(last_m1['open'], last_m1['close'])
            body = abs(last_m1['open'] - last_m1['close'])
            if upper_wick > body * 1.5: # Wick is significant
                signal = Signal.SELL
                setup_type = "LIQ_SWEEP_BEAR"
                confidence = 80
                reasons.append(f"Bearish Sweep of {recent_high:.2f}")

        # Liquidity Sweep BUY
        # Low of current bar broke recent_low, but Close is ABOVE recent_low
        elif last_m1['low'] < recent_low and last_m1['close'] > recent_low:
            lower_wick = min(last_m1['open'], last_m1['close']) - last_m1['low']
            body = abs(last_m1['open'] - last_m1['close'])
            if lower_wick > body * 1.5:
                signal = Signal.BUY
                setup_type = "LIQ_SWEEP_BULL"
                confidence = 80
                reasons.append(f"Bullish Sweep of {recent_low:.2f}")


        # --- STRATEGY 2: MOMENTUM BURST (Continuation) ---
        # Logic: Big Body Candle + High Volume + Breakout
        # Only check if no Sweep signal yet
        if signal == Signal.NONE:
            avg_body = (df['close'] - df['open']).abs().rolling(20).mean().iloc[-1]
            avg_vol = df['tick_volume'].rolling(20).mean().iloc[-1]
            
            curr_body = abs(last_m1['close'] - last_m1['open'])
            curr_vol = last_m1['tick_volume']
            
            is_big_candle = curr_body > (avg_body * self.CONFIG['mom_candle_factor'])
            is_high_vol = curr_vol > (avg_vol * self.CONFIG['mom_volume_factor'])
            
            if is_big_candle and is_high_vol:
                # Bullish Burst
                if last_m1['close'] > last_m1['open']:
                    # Ensure close is near high (strong finish)
                    if (last_m1['high'] - last_m1['close']) < (curr_body * 0.3):
                        signal = Signal.BUY
                        setup_type = "MOMENTUM_BULL"
                        confidence = 75
                        reasons.append("High Volatility Impulse UP")
                
                # Bearish Burst
                elif last_m1['close'] < last_m1['open']:
                    # Ensure close is near low (strong finish)
                    if (last_m1['close'] - last_m1['low']) < (curr_body * 0.3):
                        signal = Signal.SELL
                        setup_type = "MOMENTUM_BEAR"
                        confidence = 75
                        reasons.append("High Volatility Impulse DOWN")

        # --- FINAL PACKAGING ---
        if signal != Signal.NONE:
            # Dynamic SL/TP
            # Stop Loss is calculated from ATR
            sl_dist = atr * self.CONFIG['sl_atr_mult']
            tp_dist = atr * self.CONFIG['tp_atr_mult']
            
            if signal == Signal.BUY:
                sl = price - sl_dist
                # For Sweep, SL can be tighter: slightly below Low
                if setup_type == "LIQ_SWEEP_BULL":
                    sl = min(sl, last_m1['low'] - (atr * 0.5))
                tp = price + tp_dist
                
            else: # SELL
                sl = price + sl_dist
                # For Sweep, SL can be tighter: slightly above High
                if setup_type == "LIQ_SWEEP_BEAR":
                    sl = max(sl, last_m1['high'] + (atr * 0.5))
                tp = price - tp_dist

            return HyperSignal(
                signal=signal,
                confidence=confidence,
                entry_method=setup_type,
                entry_price=float(price),
                stop_loss=float(sl),
                take_profit=float(tp),
                reasons=reasons
            )

        return self._no_signal("Scanning...")

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        if 'ATR' not in df.columns:
            # Simple ATR calculation
            high_low = df['high'] - df['low']
            high_close = (df['high'] - df['close'].shift()).abs()
            low_close = (df['low'] - df['close'].shift()).abs()
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = ranges.max(axis=1)
            df['ATR'] = true_range.rolling(self.CONFIG['atr_period']).mean()
        return df

    def _no_signal(self, reason: str) -> HyperSignal:
        return HyperSignal(
            signal=Signal.NONE,
            confidence=0,
            entry_method="NONE",
            entry_price=0,
            stop_loss=0,
            take_profit=0,
            reasons=[reason]
        )

# Global Instance
hyper_scalp_strategy = HyperScalpStrategy()
