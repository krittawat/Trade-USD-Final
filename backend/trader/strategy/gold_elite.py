# -*- coding: utf-8 -*-
"""
Gold Elite Strategy V2 — 5-Layer 100-Point Scoring for Maximum Precision.
Adapted from the best backup strategy for the V2 OPUS Pipeline.
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("gold_elite")

# Default Parameters for Gold Elite (M5/M15 timeframe)
CONFIGS = {
    "ema_fast": 9,
    "ema_mid": 21,
    "ema_slow": 50,
    "ema_trend": 200,

    "adx_period": 14,
    "adx_min": 20,

    "rsi_period": 14,
    
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,

    "swing_lookback": 15,
    "max_sl_dist": 4.0, # Max $4.00 (400 points) SL for $100 account micro-scalping

    "atr_period": 14,
    "sl_atr_mult": 1.5,
    "sl_buffer_atr": 0.3,

    "min_score_trade": 70,
    "rr_standard": 1.5,
    "rr_strong": 1.8,
    "rr_elite": 2.0,
}

def _compute_adx(high, low, close, period):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    up_move = high - high.shift()
    down_move = low.shift() - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_s = pd.Series(plus_dm, index=high.index).rolling(period).mean()
    minus_dm_s = pd.Series(minus_dm, index=high.index).rolling(period).mean()
    atr_s = tr.rolling(period).mean()

    di_plus_s = 100 * plus_dm_s / (atr_s + 1e-10)
    di_minus_s = 100 * minus_dm_s / (atr_s + 1e-10)
    dx = 100 * (di_plus_s - di_minus_s).abs() / (di_plus_s + di_minus_s + 1e-10)
    adx_s = dx.rolling(period).mean()

    return float(adx_s.iloc[-1]), float(di_plus_s.iloc[-1]), float(di_minus_s.iloc[-1])

def _compute_rsi(close, period):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])

def _compute_macd(close, fast, slow, signal):
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return float(hist.iloc[-1])


def signal_gold_elite(df: pd.DataFrame, context: dict) -> dict:
    if len(df) < CONFIGS["ema_trend"] + 20:
        return None  # Insufficient bars for EMA-200

    # Dynamic Parameters from AI Brain
    strat_params = context.get('brain_params', {}).get('gold_elite', {})
    p = CONFIGS.copy()
    if strat_params:
        p.update(strat_params)
        logger.debug(f"GOLD_ELITE: Using AI Evolved Params: {strat_params}")

    symbol = context.get('symbol', 'UNKNOWN')
    
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    curr_close = float(close.iloc[-1])
    
    # ─── Compute Indicators (Using 'p' instead of CONFIGS) ───
    ema_f = close.ewm(span=p["ema_fast"], adjust=False).mean().iloc[-1]
    ema_m = close.ewm(span=p["ema_mid"], adjust=False).mean().iloc[-1]
    ema_s = close.ewm(span=p["ema_slow"], adjust=False).mean().iloc[-1]
    ema_t = close.ewm(span=p["ema_trend"], adjust=False).mean().iloc[-1]

    # ATR
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(p["atr_period"]).mean().iloc[-1])
    
    # Block if ATR is dead
    if np.isnan(atr) or atr <= 0: return None

    adx, di_plus, di_minus = _compute_adx(high, low, close, p["adx_period"])
    rsi = _compute_rsi(close, p["rsi_period"])
    macd_hist = _compute_macd(close, p["macd_fast"], p["macd_slow"], p["macd_signal"])

    # ─── Fallback block on extremes ───
    if rsi > 80 or rsi < 20: 
        return None

    # Trend vs Sideways
    if adx < p["adx_min"]:
        return None 

    # ─── 5-Layer Scoring ───
    b_score, s_score = 0, 0
    b_reasons, s_reasons = [], []

    # Layer 1: Trend Alignment
    if ema_f > ema_m > ema_s > ema_t:
        b_score += 15
        b_reasons.append("L1: Full Bull Stack")
    elif ema_f > ema_m and curr_close > ema_t:
        b_score += 8
    
    if ema_f < ema_m < ema_s < ema_t:
        s_score += 15
        s_reasons.append("L1: Full Bear Stack")
    elif ema_f < ema_m and curr_close < ema_t:
        s_score += 8

    # Layer 2: Momentum
    if adx > p["adx_min"]:
        if di_plus > di_minus:
            b_score += 10
            b_reasons.append(f"L2: ADX={adx:.1f} DI+")
        else:
            s_score += 10
            s_reasons.append(f"L2: ADX={adx:.1f} DI-")

    if macd_hist > 0: b_score += 5
    else: s_score += 5

    # Layer 3: RSI Zones
    if 50 < rsi < 75: b_score += 10; b_reasons.append(f"L3: RSI={rsi:.1f}")
    if 25 < rsi < 50: s_score += 10; s_reasons.append(f"L3: RSI={rsi:.1f}")

    # Layer 4: Close proximity logic vs EMAs
    if curr_close > ema_f: b_score += 5
    if curr_close < ema_f: s_score += 5

    # Determine Winner
    if b_score >= s_score and b_score >= p["min_score_trade"]:
        side = "BUY"
        score = b_score
        reasons = b_reasons
    elif s_score > b_score and s_score >= p["min_score_trade"]:
        side = "SELL"
        score = s_score
        reasons = s_reasons
    else:
        return None 

    # Risk sizing based on score tier
    if score >= 85: rr = p["rr_elite"]
    elif score >= 75: rr = p["rr_strong"]
    else: rr = p["rr_standard"]

    # Swing high / low for SL
    swing_window = df.iloc[-p["swing_lookback"]-1:-1]
    swing_high = float(swing_window["high"].max())
    swing_low = float(swing_window["low"].min())

    sl_buffer = atr * p["sl_buffer_atr"]
    sl_atr_fallback = atr * p["sl_atr_mult"]

    if side == "BUY":
        sl_base = swing_low
        sl_price = sl_base - sl_buffer
        sl_atr = curr_close - sl_atr_fallback
        sl = max(sl_price, sl_atr) 
        sl_dist = curr_close - sl
        if sl_dist < atr * 0.5: sl_dist = atr 
        if sl_dist > p["max_sl_dist"]: return None 
        tp = curr_close + (sl_dist * rr)
    else:
        sl_base = swing_high
        sl_price = sl_base + sl_buffer
        sl_atr = curr_close + sl_atr_fallback
        sl = min(sl_price, sl_atr) 
        sl_dist = sl - curr_close
        if sl_dist < atr * 0.5: sl_dist = atr
        if sl_dist > p["max_sl_dist"]: return None 
        tp = curr_close - (sl_dist * rr)


    return {
        "symbol": symbol, 
        "side": side, 
        "entry_type": "MARKET",
        "entry_price": curr_close, 
        "sl": round(sl, 5),
        "tp1": round(tp, 5), 
        "rationale": [f"Gold Elite Score: {score}/100 (RR={rr})"] + reasons,
        "confidence": 0.85 + (score - 60) * 0.002,
        "model": "GOLD_ELITE",
    }
