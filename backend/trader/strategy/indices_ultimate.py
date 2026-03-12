# -*- coding: utf-8 -*-
"""
INDICES ULTIMATE V1 — Optimized for US30m & USTECm (Nasdaq).
══════════════════════════════════════════════════════════
Focus: Session Breakouts & VWAP Mean Reversion.
"""
import logging
import numpy as np
import pandas as pd
from app.analysis.structure import detect_structure

logger = logging.getLogger("indices_ultimate")

CONFIGS = {
    "ema_trend": 200,
    "rsi_period": 14,
    "atr_period": 14,
    "vwap_window": 100,
    "min_rr": 1.5,
    "max_sl_pts": 3000, # Indices have high point values
    "sl_atr_mult": 2.0,
    "ny_open_hour": 14, # 14:30 UTC is US Open
}

def signal_indices_ultimate(df: pd.DataFrame, context: dict) -> dict:
    """
    US Indices Specific Strategy.
    """
    symbol = context.get('symbol', 'UNKNOWN')
    if "30" not in symbol and "TEC" not in symbol and "NAS" not in symbol:
        return None

    if len(df) < CONFIGS["ema_trend"] + 20:
        return None

    latest = df.iloc[-1]
    close = float(latest['close'])
    
    # ─── 1. Advanced Structure (SMC) ───
    struct = detect_structure(df)
    if not struct: return None

    # ─── 2. VWAP & Trend ───
    # Simplified VWAP for M5 (Rolling 100 bars)
    df['vwap'] = (df['close'] * df.get('tick_volume', 1)).rolling(CONFIGS["vwap_window"]).sum() / \
                  df.get('tick_volume', 1).rolling(CONFIGS["vwap_window"]).sum()
    vwap = df['vwap'].iloc[-1]
    ema_200 = float(latest.get('ema_200', close))
    
    # ─── 3. Scoring ───
    b_score, s_score = 0, 0
    b_reasons, s_reasons = [], []
    
    # Trend Layer
    if close > ema_200 and struct.get('trend') == "BULLISH":
        b_score += 40
        b_reasons.append("Trend: Bullish SMC + Above EMA-200")
    elif close < ema_200 and struct.get('trend') == "BEARISH":
        s_score += 40
        s_reasons.append("Trend: Bearish SMC + Below EMA-200")
        
    # VWAP Layer (Mean Reversion / Value Gate)
    if close > vwap:
        s_score += 20; s_reasons.append("VWAP: Above Value (Premium)")
    else:
        b_score += 20; b_reasons.append("VWAP: Below Value (Discount)")

    # Price Action Layer
    patterns = struct.get('patterns', {})
    if patterns.get('engulf_bull') or patterns.get('morning_star'):
        b_score += 40; b_reasons.append("PA: Bullish Momentum Trigger")
    if patterns.get('engulf_bear') or patterns.get('evening_star'):
        s_score += 40; s_reasons.append("PA: Bearish Momentum Trigger")

    # Volume Expansion Layer
    vol_ratio = float(latest.get('vol_ratio', 1.0))
    if vol_ratio >= 1.2:
        b_score += 15; b_reasons.append(f"Vol: Expansion ({vol_ratio:.1f}x)")
        s_score += 15; s_reasons.append(f"Vol: Expansion ({vol_ratio:.1f}x)")

    # ─── 4. Decision ───
    side = None
    score = 0
    reasons = []
    
    # Indices move fast — we need high conviction
    if b_score >= 75:
        side = "BUY"
        score = b_score
        reasons = b_reasons
    elif s_score >= 75:
        side = "SELL"
        score = s_score
        reasons = s_reasons

    if not side:
        return None

    # ─── 5. Risk Management ───
    atr = float(latest.get('atr', 10.0))
    sl_dist = atr * CONFIGS["sl_atr_mult"]
    
    # Point checks for Indices
    pt_val = context.get('point', 0.01)
    sl_points = sl_dist / pt_val if pt_val else sl_dist * 100
    if sl_points > CONFIGS["max_sl_pts"]:
        sl_dist = CONFIGS["max_sl_pts"] * pt_val

    if side == "BUY":
        sl = close - sl_dist
        tp = close + (sl_dist * CONFIGS["min_rr"])
    else:
        sl = close + sl_dist
        tp = close - (sl_dist * CONFIGS["min_rr"])

    return {
        "symbol": symbol,
        "side": side,
        "entry_type": "MARKET",
        "entry_price": close,
        "sl": round(sl, 2),
        "tp1": round(tp, 2),
        "rationale": reasons,
        "confidence": min(0.95, score / 100.0),
        "model": "INDICES_ULTIMATE",
    }
