# -*- coding: utf-8 -*-
"""
Pattern Recognition AI Engine
Detects structural and institutional patterns:
1. Quasimodo (QML) - Over Under pattern
2. Wyckoff Accumulation/Distribution (Spring / Upthrust)
using Local Extrema mapping (scipy)
"""
import logging
import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

logger = logging.getLogger("pattern_ai")

def _find_extrema(series: pd.Series, order: int = 5):
    """Find local highs and lows indices."""
    local_max = argrelextrema(series.values, np.greater, order=order)[0]
    local_min = argrelextrema(series.values, np.less, order=order)[0]
    return local_max, local_min

def detect_quasimodo(df: pd.DataFrame, order: int = 5) -> dict:
    """
    Detects Quasimodo (Over/Under) reversal pattern.
    Bullish QML: Low (L), High (H), Lower Low (LL - sweep), Higher High (HH - BOS), Pullback to L (MPL).
    Bearish QML: High (H), Low (L), Higher High (HH - stop hunt), Lower Low (LL - MSB), pullback to H.
    """
    if len(df) < 50:
        return {"bullish": False, "bearish": False}
        
    # Analyze the last 50 bars
    window = df.iloc[-50:]
    highs, lows = _find_extrema(window['high'], order=order)
    max_idx, min_idx = _find_extrema(window['low'], order=order)
    
    # Needs at least 2 highs and 2 lows to form the structure
    if len(highs) < 2 or len(min_idx) < 2:
        return {"bullish": False, "bearish": False}
    
    # ─── Bearish QML ───
    # H -> L -> HH -> LL
    last_two_highs = window['high'].iloc[highs[-2:]].values
    last_two_lows = window['low'].iloc[min_idx[-2:]].values
    
    H1, HH = last_two_highs[0], last_two_highs[1]
    L1, LL = last_two_lows[0], last_two_lows[1]
    
    bearish_qml = False
    if HH > H1 and LL < L1:
        # Structure is H, L, HH, LL. Current price pulling back near H1?
        close = window['close'].iloc[-1]
        # In a bearish QML, retail stops were taken above H1 (HH), then structure broke below L1 (LL).
        # We short when price returns to the FVG/QML line near H1.
        if abs(close - H1) / H1 < 0.005:  # Within 0.5% of the QML level
            bearish_qml = True
            
    # ─── Bullish QML ───
    # L -> H -> LL -> HH
    bullish_qml = False
    if LL < L1 and HH > H1:
        # Structure is L, H, LL, HH. Current price pulling back near L1?
        close = window['close'].iloc[-1]
        if abs(close - L1) / L1 < 0.005:
            bullish_qml = True
            
    return {
        "bullish": bullish_qml,
        "bearish": bearish_qml
    }

def detect_wyckoff(df: pd.DataFrame) -> dict:
    """
    Simplified Wyckoff Spring (Accumulation) / Upthrust (Distribution) detection.
    Looks for a trading range (TR) and a false breakout (Spring/UTAD) that quickly reverses.
    """
    if len(df) < 60:
        return {"spring": False, "upthrust": False}
    
    # Look back 60 bars to define a range
    range_window = df.iloc[-60:-10]
    range_high = range_window['high'].max()
    range_low = range_window['low'].min()
    
    recent_window = df.iloc[-10:]
    
    # Spring (Bullish): Price drops below range_low, volume spikes, closes back inside range
    spring = False
    if recent_window['low'].min() < range_low:
        # Check if the candle that broke the low had high volume and rejected it
        breakdown_candle = recent_window[recent_window['low'] == recent_window['low'].min()].iloc[0]
        if breakdown_candle['close'] > range_low and breakdown_candle['tick_volume'] > range_window['tick_volume'].mean() * 1.5:
            spring = True

    # Upthrust (Bearish): Price pops above range_high, volume spikes, closes back inside range
    upthrust = False
    if recent_window['high'].max() > range_high:
        breakout_candle = recent_window[recent_window['high'] == recent_window['high'].max()].iloc[0]
        if breakout_candle['close'] < range_high and breakout_candle['tick_volume'] > range_window['tick_volume'].mean() * 1.5:
            upthrust = True
            
    return {
        "spring": spring,
        "upthrust": upthrust
    }

def analyze_patterns(df: pd.DataFrame) -> dict:
    """Run all pattern recognitions and return a state dictionary."""
    qml = detect_quasimodo(df)
    wyckoff = detect_wyckoff(df)
    
    return {
        "bullish_qml": qml["bullish"],
        "bearish_qml": qml["bearish"],
        "wyckoff_spring": wyckoff["spring"],
        "wyckoff_upthrust": wyckoff["upthrust"]
    }
