# -*- coding: utf-8 -*-
"""
Machine Learning Probability Scoring Engine V2 (The Antigravity Engine)
======================================================================
Evaluates trade signals based on ACTUAL INDICATOR DATA from the DataFrame,
not string keyword matching. Strategy-agnostic scoring.

Outputs a 0-100 Probability Score based on 7 weighted market factors.

Changes from V1:
- Removed string-based rationale matching (root cause of 93% signal kill)
- Scores based on real indicator values (RSI, ADX, EMA, Volume, MACD, etc.)
- Blends with raw strategy confidence instead of replacing it
"""
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("ml_scoring")

# Feature importance weights (sum = 100 for perfect setup)
FEATURE_WEIGHTS = {
    "trend_alignment": 15.0,   # EMA/Supertrend agreement with signal direction
    "momentum": 15.0,          # ROC + MACD histogram + Force Index direction
    "rsi_position": 10.0,      # RSI favorable zone for entry
    "volume_confirm": 15.0,    # Volume ratio + delta direction
    "adx_strength": 10.0,      # ADX trending confirmation
    "stochastic": 10.0,        # Stochastic direction alignment
    "body_dominance": 10.0,    # Candle body conviction
    "macro_overlay": 15.0,     # Institutional context boost (increased to 15%)
}


def calculate_trade_probability(df: pd.DataFrame, signal_context: dict, pattern_state: dict) -> float:
    """
    Calculates a probability score [0.0 - 100.0] for a given trade setup.
    Uses ACTUAL INDICATOR VALUES from the DataFrame — strategy agnostic.
    """
    if len(df) < 50:
        return 0.0

    score = 0.0
    latest = df.iloc[-1]

    side = signal_context.get("side", "BUY")
    is_buy = side == "BUY"
    close = float(latest['close'])

    # ─── Extract indicator values ────────────────────────
    ema_fast = float(latest.get('ema_fast', close))
    ema_slow = float(latest.get('ema_slow', close))
    ema_200 = float(latest.get('ema_200', close))
    rsi = float(latest.get('rsi', 50))
    adx_val = float(latest.get('adx', 0))
    stoch_k = float(latest.get('stoch_k', 50))
    stoch_d = float(latest.get('stoch_d', 50))
    macd_hist = float(latest.get('macd_hist', 0))
    vol_ratio = float(latest.get('vol_ratio', 1.0))
    delta_bullish = bool(latest.get('delta_bullish', False))
    delta_bearish = bool(latest.get('delta_bearish', False))
    is_uptrend = bool(latest.get('is_uptrend', False))
    is_downtrend = bool(latest.get('is_downtrend', False))
    body_ratio = float(latest.get('body_ratio', 0.5))
    roc = float(latest.get('roc_5', 0))
    force_idx = float(latest.get('force_index', 0))

    # Handle NaN values
    if np.isnan(rsi): rsi = 50
    if np.isnan(adx_val): adx_val = 0
    if np.isnan(stoch_k): stoch_k = 50
    if np.isnan(stoch_d): stoch_d = 50
    if np.isnan(macd_hist): macd_hist = 0
    if np.isnan(vol_ratio): vol_ratio = 1.0
    if np.isnan(body_ratio): body_ratio = 0.5
    if np.isnan(roc): roc = 0
    if np.isnan(force_idx): force_idx = 0

    W = FEATURE_WEIGHTS

    # ─── Factor 1: TREND ALIGNMENT (15 pts) ──────────────
    trend_score = 0
    if is_buy:
        if is_uptrend:
            trend_score += 0.4  # Supertrend confirms
        if ema_fast > ema_slow:
            trend_score += 0.3  # Short-term trend up
        if close > ema_200:
            trend_score += 0.3  # Above HTF trend
    else:
        if is_downtrend:
            trend_score += 0.4
        if ema_fast < ema_slow:
            trend_score += 0.3
        if close < ema_200:
            trend_score += 0.3
    score += trend_score * W["trend_alignment"]

    # ─── Factor 2: MOMENTUM (20 pts) ─────────────────────
    mom_score = 0
    if is_buy:
        if roc > 0:
            mom_score += 0.35
        if macd_hist > 0:
            mom_score += 0.35
        if force_idx > 0:
            mom_score += 0.30
    else:
        if roc < 0:
            mom_score += 0.35
        if macd_hist < 0:
            mom_score += 0.35
        if force_idx < 0:
            mom_score += 0.30
    score += mom_score * W["momentum"]

    # ─── Factor 3: RSI POSITION (15 pts) ─────────────────
    rsi_score = 0
    if is_buy:
        if rsi < 30:
            rsi_score = 1.0       # Oversold — great entry
        elif rsi < 40:
            rsi_score = 0.8
        elif rsi < 55:
            rsi_score = 0.6       # Neutral-bullish zone
        elif rsi < 70:
            rsi_score = 0.4       # Trending but not extreme
        else:
            rsi_score = 0.1       # Overbought — bad for new buy
    else:
        if rsi > 70:
            rsi_score = 1.0
        elif rsi > 60:
            rsi_score = 0.8
        elif rsi > 45:
            rsi_score = 0.6
        elif rsi > 30:
            rsi_score = 0.4
        else:
            rsi_score = 0.1
    score += rsi_score * W["rsi_position"]

    # ─── Factor 4: VOLUME CONFIRMATION (15 pts) ──────────
    vol_score = 0
    if vol_ratio >= 2.0:
        vol_score += 0.5         # Strong volume surge
    elif vol_ratio >= 1.2:
        vol_score += 0.4
    elif vol_ratio >= 0.8:
        vol_score += 0.25
    else:
        vol_score += 0.1         # Low volume — less conviction

    # Delta direction confirmation
    if is_buy and delta_bullish:
        vol_score += 0.5
    elif not is_buy and delta_bearish:
        vol_score += 0.5
    elif is_buy and not delta_bearish:
        vol_score += 0.2         # At least not contra
    elif not is_buy and not delta_bullish:
        vol_score += 0.2
    score += min(1.0, vol_score) * W["volume_confirm"]

    # ─── Factor 5: ADX STRENGTH (10 pts) ─────────────────
    adx_score = 0
    if adx_val > 35:
        adx_score = 1.0          # Strong trend
    elif adx_val > 25:
        adx_score = 0.8
    elif adx_val > 20:
        adx_score = 0.6
    elif adx_val > 15:
        adx_score = 0.4          # Weak but present
    else:
        adx_score = 0.15         # No trend
    score += adx_score * W["adx_strength"]

    # ─── Factor 6: STOCHASTIC (10 pts) ───────────────────
    stoch_score = 0
    if is_buy:
        if stoch_k > stoch_d and stoch_k < 30:
            stoch_score = 1.0     # Crossing up from oversold
        elif stoch_k > stoch_d:
            stoch_score = 0.7     # Bullish crossover
        elif stoch_k < 20:
            stoch_score = 0.5     # Oversold waiting for cross
        else:
            stoch_score = 0.2
    else:
        if stoch_k < stoch_d and stoch_k > 70:
            stoch_score = 1.0
        elif stoch_k < stoch_d:
            stoch_score = 0.7
        elif stoch_k > 80:
            stoch_score = 0.5
        else:
            stoch_score = 0.2
    score += stoch_score * W["stochastic"]

    # ─── Factor 7: BODY DOMINANCE (10 pts) ───────────────
    body_score = 0
    if body_ratio > 0.7:
        body_score = 1.0          # Strong conviction candle
    elif body_ratio > 0.5:
        body_score = 0.7
    elif body_ratio > 0.3:
        body_score = 0.4
    else:
        body_score = 0.15         # Doji/indecision
    score += body_score * W["body_dominance"]

    # ─── Factor 8: MACRO OVERLAY (5 pts) ─────────────────
    # Pattern recognition boost (QML/Wyckoff from pattern_state)
    macro_score = 0
    if is_buy and pattern_state.get('bullish_qml', False):
        macro_score += 0.5
    elif not is_buy and pattern_state.get('bearish_qml', False):
        macro_score += 0.5
    if is_buy and pattern_state.get('wyckoff_spring', False):
        macro_score += 0.5
    elif not is_buy and pattern_state.get('wyckoff_upthrust', False):
        macro_score += 0.5
    score += min(1.0, macro_score) * W["macro_overlay"]

    # ─── Normalize to 0-100 ──────────────────────────────
    max_possible = sum(W.values())
    probability = min(100.0, (score / max_possible) * 100)

    return round(probability, 2)
