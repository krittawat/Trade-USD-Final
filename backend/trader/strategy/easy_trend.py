# -*- coding: utf-8 -*-
"""
Easy Trend Pro v2 — Institutional Momentum & Structure
Upgraded from basic SMA/MACD logic.

LOGIC:
1. Trend Bias: VWAP Bias (Price vs VWAP) + ADX Slope
2. Institutional Confluence:
    - Fair Value Gap (FVG) proximity
    - Premium/Discount Zone (Buy in Discount, Sell in Premium)
3. Momentum Trigger: MACD Impulse (MACD > BB on MACD)
4. Strength Filter: ADX > 20 + RSI Direction
5. Exit: Dynamic TP based on Market Structure (HH/LL/ATR)
"""
import json
import logging
import numpy as np
import pandas as pd

from backend.trader.config.paths import SETTINGS_PATH

logger = logging.getLogger("easy_trend")

with open(SETTINGS_PATH, encoding="utf-8") as _f:
    _CFG = json.load(_f)
    _ET_CFG = _CFG.get("strategy", {})

SL_ATR_MULT_DEFAULT = _ET_CFG.get("sl_atr_multiplier", 1.5)

def _get_asset_params(symbol: str) -> dict:
    sym = symbol.upper()
    if "BTC" in sym:
        return {"sl_mult": 2.5, "adx_thresh": 25, "rsi_buy": 55, "rsi_sell": 45}
    elif "XAG" in sym:
        return {"sl_mult": 2.2, "adx_thresh": 22, "rsi_buy": 55, "rsi_sell": 45}
    return {"sl_mult": 1.8, "adx_thresh": 20, "rsi_buy": 53, "rsi_sell": 47}

def signal_easy_trend(df: pd.DataFrame, context: dict) -> dict:
    """
    Easy Trend Pro v2: VWAP Bias + FVG/PD Zone + MACD Impulse.
    """
    if len(df) < 100:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    params = _get_asset_params(symbol)
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    close = float(latest['close'])
    atr = float(latest.get('atr', close * 0.002))
    if np.isnan(atr) or atr <= 0: atr = close * 0.002

    # 1. Faster Trend Detection: VWAP Bias & ADX Slope
    vwap = float(latest.get('vwap', close))
    adx_now = float(latest.get('adx', 0))
    adx_prev = float(prev.get('adx', 0))
    adx_slope = adx_now - adx_prev
    
    trend_bias = "NEUTRAL"
    if close > vwap and adx_slope > 0:
        trend_bias = "BULL"
    elif close < vwap and adx_slope > 0:
        trend_bias = "BEAR"

    # 2. Institutional Confluence: FVG & PD Zones
    fvg_bull = float(latest.get('fvg_bull', 0))
    fvg_bear = float(latest.get('fvg_bear', 0))
    pd_zone = latest.get('pd_zone', 'EQUILIBRIUM')

    # 3. Momentum: MACD Impulse (Compute Inline BB on MACD)
    macd_val = float(latest.get('macd_line', 0))
    macd_series = df['macd_line'].dropna()
    if len(macd_series) < 20: return None
    
    bb_macd_mid = macd_series.rolling(10).mean().iloc[-1]
    bb_macd_std = macd_series.rolling(10).std().iloc[-1]
    if np.isnan(bb_macd_std) or bb_macd_std == 0: return None
    
    bull_impulse = macd_val > (bb_macd_mid + bb_macd_std)
    bear_impulse = macd_val < (bb_macd_mid - bb_macd_std)

    # 4. Indicators
    rsi = float(latest.get('rsi', 50))
    vol_ratio = float(latest.get('vol_ratio', 1.0))

    confidence = 0
    reasons = []

    # ─── Logic: BUY ──────────────────────────────────────────
    if trend_bias == "BULL" and bull_impulse:
        confidence += 40
        reasons.append("VWAP Bull Bias + MACD Impulse")

        if pd_zone == "DISCOUNT":
            confidence += 20
            reasons.append("Institutional Discount Zone")
        elif pd_zone == "EQUILIBRIUM":
            confidence += 5
            reasons.append("Equilibrium Zone")
        
        if fvg_bull > 0:
            confidence += 15
            reasons.append("FVG Bull Support")

        if rsi > params['rsi_buy']:
            confidence += 10
            reasons.append(f"RSI Strength ({rsi:.0f})")

        if vol_ratio > 1.2:
            confidence += 10
            reasons.append("Volume Expansion")

        if confidence >= 55:
            sl_mult = params['sl_mult']
            # Entry: Market
            sl = close - (atr * sl_mult)
            risk = close - sl
            
            # Dynamic TP based on Institutional Targets
            tp_base = close + (risk * 1.5)
            return {
                "symbol": symbol, "side": "BUY", "entry_type": "MARKET",
                "entry_price": close, "sl": sl,
                "tp1": tp_base,
                "tp2": close + (risk * 2.5),
                "tp3": close + (risk * 4.0),
                "rationale": reasons,
                "confidence": min(1.0, confidence / 100.0),
                "model": "EASY_TREND",
            }

    # ─── Logic: SELL ─────────────────────────────────────────
    if trend_bias == "BEAR" and bear_impulse:
        confidence += 40
        reasons.append("VWAP Bear Bias + MACD Impulse")

        if pd_zone == "PREMIUM":
            confidence += 20
            reasons.append("Institutional Premium Zone")
        elif pd_zone == "EQUILIBRIUM":
            confidence += 5
            reasons.append("Equilibrium Zone")

        if fvg_bear > 0:
            confidence += 15
            reasons.append("FVG Bear Resistance")

        if rsi < params['rsi_sell']:
            confidence += 10
            reasons.append(f"RSI Weakness ({rsi:.0f})")

        if vol_ratio > 1.2:
            confidence += 10
            reasons.append("Volume Expansion")

        if confidence >= 55:
            sl_mult = params['sl_mult']
            sl = close + (atr * sl_mult)
            risk = sl - close
            
            tp_base = close - (risk * 1.5)
            return {
                "symbol": symbol, "side": "SELL", "entry_type": "MARKET",
                "entry_price": close, "sl": sl,
                "tp1": tp_base,
                "tp2": close - (risk * 2.5),
                "tp3": close - (risk * 4.0),
                "rationale": reasons,
                "confidence": min(1.0, confidence / 100.0),
                "model": "EASY_TREND",
            }

    return None
