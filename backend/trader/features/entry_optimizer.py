# -*- coding: utf-8 -*-
"""
Entry Optimizer — Precision Pricing for Institutional Strategies.
Calculates OTE (Optimal Trade Entry) and FVG midpoints.
"""
import pandas as pd
import numpy as np

def calculate_optimal_entry(signal: dict, df: pd.DataFrame) -> dict:
    """
    Refines the entry_price and entry_type based on institutional levels.
    """
    if len(df) < 5:
        return signal

    symbol = signal.get('symbol', '')
    side = signal.get('side', '')
    current_price = signal.get('entry_price', 0)
    
    # 1. Calculate FVG Midpoint (Consequent Encroachment)
    # We look for the displacement candle (usually idx-1 or idx-2)
    # in Alpha V5, the FVG is usually between idx-2 and idx. 
    # Midpoint of gap = (High[idx-2] + Low[idx]) / 2 for Bullish
    
    c1_high = df.iloc[-3]['high'] # Bar 0 in FVG
    c3_low = df.iloc[-1]['low']   # Bar 2 in FVG
    
    c1_low = df.iloc[-3]['low']
    c3_high = df.iloc[-1]['high']
    
    optimal_price = current_price
    entry_type = "MARKET"
    
    if side == "BUY":
        fvg_mid = (c1_high + c3_low) / 2.0
        # OTE range (61.8% - 70.5% of the sweep move)
        # For simplicity, we use FVG midpoint as a primary institutional magnet
        if current_price > fvg_mid * 1.0005: 
            # Price has run away > 5 pips on BTC equivalent
            optimal_price = round(fvg_mid, 2)
            entry_type = "LIMIT"
    else:
        fvg_mid = (c1_low + c3_high) / 2.0
        if current_price < fvg_mid * 0.9995:
            optimal_price = round(fvg_mid, 2)
            entry_type = "LIMIT"
            
    signal['entry_price'] = optimal_price
    signal['entry_type'] = entry_type
    
    # Recalculate RR if entry changed
    sl = signal.get('sl', 0)
    if side == "BUY":
        risk = optimal_price - sl
        if risk > 0:
            signal['tp1'] = round(optimal_price + risk * 1.5, 2)
            signal['tp2'] = round(optimal_price + risk * 2.5, 2)
            signal['tp3'] = round(optimal_price + risk * 4.0, 2)
    else:
        risk = sl - optimal_price
        if risk > 0:
            signal['tp1'] = round(optimal_price - risk * 1.5, 2)
            signal['tp2'] = round(optimal_price - risk * 2.5, 2)
            signal['tp3'] = round(optimal_price - risk * 4.0, 2)
            
    return signal
