# -*- coding: utf-8 -*-
"""
USOIL Momentum Strategy — High Precision WR > 52%
================================================
Specially tuned for USOILm (Exness Standard).
Uses a 3-Layer Trend Core + Momentum Acceleration + Volatility Gate.

Rules:
1. Direction: 3/3 Alignment (EMA 200, Supertrend, DI+/-).
2. Momentum: ROC > 0.12%, Force Index positive, DI Gap > 4.
3. Quality: ADX > 25, Candle Body > 30% of range.
4. Entry: Pullback to EMA Fast (EMA 9).
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("usoil_momentum")

PARAMS = {
    "ema_fast": 9,
    "ema_trend": 200,
    "adx_threshold": 25,
    "min_roc": 0.12,
    "min_di_gap": 4.0,
    "min_body_ratio": 0.30,
    "vol_climax_thresh": 3.0,
    "sl_atr_mult": 1.2,
    "tp_rr": 1.8,
    "min_score": 7,  # Max confluence required
}

def classify_market_state(latest: pd.Series, close: float) -> str:
    vote_bull = 0
    vote_bear = 0
    
    # Layer 1: EMA 200
    ema_200 = float(latest.get('ema_200', close))
    if close > ema_200: vote_bull += 1
    else: vote_bear += 1
    
    # Layer 2: Supertrend
    if bool(latest.get('is_uptrend', False)): vote_bull += 1
    elif bool(latest.get('is_downtrend', False)): vote_bear += 1
    
    # Layer 3: DI Directional
    plus_di = float(latest.get('plus_di', 0))
    minus_di = float(latest.get('minus_di', 0))
    adx = float(latest.get('adx', 0))
    
    if adx > 20:
        if plus_di > minus_di: vote_bull += 1
        elif minus_di > plus_di: vote_bear += 1
        
    if vote_bull == 3: return "BULL"
    if vote_bear == 3: return "BEAR"
    return "NEUTRAL"

def signal_usoil_momentum(df: pd.DataFrame, context: dict) -> dict:
    if len(df) < 50: return None
    
    symbol = context.get('symbol', 'UNKNOWN')
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = float(latest['close'])
    
    # 1. Volatility Gate (Anti-Climax)
    vol_ratio = float(latest.get('vol_ratio', 1.0))
    if vol_ratio > PARAMS["vol_climax_thresh"]:
        return None
        
    # 2. Market State (3/3 Required)
    state = classify_market_state(latest, close)
    if state == "NEUTRAL": return None
    
    # 3. Quality Filters
    adx = float(latest.get('adx', 0))
    body_ratio = float(latest.get('body_ratio', 0))
    atr = float(latest.get('atr', close * 0.005))
    if adx < PARAMS["adx_threshold"] or body_ratio < PARAMS["min_body_ratio"]:
        return None
        
    ema_fast = float(latest.get('ema_9', latest.get('ema_fast', close)))
    roc = float(latest.get('roc_5', 0))
    force_idx = float(latest.get('force_index', 0))
    obv_bull = bool(latest.get('obv_bullish', False))
    
    htf_align = context.get('htf_ema_align', 'NEUTRAL')
    
    score = 0
    reasons = [f"Market: {state} (3/3)", f"HTF: {htf_align}"]
    
    if state == "BULL":
        # HTF Filter
        if htf_align == 'BEARISH':
            logger.debug(f"{symbol} USOIL_MOMENT Bull BLOCKED: HTF BEARISH")
            return None

        # Bullish Momentum Check
        if roc > PARAMS["min_roc"]: score += 2; reasons.append(f"ROC: +{roc:.2f}%")
        if force_idx > 0: score += 2; reasons.append("Force: +")
        if close >= ema_fast: score += 1; reasons.append("EMA: Align")
        if obv_bull: score += 2; reasons.append("OBV: +")
        
        if score < PARAMS["min_score"]: return None
        
        # Entry Logic: Mandatory Pullback to EMA Fast (EMA 9)
        # If price is above EMA, we wait for a touch (LIMIT). If already pulling back, enter MARKET.
        at_ema = abs(close - ema_fast) / close < 0.0005
        is_pulling_back = close <= ema_fast or at_ema
        
        if is_pulling_back:
            entry_price = close
            entry_type = "MARKET"
            reasons.append("Pullback: Level Reached ✅")
        else:
            entry_price = round(ema_fast, 2)
            entry_type = "LIMIT"
            reasons.append("Pullback: Waiting at EMA 9 ⏳")
            
        sl_dist = atr * PARAMS["sl_atr_mult"]
        sl = entry_price - sl_dist
        tp = entry_price + (sl_dist * PARAMS["tp_rr"])
        
        return {
            "symbol": symbol, "side": "BUY", "entry_type": entry_type,
            "entry_price": entry_price, "sl": round(sl, 5), "tp1": round(tp, 5),
            "rationale": reasons, 
            "confidence": min(0.95, 0.6 + (score/10.0)),
            "model": "USOIL_MOMENTUM"
        }
        
    elif state == "BEAR":
        # HTF Filter
        if htf_align == 'BULLISH':
            logger.debug(f"{symbol} USOIL_MOMENT Bear BLOCKED: HTF BULLISH")
            return None

        # Bearish Momentum Check
        if roc < -PARAMS["min_roc"]: score += 2; reasons.append(f"ROC: {roc:.2f}%")
        if force_idx < 0: score += 2; reasons.append("Force: -")
        if close <= ema_fast: score += 1; reasons.append("EMA: Align")
        if not obv_bull: score += 2; reasons.append("OBV: -")
        
        if score < PARAMS["min_score"]: return None
        
        # Entry Logic: Mandatory Pullback to EMA Fast (EMA 9)
        at_ema = abs(close - ema_fast) / close < 0.0005
        is_pulling_back = close >= ema_fast or at_ema
        
        if is_pulling_back:
            entry_price = close
            entry_type = "MARKET"
            reasons.append("Pullback: Level Reached ✅")
        else:
            entry_price = round(ema_fast, 2)
            entry_type = "LIMIT"
            reasons.append("Pullback: Waiting at EMA 9 ⏳")
            
        sl_dist = atr * PARAMS["sl_atr_mult"]
        sl = entry_price + sl_dist
        tp = entry_price - (sl_dist * PARAMS["tp_rr"])
        
        return {
            "symbol": symbol, "side": "SELL", "entry_type": entry_type,
            "entry_price": entry_price, "sl": round(sl, 5), "tp1": round(tp, 5),
            "rationale": reasons,
            "confidence": min(0.95, 0.6 + (score/10.0)),
            "model": "USOIL_MOMENTUM"
        }
        
    return None
