# -*- coding: utf-8 -*-
"""
SMC Metals Strategy for XAU/XAG (Hybrid Intelligence)
Features:
1. SMC Liquidity Sweep (Rolling High/Low Break and Reversal)
2. EMA 200 Trend Filter
3. RSI(14) Overbought/Oversold Filter
4. AI Confidence Score (0-10) -> converted to 0.0 - 1.0
5. ATR-based SL (1.5x ATR)
"""
import json
import logging
import numpy as np
import pandas as pd
logger = logging.getLogger("smc_metals")


def get_dynamic_atr_multiplier(current_atr: float, baseline_atr: float, base_multiplier: float = 1.5) -> float:
    """Dynamic ATR Multiplier: widen SL when volatile, tighten when quiet."""
    if current_atr <= 0 or baseline_atr <= 0:
        return base_multiplier
    volatility_ratio = current_atr / baseline_atr
    adaptive_ratio = max(0.8, min(1.3, volatility_ratio))
    return round(base_multiplier * adaptive_ratio, 2)

def signal_smc_metals(df: pd.DataFrame, context: dict) -> dict:
    """
    SMC strategy specifically tuned for precious metals.
    Volume confirms sweep authenticity.
    """
    if len(df) < 205:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    
    latest = df.iloc[-1]
    
    close = float(latest['close'])
    rsi = float(latest.get('rsi', 50))
    atr = float(latest.get('atr', 0))
    if atr <= 0 or np.isnan(atr):
        atr = close * 0.005
        
    baseline_atr = float(latest.get('atr_baseline', atr))
    sl_multiplier = get_dynamic_atr_multiplier(atr, baseline_atr, base_multiplier=1.5)

    ema_200 = float(latest.get('ema_200', close))
    
    # ─── Volume Features ─────────────────────────
    vol_ratio = float(latest.get('vol_ratio', 0.0))
    delta_bullish = bool(latest.get('delta_bullish', False))
    delta_bearish = bool(latest.get('delta_bearish', False))

    # ─── SMC Liquidity Sweep Detection ───
    lookback = 20
    recent_high = df['high'].iloc[-lookback-1:-1].max()
    recent_low = df['low'].iloc[-lookback-1:-1].min()
    
    # Sweep High (Bearish Reversal): Price goes above recent_high, then closes below it.
    sweep_high = latest['high'] > recent_high and close < recent_high
    # Sweep Low (Bullish Reversal): Price goes below recent_low, then closes above it.
    sweep_low = latest['low'] < recent_low and close > recent_low
    
    # ─── Trend Filter ───
    uptrend = close > ema_200
    downtrend = close < ema_200

    # ─── RSI Filter ───
    rsi_oversold = rsi < 35
    rsi_overbought = rsi > 65
    
    # ─── Scoring System (0-10) ───
    bull_score = 0
    bear_score = 0
    bull_reasons = []
    bear_reasons = []
    
    if sweep_low:
        bull_score += 4
        bull_reasons.append("SMC Bullish Sweep")
    if uptrend:
        bull_score += 3
        bull_reasons.append("Uptrend (EMA200)")
    if rsi_oversold:
        bull_score += 3
        bull_reasons.append(f"RSI Oversold ({rsi:.0f})")
    
    if sweep_high:
        bear_score += 4
        bear_reasons.append("SMC Bearish Sweep")
    if downtrend:
        bear_score += 3
        bear_reasons.append("Downtrend (EMA200)")
    if rsi_overbought:
        bear_score += 3
        bear_reasons.append(f"RSI Overbought ({rsi:.0f})")

    # ─── Volume Confirmation for Sweeps ───
    # Sweeps w/ elevated volume = institutional activity
    if vol_ratio >= 1.3:
        if sweep_low:
            bull_score += 2
            bull_reasons.append(f"Vol Sweep Confirmed ({vol_ratio:.1f}x)")
        if sweep_high:
            bear_score += 2
            bear_reasons.append(f"Vol Sweep Confirmed ({vol_ratio:.1f}x)")
    
    # Volume delta alignment
    if delta_bullish and sweep_low:
        bull_score += 1
        bull_reasons.append("Vol Delta Bullish")
    if delta_bearish and sweep_high:
        bear_score += 1
        bear_reasons.append("Vol Delta Bearish")

    # Need high confidence (Score >= 7)
    if bull_score >= 7:
        bull_reasons.append(f"Confidence {bull_score}/10")
        sl_dist = atr * sl_multiplier
        tp_dist = atr * 3.0
        return _build("BUY", close, sl_dist, tp_dist, symbol, bull_reasons, bull_score / 10.0)

    if bear_score >= 7:
        bear_reasons.append(f"Confidence {bear_score}/10")
        sl_dist = atr * sl_multiplier
        tp_dist = atr * 3.0
        return _build("SELL", close, sl_dist, tp_dist, symbol, bear_reasons, bear_score / 10.0)

    return None


def _build(side: str, price: float, sl_dist: float, tp_dist: float, symbol: str, reasons: list, confidence: float) -> dict:
    if side == "BUY":
        sl = price - sl_dist
        risk = price - sl
        tp1 = price + risk * 1.5 # Target scale-out at 1.5R
        tp2 = price + tp_dist
        tp3 = price + tp_dist * 1.5
    else:
        sl = price + sl_dist
        risk = sl - price
        tp1 = price - risk * 1.5
        tp2 = price - tp_dist
        tp3 = price - tp_dist * 1.5
        
    return {
        "symbol": symbol, "side": side, "entry_type": "MARKET",
        "entry_price": price, "sl": round(sl, 3),
        "tp1": round(tp1, 3), "tp2": round(tp2, 3), "tp3": round(tp3, 3),
        "rationale": reasons, "confidence": min(1.0, max(0.0, confidence)),
        "model": "SMC_METALS",
    }
