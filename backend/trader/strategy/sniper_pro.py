# -*- coding: utf-8 -*-
"""
Sniper Pro Strategy — HTF-Confirmed Trend Pullback
Ported จาก gold-risk-engine (D:\\Trade\\gold-risk-engine\\backend\\app\\strategy\\sniper_pro.py)

LOGIC:
1. HTF Trend Filter (EMA 200 on current timeframe as D1 proxy)
2. RSI Pullback Entry (< threshold for BUY, > threshold for SELL)
3. Stochastic Oversold/Overbought confirmation
4. Bollinger Band value zone check
5. ADX Trend Strength filter
6. Candlestick (bullish/bearish) confirmation trigger
7. Session filter (London/NY for metals)

ASSET-SPECIFIC CONFIGS: XAU (aggressive), XAG (trend hunter), BTC (wide target)
"""
import json
import logging
import numpy as np
import pandas as pd

from backend.trader.config.paths import SETTINGS_PATH

logger = logging.getLogger("sniper_pro")

# Load strategy params from config
with open(SETTINGS_PATH, encoding="utf-8") as _f:
    _SNIPER_CFG = json.load(_f).get("strategy", {}).get("sniper_pro", {})

# Asset-specific configurations (validated from backtests)
CONFIGS = {
    "DEFAULT": {
        "rsi_buy": 30, "rsi_sell": 70,
        "stoch_k": 25, "bb_dev": 2.0,
        "adx": 20,
        "sl_mult": 2.5, "tp_mult": 3.0,
        "min_conf": _SNIPER_CFG.get("min_confidence", 75),
    },
    "XAU": {  # Gold "SNIPER ELITE" — pullback entry with wide reward
        "rsi_buy": 35, "rsi_sell": 65,
        "stoch_k": 30, "bb_dev": 2.2,
        "adx": 22,
        "sl_mult": 1.5, "tp_mult": 4.0,  # RR ~1:2.6
        "min_conf": 75,
    },
    "XAG": {  # Silver — trend hunter
        "rsi_buy": 35, "rsi_sell": 65,
        "stoch_k": 25, "bb_dev": 2.0,
        "adx": 20,
        "sl_mult": 2.0, "tp_mult": 5.0,
        "min_conf": 75,
    },
    "BTC": {  # Bitcoin — wide target is king
        "rsi_buy": 35, "rsi_sell": 65,
        "stoch_k": 20, "bb_dev": 2.0,
        "adx": 20,
        "sl_mult": 2.5, "tp_mult": 5.0,
        "min_conf": 80,
    },
}


def _get_cfg(symbol: str) -> dict:
    sym = symbol.upper()
    if "XAU" in sym:
        return CONFIGS["XAU"]
    elif "XAG" in sym:
        return CONFIGS["XAG"]
    elif "BTC" in sym:
        return CONFIGS["BTC"]
    return CONFIGS["DEFAULT"]


def signal_sniper_pro(df: pd.DataFrame, context: dict) -> dict:
    """
    Sniper Pro: HTF pullback with multi-indicator confluence + Volume.

    Returns signal dict compatible with project pipeline, or None.
    """
    if len(df) < 200:
        return None

    symbol = context.get('symbol', 'DEFAULT')
    cfg = _get_cfg(symbol).copy()

    # Dynamic Parameters from AI Brain
    # Try 'ranging_sniper' (from training script) or 'sniper_pro'
    strat_params = context.get('brain_params', {}).get('ranging_sniper')
    if not strat_params:
        strat_params = context.get('brain_params', {}).get('sniper_pro')
    
    if strat_params:
        cfg.update(strat_params)
        logger.debug(f"SNIPER_PRO: Using AI Evolved Params: {strat_params}")


    latest = df.iloc[-1]
    price = float(latest['close'])

    # ─── Required indicators (all populated by volatility.py) ─────
    rsi = float(latest.get('rsi', 50))
    adx_val = float(latest.get('adx', 0))
    stoch_k = float(latest.get('stoch_k', 50))
    bb_upper = float(latest.get('bb_upper', price))
    bb_lower = float(latest.get('bb_lower', price))
    ema_200 = float(latest.get('ema_200', price))
    atr = float(latest.get('atr', 0))

    if atr <= 0 or np.isnan(atr):
        atr = price * 0.005  # Fallback 0.5%

    # ─── Volume Features ─────────────────────────
    vol_ratio = float(latest.get('vol_ratio', 0.0))
    vol_spike = bool(latest.get('vol_spike', False))
    delta_bullish = bool(latest.get('delta_bullish', False))
    delta_bearish = bool(latest.get('delta_bearish', False))

    # ─── HTF Trend (EMA 200 proxy) ─────────────────────
    # Approximate D1 trend using 200-bar EMA on M5 data
    htf_ema = df['close'].ewm(span=200, adjust=False).mean().iloc[-1]
    htf_trend = "UP" if price > htf_ema else "DOWN"
    ltf_trend = "UP" if price > ema_200 else "DOWN"

    # ─── Session Filter (metals) ─────────────────────
    is_active_session = True
    if "XAU" in symbol.upper() or "XAG" in symbol.upper():
        try:
            idx = df.index[-1]
            current_hour = idx.hour if hasattr(idx, 'hour') else 12
            if current_hour < 8 or current_hour > 21:
                is_active_session = False
        except Exception:
            pass

    # ─── Candlestick confirmation ───────────────────
    is_bullish = float(latest['close']) > float(latest['open'])
    is_bearish = float(latest['close']) < float(latest['open'])

    # ─── Scoring ───────────────────────────────────
    def _evaluate_buy() -> dict:
        if htf_trend != "UP" or ltf_trend != "UP":
            return None

        confidence = 50
        reasons = []

        # 1. RSI Pullback
        if rsi < cfg['rsi_buy']:
            confidence += 20
            reasons.append(f"RSI Pullback ({rsi:.1f}<{cfg['rsi_buy']})")
        else:
            return None  # RSI pullback is mandatory

        # 2. Stochastic
        if stoch_k < cfg['stoch_k']:
            confidence += 20
            reasons.append(f"Stoch Oversold ({stoch_k:.1f})")

        # 3. BB Value Zone
        if price <= bb_lower * 1.002:
            confidence += 20
            reasons.append("BB Low Touch")

        # 4. ADX Strength
        if not np.isnan(adx_val) and adx_val > cfg['adx']:
            confidence += 10
            reasons.append(f"ADX {adx_val:.0f}")

        # 5. Session
        if is_active_session:
            confidence += 10
        else:
            confidence -= 20
            reasons.append("Low Session")

        # 6. Candle confirmation (mandatory trigger)
        if not is_bullish:
            return None  # Need bullish candle to trigger

        confidence += 20
        reasons.append("Bullish Candle")

        # 7. Volume Confirmation (pullback reversal)
        if vol_ratio >= 1.2:
            confidence += 15
            reasons.append(f"Vol Pullback Confirmed ({vol_ratio:.1f}x)")
        if vol_spike:
            confidence += 10
            reasons.append("🔊 Vol Spike Reversal")
        if delta_bullish:
            confidence += 5
            reasons.append("Vol Delta Bullish")

        if confidence < cfg['min_conf']:
            return None

        sl = price - (atr * cfg['sl_mult'])
        risk = price - sl
        tp1 = price + risk * 1.5
        tp2 = price + (atr * cfg['tp_mult'])
        tp3 = price + (atr * cfg['tp_mult'] * 1.5)

        return {
            "symbol": symbol, "side": "BUY", "entry_type": "MARKET",
            "entry_price": price, "sl": sl,
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "rationale": reasons, "confidence": min(1.0, confidence / 100.0),
            "model": "SNIPER_PRO",
        }

    def _evaluate_sell() -> dict:
        if htf_trend != "DOWN" or ltf_trend != "DOWN":
            return None

        confidence = 50
        reasons = []

        if rsi > cfg['rsi_sell']:
            confidence += 20
            reasons.append(f"RSI Throwback ({rsi:.1f}>{cfg['rsi_sell']})")
        else:
            return None

        if stoch_k > (100 - cfg['stoch_k']):
            confidence += 20
            reasons.append(f"Stoch Overbought ({stoch_k:.1f})")

        if price >= bb_upper * 0.998:
            confidence += 20
            reasons.append("BB High Touch")

        if not np.isnan(adx_val) and adx_val > cfg['adx']:
            confidence += 10
            reasons.append(f"ADX {adx_val:.0f}")

        if is_active_session:
            confidence += 10
        else:
            confidence -= 20

        if not is_bearish:
            return None

        confidence += 20
        reasons.append("Bearish Candle")

        # Volume Confirmation (pullback reversal)
        if vol_ratio >= 1.2:
            confidence += 15
            reasons.append(f"Vol Pullback Confirmed ({vol_ratio:.1f}x)")
        if vol_spike:
            confidence += 10
            reasons.append("🔊 Vol Spike Reversal")
        if delta_bearish:
            confidence += 5
            reasons.append("Vol Delta Bearish")

        if confidence < cfg['min_conf']:
            return None

        sl = price + (atr * cfg['sl_mult'])
        risk = sl - price
        tp1 = price - risk * 1.5
        tp2 = price - (atr * cfg['tp_mult'])
        tp3 = price - (atr * cfg['tp_mult'] * 1.5)

        return {
            "symbol": symbol, "side": "SELL", "entry_type": "MARKET",
            "entry_price": price, "sl": sl,
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "rationale": reasons, "confidence": min(1.0, confidence / 100.0),
            "model": "SNIPER_PRO",
        }

    # Try both directions
    sig = _evaluate_buy()
    if sig:
        return sig
    return _evaluate_sell()
