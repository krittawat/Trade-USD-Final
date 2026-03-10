# -*- coding: utf-8 -*-
"""
Predicta Futures V4 Strategy — 8-Point Confluence System
Ported จาก gold-risk-engine predicta_futures_v4.py

FEATURES:
1. Custom Supertrend (direction detection)
2. Volume Delta (buy/sell volume apportionment)
3. Volatility Regime (ATR percentile → multiplier)
4. 8-Point Confluence scoring: Trend, EMA, MACD, Stoch, Volume, ADX, RSI, Delta
5. Dynamic Threshold based on ADX strength
6. Perfect Time Logic (all factors aligned)
"""
import json
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("predicta_v4")

with open("d:/VibeCode/Trade/backend/trader/config/settings.json") as _f:
    _P4_CFG = json.load(_f).get("strategy", {}).get("predicta_v4", {})

MIN_CONFLUENCE = _P4_CFG.get("min_confluence", 4)
MIN_VOLUME_RATIO = 0.8
ADX_THRESHOLD = 25


def signal_predicta_v4(df: pd.DataFrame, context: dict) -> dict:
    """
    Predicta V4: 8-point confluence with Supertrend + Volume Delta.
    Returns signal dict or None.
    """
    if len(df) < 110:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # ─── Extract indicators (populated by volatility.py) ────
    close = float(latest['close'])
    rsi = float(latest.get('rsi', 50))
    adx_val = float(latest.get('adx', 0))
    stoch_k = float(latest.get('stoch_k', 50))
    stoch_d = float(latest.get('stoch_d', 50))
    macd_line = float(latest.get('macd_line', 0))
    macd_sig = float(latest.get('macd_signal', 0))
    macd_hist = float(latest.get('macd_hist', 0))
    vol_ratio = float(latest.get('vol_ratio', 1.0))
    vol_delta = float(latest.get('vol_delta', 0))
    delta_bullish = bool(latest.get('delta_bullish', False))
    delta_bearish = bool(latest.get('delta_bearish', False))
    atr = float(latest.get('atr', 0))

    ema_fast_val = float(latest.get('ema_fast', close))
    ema_slow_val = float(latest.get('ema_slow', close))
    ema_200 = float(latest.get('ema_200', close))

    is_uptrend = bool(latest.get('is_uptrend', False))
    is_downtrend = bool(latest.get('is_downtrend', False))

    if atr <= 0 or np.isnan(atr):
        atr = close * 0.005

    if np.isnan(adx_val):
        adx_val = 0
    if np.isnan(rsi):
        rsi = 50

    # ─── Volatility Regime Multiplier ──────────────
    atr_rank = df['atr'].rolling(100, min_periods=20).rank(pct=True).iloc[-1] * 100
    if np.isnan(atr_rank):
        atr_rank = 50
    vol_mult = 0.85 if atr_rank > 75 else 1.15 if atr_rank < 25 else 1.0

    # ─── Scoring System (7 weighted factors) ───────
    # MACD Score
    macd_score_long = 100 if (macd_line > macd_sig and macd_hist > 0) else \
        70 if (macd_line > macd_sig) else 50 if (macd_hist > 0) else 20
    macd_score_short = 100 if (macd_line < macd_sig and macd_hist < 0) else \
        70 if (macd_line < macd_sig) else 50 if (macd_hist < 0) else 20

    # RSI Score
    rsi_score_long = 100 if rsi < 30 else 85 if rsi < 40 else 70 if rsi < 50 else 50 if rsi < 60 else 25
    rsi_score_short = 100 if rsi > 70 else 85 if rsi > 60 else 70 if rsi > 50 else 50 if rsi > 40 else 25

    # Stoch Score
    stoch_score_long = 100 if (stoch_k > stoch_d and stoch_k < 20) else \
        85 if (stoch_k > stoch_d and stoch_k < 50) else 65 if stoch_k > stoch_d else 25
    stoch_score_short = 100 if (stoch_k < stoch_d and stoch_k > 80) else \
        85 if (stoch_k < stoch_d and stoch_k > 50) else 65 if stoch_k < stoch_d else 25

    # Volume Score
    vol_score = 100 if vol_ratio > 2.0 else 80 if vol_ratio > 1.5 else \
        60 if vol_ratio > 1.0 else 45 if vol_ratio > 0.8 else 25

    # Delta Score
    delta_ema = float(latest.get('delta_ema', 0))
    delta_mom = vol_delta > delta_ema
    delta_score_long = 100 if (vol_delta > 0 and delta_mom) else 75 if vol_delta > 0 else 20
    delta_score_short = 100 if (vol_delta < 0 and not delta_mom) else 75 if vol_delta < 0 else 20

    # ADX Score
    adx_score = 100 if adx_val > 35 else 85 if adx_val > 30 else \
        70 if adx_val > 25 else 50 if adx_val > 20 else 30

    # Trend Score
    trend_score_long = 100 if (is_uptrend and ema_fast_val > ema_slow_val and ema_slow_val > ema_200) else \
        80 if (is_uptrend and ema_fast_val > ema_slow_val) else 60 if is_uptrend else 0
    trend_score_short = 100 if (is_downtrend and ema_fast_val < ema_slow_val and ema_slow_val < ema_200) else \
        80 if (is_downtrend and ema_fast_val < ema_slow_val) else 60 if is_downtrend else 0

    # Weighted sum
    long_raw = (trend_score_long * 0.23 + macd_score_long * 0.18 + delta_score_long * 0.15 +
                rsi_score_long * 0.12 + stoch_score_long * 0.12 + adx_score * 0.10 + vol_score * 0.10)
    short_raw = (trend_score_short * 0.23 + macd_score_short * 0.18 + delta_score_short * 0.15 +
                 rsi_score_short * 0.12 + stoch_score_short * 0.12 + adx_score * 0.10 + vol_score * 0.10)

    long_pct = round(min(100, max(0, long_raw * vol_mult)))
    short_pct = round(min(100, max(0, short_raw * vol_mult)))

    total = long_pct + short_pct
    final_long = round(long_pct / total * 100) if total > 0 else 50
    final_short = 100 - final_long

    # ─── Dynamic Threshold (ADX-based) ────────────
    dyn_thresh = 50 if adx_val > 30 else 55 if adx_val > 25 else 60 if adx_val > 20 else 65

    # ─── 8-Point Confluence ──────────────────────
    volume_ok = vol_ratio >= MIN_VOLUME_RATIO

    conf_long = sum([
        is_uptrend, ema_fast_val > ema_slow_val,
        macd_line > macd_sig, stoch_k > stoch_d,
        volume_ok, adx_val > ADX_THRESHOLD,
        rsi > 50, delta_bullish,
    ])
    conf_short = sum([
        is_downtrend, ema_fast_val < ema_slow_val,
        macd_line < macd_sig, stoch_k < stoch_d,
        volume_ok, adx_val > ADX_THRESHOLD,
        rsi < 50, delta_bearish,
    ])

    # ─── Perfect Time (full alignment) ──────────
    long_perfect = (is_uptrend and final_long >= dyn_thresh and
                    conf_long >= MIN_CONFLUENCE and volume_ok and
                    rsi > 50 and delta_bullish)
    short_perfect = (is_downtrend and final_short >= dyn_thresh and
                     conf_short >= MIN_CONFLUENCE and volume_ok and
                     rsi < 50 and delta_bearish)

    # ─── EMA Cross (secondary signal) ─────────
    prev_ema_f = float(prev.get('ema_fast', 0))
    prev_ema_s = float(prev.get('ema_slow', 0))
    ema_cross_bull = (prev_ema_f < prev_ema_s) and (ema_fast_val > ema_slow_val)
    ema_cross_bear = (prev_ema_f > prev_ema_s) and (ema_fast_val < ema_slow_val)

    bull_signal = ema_cross_bull and is_uptrend and delta_bullish
    bear_signal = ema_cross_bear and is_downtrend and delta_bearish

    # ─── Signal Generation ──────────────────────
    sl_dist = atr * 2.0 * vol_mult
    tp_dist = atr * 3.0 * vol_mult
    
    tv = int(latest.get('tick_volume', 0))
    vol_str = f"Vol:{tv} (x{vol_ratio:.1f})"

    if long_perfect:
        reasons = [f"PERFECT LONG ({final_long}%)", f"Conf {conf_long}/8", vol_str]
        return _build("BUY", close, sl_dist, tp_dist, symbol, reasons, final_long / 100.0)

    if short_perfect:
        reasons = [f"PERFECT SHORT ({final_short}%)", f"Conf {conf_short}/8", vol_str]
        return _build("SELL", close, sl_dist, tp_dist, symbol, reasons, final_short / 100.0)

    if bull_signal and conf_long >= 3:
        reasons = ["EMA Cross + Delta BUY", f"Conf {conf_long}/8", vol_str]
        return _build("BUY", close, sl_dist, tp_dist, symbol, reasons, final_long / 100.0 * 0.85)

    if bear_signal and conf_short >= 3:
        reasons = ["EMA Cross + Delta SELL", f"Conf {conf_short}/8", vol_str]
        return _build("SELL", close, sl_dist, tp_dist, symbol, reasons, final_short / 100.0 * 0.85)

    return None


def _build(side: str, price: float, sl_dist: float, tp_dist: float,
           symbol: str, reasons: list, confidence: float) -> dict:
    if side == "BUY":
        sl = price - sl_dist
        risk = price - sl
        tp1 = price + risk * 1.2
        tp2 = price + tp_dist
        tp3 = price + tp_dist * 1.5
    else:
        sl = price + sl_dist
        risk = sl - price
        tp1 = price - risk * 1.2
        tp2 = price - tp_dist
        tp3 = price - tp_dist * 1.5

    return {
        "symbol": symbol, "side": side, "entry_type": "MARKET",
        "entry_price": price, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rationale": reasons, "confidence": min(1.0, max(0.0, confidence)),
        "model": "PREDICTA_V4",
    }
