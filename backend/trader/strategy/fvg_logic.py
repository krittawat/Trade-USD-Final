# -*- coding: utf-8 -*-
"""
Fair Value Gap (FVG) Strategy (Institutional Flow)
Features:
1. Three-Candle Pattern detection (gap between candle 1 and candle 3)
2. Gap Size Filter (gap must be > 1.0x ATR to denote true displacement)
3. Consequent Encroachment (Entry limit/market at 50% fill)
4. Context & Confluence (RSI, Trend, or recent Liquidity Sweep)
"""
import logging
import numpy as np
import pandas as pd
logger = logging.getLogger("fvg_logic")


def get_dynamic_atr_multiplier(current_atr: float, baseline_atr: float, base_multiplier: float = 1.5) -> float:
    """Dynamic ATR Multiplier: widen SL when volatile, tighten when quiet."""
    if current_atr <= 0 or baseline_atr <= 0:
        return base_multiplier
    volatility_ratio = current_atr / baseline_atr
    adaptive_ratio = max(0.8, min(1.3, volatility_ratio))
    return round(base_multiplier * adaptive_ratio, 2)

def signal_fvg_logic(df: pd.DataFrame, context: dict) -> dict:
    """
    FVG / SMC Institutional flow strategy with Volume confirmation.
    """
    if len(df) < 205:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    latest = df.iloc[-1]
    
    close = float(latest['close'])
    atr = float(latest.get('atr', 0))
    if atr <= 0 or np.isnan(atr):
        atr = close * 0.005
        
    baseline_atr = float(latest.get('atr_baseline', atr))
    sl_multiplier = get_dynamic_atr_multiplier(atr, baseline_atr, base_multiplier=1.5)

    ema_200 = float(latest.get('ema_200', close))
    
    # ─── 1. FVG Detection (3 Candle Pattern) ───
    c1 = df.iloc[-4]
    c2 = df.iloc[-3]  # Displacement candle (the big move)
    c3 = df.iloc[-2]
    
    # Bullish FVG: C3 Low > C1 High (gap left behind by massive green C2)
    bull_fvg_gap = c3['low'] - c1['high']
    is_bull_fvg = bull_fvg_gap > 0
    bull_fvg_valid = is_bull_fvg and (bull_fvg_gap > atr * 0.8) # Strong displacement
    
    # Bearish FVG: C1 Low > C3 High (gap left behind by massive red C2)
    bear_fvg_gap = c1['low'] - c3['high']
    is_bear_fvg = bear_fvg_gap > 0
    bear_fvg_valid = is_bear_fvg and (bear_fvg_gap > atr * 0.8)

    # Calculate 50% fill (Consequent Encroachment)
    bull_ce_level = c1['high'] + (bull_fvg_gap / 2.0) if is_bull_fvg else 0
    bear_ce_level = c3['high'] + (bear_fvg_gap / 2.0) if is_bear_fvg else 0
    
    # ─── 2. Entry Logic (Retest/Fill) ───
    bull_retesting = bull_fvg_valid and (latest['low'] <= bull_ce_level) and (close >= c1['high'])
    bear_retesting = bear_fvg_valid and (latest['high'] >= bear_ce_level) and (close <= c1['low'])

    # ─── 3. Confluence (Trend / Premium/Discount) ───
    uptrend = close > ema_200
    downtrend = close < ema_200

    # ─── 4. Volume Analysis on Displacement Candle (C2) ───
    vol_col = 'tick_volume' if 'tick_volume' in df.columns else 'volume'
    c2_volume = float(c2.get(vol_col, 0)) if vol_col in df.columns else 0
    vol_avg = float(latest.get('vol_avg', 1))
    c2_vol_ratio = c2_volume / vol_avg if vol_avg > 0 else 0
    
    # C2 delta direction (was the displacement candle buying or selling?)
    c2_range = c2['high'] - c2['low']
    c2_delta_bullish = (c2['close'] - c2['low']) > (c2['high'] - c2['close']) if c2_range > 0 else False
    c2_delta_bearish = (c2['high'] - c2['close']) > (c2['close'] - c2['low']) if c2_range > 0 else False

    # ─── 5. Scoring System (0-10) ───
    bull_score = 0
    bear_score = 0
    bull_reasons = []
    bear_reasons = []
    
    if bull_retesting:
        bull_score += 5
        bull_reasons.append(f"Bullish FVG Retest")
    if uptrend:
        bull_score += 3
        bull_reasons.append("Uptrend (EMA200)")
    
    if bear_retesting:
        bear_score += 5
        bear_reasons.append(f"Bearish FVG Retest")
    if downtrend:
        bear_score += 3
        bear_reasons.append("Downtrend (EMA200)")

    # Volume confirmation on displacement candle
    if c2_vol_ratio >= 1.5:
        if bull_retesting:
            bull_score += 2
            bull_reasons.append(f"C2 Vol Displacement ({c2_vol_ratio:.1f}x)")
        if bear_retesting:
            bear_score += 2
            bear_reasons.append(f"C2 Vol Displacement ({c2_vol_ratio:.1f}x)")
    
    # Delta alignment on displacement
    if c2_delta_bullish and bull_retesting:
        bull_score += 1
        bull_reasons.append("C2 Delta Bullish")
    if c2_delta_bearish and bear_retesting:
        bear_score += 1
        bear_reasons.append("C2 Delta Bearish")

    # Need high confidence (Score >= 8) -> FVG test + Trend + Volume
    if bull_score >= 8:
        bull_reasons.append(f"Confidence {bull_score}/10")
        bull_reasons.append(f"Gap: {bull_fvg_gap:.2f}")
        sl_dist = atr * sl_multiplier
        tp_dist = atr * 3.0  
        return _build("BUY", close, sl_dist, tp_dist, symbol, bull_reasons, bull_score / 10.0)

    if bear_score >= 8:
        bear_reasons.append(f"Confidence {bear_score}/10")
        bear_reasons.append(f"Gap: {bear_fvg_gap:.2f}")
        sl_dist = atr * sl_multiplier
        tp_dist = atr * 3.0
        return _build("SELL", close, sl_dist, tp_dist, symbol, bear_reasons, bear_score / 10.0)

    return None

def _build(side: str, price: float, sl_dist: float, tp_dist: float, symbol: str, reasons: list, confidence: float) -> dict:
    if side == "BUY":
        sl = price - sl_dist
        risk = price - sl
        tp1 = price + risk * 1.5
        tp2 = price + tp_dist
        tp3 = price + tp_dist * 2.0
    else:
        sl = price + sl_dist
        risk = sl - price
        tp1 = price - risk * 1.5
        tp2 = price - tp_dist
        tp3 = price - tp_dist * 2.0
        
    return {
        "symbol": symbol, "side": side, "entry_type": "MARKET",
        "entry_price": price, "sl": round(sl, 3),
        "tp1": round(tp1, 3), "tp2": round(tp2, 3), "tp3": round(tp3, 3),
        "rationale": reasons, "confidence": min(1.0, max(0.0, confidence)),
        "model": "FVG_FLOW",
    }
    
    # --- Precision Entry Optimization ---
    from backend.trader.features.entry_optimizer import calculate_optimal_entry
    signal = calculate_optimal_entry(signal, df)
    
    return signal
