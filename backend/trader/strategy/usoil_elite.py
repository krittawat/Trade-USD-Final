# -*- coding: utf-8 -*-
"""
USOIL Elite Strategy V1 — Specially Tuned for Crude Oil (WTI).
Designed for the USOILm (Standard) symbol with ~18-30 pts spread.

PHILOSOPHY:
    Crude Oil is a high-momentum asset but highly sensitive to US Session hours.
    This strategy combines:
    1. Trend Filter (EMA 200)
    2. Momentum Alignment (EMA 9/21)
    3. Mean Reversion Safety (RSI extremes)
    4. Force Index (Volume + Price Pressure)
"""

import logging
import numpy as np
import pandas as pd
from app.analysis.structure import detect_structure

logger = logging.getLogger("usoil_elite")

# USOILm Specific Parameters (M5 Timeframe optimized)
CONFIGS = {
    "ema_fast": 9,
    "ema_mid": 21,
    "ema_slow": 50,
    "ema_trend": 200,

    "rsi_period": 14,
    "rsi_buy_min": 40,  # Avoid overbought but catch momentum
    "rsi_buy_max": 72,
    "rsi_sell_min": 28,
    "rsi_sell_max": 60,

    "force_period": 13,
    "min_vol_ratio": 1.1,  # Must have volume above average
    
    "lookback_bars": 120,
    "atr_period": 14,
    
    # Risk Management (USOIL points: 0.10 move = 100 points)
    "min_rr": 1.6,
    "max_sl_pts": 600,  # Max $0.60 risk for Oil scalping
    "sl_atr_mult": 1.8,
    "tp_atr_mult": 3.2,
    
    # Session Hours (UTC) - USOIL is most volatile in NY
    "trade_sessions": ["LONDON", "NY", "OVERLAP"],
}

def _calculate_pullback_penalty(df: pd.DataFrame, side: str, entry_price: float) -> tuple[float, str]:
    """
    USOIL V2.1: Pullback Gate.
    """
    if len(df) < 15: return 1.0, ""
    recent = df.tail(15)
    
    if side == "BUY":
        origin = float(recent['low'].min())
        peak = float(recent['high'].max())
        move = peak - origin
        if move <= 0: return 1.0, ""
        retrace = (peak - entry_price) / move
        if retrace < 0.20: # Slightly stricter for Oil (20%)
            return 0.75, f"Pullback Gate: USOIL BUY at peak ({retrace*100:.1f}%)"
    else:
        origin = float(recent['high'].max())
        peak = float(recent['low'].min())
        move = origin - peak
        if move <= 0: return 1.0, ""
        retrace = (entry_price - peak) / move
        if retrace < 0.20:
            return 0.75, f"Pullback Gate: USOIL SELL at peak ({retrace*100:.1f}%)"
            
    return 1.0, ""

def signal_usoil_elite(df: pd.DataFrame, context: dict) -> dict:
    """
    Crude Oil Elite V2 — SMC + Momentum Integration.
    """
    if len(df) < CONFIGS["ema_trend"] + 20:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    latest = df.iloc[-1]
    
    # ─── 1. Session Filter ───
    session = latest.get('session', 'UNKNOWN')
    if session not in CONFIGS["trade_sessions"]:
        return None

    # ─── 2. Advanced Structure Analysis (SMC) ───
    struct = detect_structure(df)
    if not struct: return None
    
    # ─── 3. Indicator Extraction ───
    close = float(latest['close'])
    ema_9 = float(latest.get('ema_9', latest.get('ema_fast', close)))
    ema_21 = float(latest.get('ema_21', latest.get('ema_mid', close)))
    ema_200 = float(latest.get('ema_200', close))
    
    rsi = float(latest.get('rsi', 50))
    atr = float(latest.get('atr', 0.05))
    vol_ratio = float(latest.get('vol_ratio', 1.0))
    force_idx = float(latest.get('force_index', 0))
    force_ema = float(latest.get('force_ema', 0))
    
    if np.isnan(atr) or atr <= 0: return None

    # ─── 4. Multi-Layer Scoring (0-100) ───
    b_score, s_score = 0, 0
    b_reasons, s_reasons = [], []

    # Layer 1: Trend & Structure (40 pts)
    if struct.get('trend') == "BULLISH":
        b_score += 25
        b_reasons.append(f"SMC: Trend Bullish (BOS/CHoCH)")
    elif struct.get('trend') == "BEARISH":
        s_score += 25
        s_reasons.append(f"SMC: Trend Bearish (BOS/CHoCH)")

    if close > ema_200:
        b_score += 15
        if ema_9 > ema_21: b_score += 10
    else:
        s_score += 15
        if ema_9 < ema_21: s_score += 10

    # Layer 2: Momentum (30 pts)
    if CONFIGS["rsi_buy_min"] < rsi < CONFIGS["rsi_buy_max"]:
        b_score += 15
        if rsi > 50: b_score += 15
        b_reasons.append(f"Mom: RSI {rsi:.1f} Bullish")
        
    if CONFIGS["rsi_sell_min"] < rsi < CONFIGS["rsi_sell_max"]:
        s_score += 15
        if rsi < 50: s_score += 15
        s_reasons.append(f"Mom: RSI {rsi:.1f} Bearish")

    # Layer 3: Price Action Triggers (30 pts)
    patterns = struct.get('patterns', {})
    if patterns.get('engulf_bull') or patterns.get('pin_bull'):
        b_score += 30
        b_reasons.append("PA: Bullish Rejection/Momentum")
    if patterns.get('engulf_bear') or patterns.get('pin_bear'):
        s_score += 30
        s_reasons.append("PA: Bearish Rejection/Momentum")

    # ─── 5. Institutional Hard Blocks ───
    # 1. Trend Filter: Don't fight the major structure
    if struct.get('trend') == "BEARISH" and b_score > 0:
        logger.info(f"🚫 [USOIL_ELITE] BUY blocked by Bearish Structure")
        b_score = 0
    if struct.get('trend') == "BULLISH" and s_score > 0:
        logger.info(f"🚫 [USOIL_ELITE] SELL blocked by Bullish Structure")
        s_score = 0

    # 1.1 Major Trend Filter (EMA 200 HTF)
    htf_align = context.get('htf_ema_align', 'UNCERTAIN')
    if htf_align == 'BEARISH' and b_score > 0:
        logger.info(f"🚫 [USOIL_ELITE] BUY blocked by HTF BEARISH trend (EMA 200)")
        b_score = 0
    if htf_align == 'BULLISH' and s_score > 0:
        logger.info(f"🚫 [USOIL_ELITE] SELL blocked by HTF BULLISH trend (EMA 200)")
        s_score = 0

    # 2. FVG Filter: Don't buy right into resistance FVG
    if b_score > 0 and struct.get('fvg_bear'):
        # If nearest bearish FVG bottom is within 1.5 ATR, block buy
        nearest_fvg = struct['fvg_bear'][0]
        if nearest_fvg['bottom'] > close and (nearest_fvg['bottom'] - close) < (atr * 1.5):
            logger.info(f"🚫 [USOIL_ELITE] BUY blocked by FVG Resistance at {nearest_fvg['bottom']}")
            b_score = 0

    # Final Decision
    side = None
    score = 0
    reasons = []
    
    if b_score >= 70:
        side = "BUY"
        score = b_score
        reasons = b_reasons
    elif s_score >= 70:
        side = "SELL"
        score = s_score
        reasons = s_reasons

    if not side:
        return None

    # ─── 5.1 Pullback Gate (FOMO Penalty) ───
    pb_mult, pb_reason = _calculate_pullback_penalty(df, side, close)
    if pb_mult < 1.0:
        reasons.append(pb_reason)
        score *= pb_mult

    # ─── 6. Entry & Risk ───
    # For Oil Elite, we aim for slightly tighter SL using ATR
    sl_dist = atr * CONFIGS["sl_atr_mult"]
    
    # Cap SL in points
    pt_val = context.get('point', 0.001)
    sl_points = sl_dist / pt_val if pt_val else sl_dist * 1000
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
        "entry_type": "MARKET" if pb_mult >= 1.0 else "LIMIT",
        "entry_price": close,
        "sl": round(sl, 5),
        "tp1": round(tp, 5),
        "rationale": reasons,
        "confidence": min(0.95, score / 100.0),
        "fomo_mult": pb_mult,
        "model": "USOIL_ELITE",
    }
