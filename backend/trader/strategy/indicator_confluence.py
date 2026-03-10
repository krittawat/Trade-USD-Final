# -*- coding: utf-8 -*-
"""
Indicator Confluence Strategy (MT5-style)

Signal framework inspired by the chart setup:
- EMA stack on price
- MACD(12,26,9)
- RSI(14)
- Bulls/Bears Power (period 25)
- Momentum(14)
- Volume confirmation

This strategy is tuned per symbol because each market has different noise,
volatility structure, and momentum profile.
"""
import json
import logging
from typing import Dict

import numpy as np
import pandas as pd

logger = logging.getLogger("indicator_confluence")

with open("d:/VibeCode/Trade/backend/trader/config/settings.json") as _f:
    _STRATEGY_CFG = json.load(_f).get("strategy", {})


_BASE_PARAMS: Dict[str, object] = {
    "ema_fast_period": 9,
    "ema_mid_period": 21,
    "ema_slow_period": 50,
    "sl_atr_mult": 1.2,
    "tp_rr": 1.8,
    "min_score": 6,
    "min_volume_ratio": 1.0,
    "rsi_buy_min": 50.0,
    "rsi_buy_max": 68.0,
    "rsi_sell_min": 32.0,
    "rsi_sell_max": 50.0,
    "momentum_buy_min": 100.05,
    "momentum_sell_max": 99.95,
    "min_sl_pct": 0.0007,
    "min_atr_pct": 0.00035,
    "require_trend_regime": True,
    "allowed_timeframes": ["M5", "M12", "M15", "H1"],
}
_BASE_PARAMS.update(_STRATEGY_CFG.get("indicator_confluence", {}))


# Symbol-level tuning. Keys are substring matches on broker symbol.
_SYMBOL_TUNING: Dict[str, Dict[str, object]] = {
    "BTC": {
        "sl_atr_mult": 2.0,
        "tp_rr": 2.2,
        "min_score": 7,
        "min_volume_ratio": 1.10,
        "rsi_buy_min": 53.0,
        "rsi_buy_max": 72.0,
        "rsi_sell_min": 28.0,
        "rsi_sell_max": 47.0,
        "momentum_buy_min": 100.25,
        "momentum_sell_max": 99.75,
        "min_sl_pct": 0.0020,
        "min_atr_pct": 0.0012,
        "allowed_timeframes": ["M5", "M15", "H1"],
    },
    "XAU": {
        "sl_atr_mult": 1.35,
        "tp_rr": 1.9,
        "min_score": 6,
        "min_volume_ratio": 1.05,
        "rsi_buy_min": 52.0,
        "rsi_buy_max": 70.0,
        "rsi_sell_min": 30.0,
        "rsi_sell_max": 48.0,
        "momentum_buy_min": 100.10,
        "momentum_sell_max": 99.90,
        "min_sl_pct": 0.0010,
        "min_atr_pct": 0.0006,
    },
    "XAG": {
        "sl_atr_mult": 1.4,
        "tp_rr": 2.0,
        "min_score": 6,
        "min_volume_ratio": 1.05,
        "rsi_buy_min": 52.0,
        "rsi_buy_max": 70.0,
        "rsi_sell_min": 30.0,
        "rsi_sell_max": 48.0,
        "momentum_buy_min": 100.10,
        "momentum_sell_max": 99.90,
        "min_sl_pct": 0.0012,
        "min_atr_pct": 0.0007,
    },
    "USOIL": {
        "sl_atr_mult": 1.5,
        "tp_rr": 2.0,
        "min_score": 6,
        "min_volume_ratio": 1.05,
        "rsi_buy_min": 52.0,
        "rsi_buy_max": 69.0,
        "rsi_sell_min": 31.0,
        "rsi_sell_max": 48.0,
        "momentum_buy_min": 100.15,
        "momentum_sell_max": 99.85,
        "min_sl_pct": 0.0015,
        "min_atr_pct": 0.0008,
    },
    "US30": {
        "sl_atr_mult": 1.6,
        "tp_rr": 2.1,
        "min_score": 6,
        "min_volume_ratio": 1.0,
        "rsi_buy_min": 51.0,
        "rsi_buy_max": 70.0,
        "rsi_sell_min": 30.0,
        "rsi_sell_max": 49.0,
        "momentum_buy_min": 100.12,
        "momentum_sell_max": 99.88,
        "min_sl_pct": 0.0011,
        "min_atr_pct": 0.0007,
    },
    "USTEC": {
        "sl_atr_mult": 1.7,
        "tp_rr": 2.15,
        "min_score": 6,
        "min_volume_ratio": 1.0,
        "rsi_buy_min": 51.0,
        "rsi_buy_max": 70.0,
        "rsi_sell_min": 30.0,
        "rsi_sell_max": 49.0,
        "momentum_buy_min": 100.12,
        "momentum_sell_max": 99.88,
        "min_sl_pct": 0.0012,
        "min_atr_pct": 0.0007,
    },
    "EURUSD": {
        "sl_atr_mult": 1.05,
        "tp_rr": 1.7,
        "min_score": 5,
        "min_volume_ratio": 0.95,
        "rsi_buy_min": 50.0,
        "rsi_buy_max": 66.0,
        "rsi_sell_min": 34.0,
        "rsi_sell_max": 50.0,
        "momentum_buy_min": 100.03,
        "momentum_sell_max": 99.97,
        "min_sl_pct": 0.0005,
        "min_atr_pct": 0.00025,
    },
    "GBPUSD": {
        "sl_atr_mult": 1.15,
        "tp_rr": 1.75,
        "min_score": 5,
        "min_volume_ratio": 0.95,
        "rsi_buy_min": 50.0,
        "rsi_buy_max": 66.0,
        "rsi_sell_min": 34.0,
        "rsi_sell_max": 50.0,
        "momentum_buy_min": 100.04,
        "momentum_sell_max": 99.96,
        "min_sl_pct": 0.0006,
        "min_atr_pct": 0.0003,
    },
    "USDJPY": {
        "sl_atr_mult": 1.15,
        "tp_rr": 1.75,
        "min_score": 5,
        "min_volume_ratio": 0.95,
        "rsi_buy_min": 50.0,
        "rsi_buy_max": 66.0,
        "rsi_sell_min": 34.0,
        "rsi_sell_max": 50.0,
        "momentum_buy_min": 100.04,
        "momentum_sell_max": 99.96,
        "min_sl_pct": 0.0006,
        "min_atr_pct": 0.0003,
    },
}


def _safe_float(value, fallback: float = 0.0) -> float:
    try:
        v = float(value)
        if np.isnan(v) or np.isinf(v):
            return fallback
        return v
    except Exception:
        return fallback


def _resolve_params(symbol: str, context: dict) -> dict:
    params = dict(_BASE_PARAMS)
    symbol_upper = str(symbol).upper()
    params["profile"] = "DEFAULT"

    for key, overrides in _SYMBOL_TUNING.items():
        if key in symbol_upper:
            params.update(overrides)
            params["profile"] = key
            break

    # Optional dynamic overrides from brain bridge
    brain_params = (context.get("brain_params") or {}).get("indicator_confluence")
    if isinstance(brain_params, dict):
        params.update(brain_params)

    return params


def signal_indicator_confluence(df: pd.DataFrame, context: dict) -> dict:
    """Confluence signal with symbol-specific tuning."""
    if len(df) < 80:
        return None

    symbol = context.get("symbol", "UNKNOWN")
    timeframe = str(context.get("timeframe", ""))
    params = _resolve_params(symbol, context)

    allowed_tfs = {str(tf) for tf in params.get("allowed_timeframes", [])}
    if allowed_tfs and timeframe and timeframe not in allowed_tfs:
        return None

    regime_name = str(context.get("regime_result", {}).get("regime", "")).lower()
    if params.get("require_trend_regime", True):
        if regime_name and ("trend" not in regime_name) and ("expansion" not in regime_name):
            return None

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = _safe_float(latest.get("close"), 0.0)
    if close <= 0:
        return None

    atr = _safe_float(latest.get("atr"), 0.0)
    if atr <= 0:
        fallback_atr = _safe_float((df["high"].tail(20) - df["low"].tail(20)).mean(), close * 0.002)
        atr = fallback_atr if fallback_atr > 0 else close * 0.002

    atr_pct = atr / close if close else 0.0
    if atr_pct < _safe_float(params.get("min_atr_pct", 0.0), 0.0):
        return None

    ema_fast_period = int(params.get("ema_fast_period", 9))
    ema_mid_period = int(params.get("ema_mid_period", 21))
    ema_slow_period = int(params.get("ema_slow_period", 50))
    close_series = df["close"]
    ema_fast_series = close_series.ewm(span=ema_fast_period, adjust=False).mean()
    ema_mid_series = close_series.ewm(span=ema_mid_period, adjust=False).mean()
    ema_slow_series = close_series.ewm(span=ema_slow_period, adjust=False).mean()

    ema_fast = _safe_float(ema_fast_series.iloc[-1], close)
    ema_mid = _safe_float(ema_mid_series.iloc[-1], close)
    ema_slow = _safe_float(ema_slow_series.iloc[-1], close)
    ema_fast_prev = _safe_float(ema_fast_series.iloc[-2], ema_fast)
    ema_mid_prev = _safe_float(ema_mid_series.iloc[-2], ema_mid)
    ema_slow_prev = _safe_float(ema_slow_series.iloc[-2], ema_slow)

    macd_line = _safe_float(latest.get("macd_line"), 0.0)
    macd_signal = _safe_float(latest.get("macd_signal"), 0.0)
    macd_hist = _safe_float(latest.get("macd_hist"), macd_line - macd_signal)
    macd_hist_prev = _safe_float(prev.get("macd_hist"), macd_hist)
    rsi = _safe_float(latest.get("rsi"), 50.0)
    vol_ratio = _safe_float(latest.get("vol_ratio"), 1.0)
    momentum_14 = _safe_float(latest.get("momentum_14"), 100.0)
    bull_power = _safe_float(latest.get("bull_power_25", latest.get("bull_power")), 0.0)
    bear_power = _safe_float(latest.get("bear_power_25", latest.get("bear_power")), 0.0)
    net_power = _safe_float(latest.get("net_power_25", latest.get("net_power")), bull_power + bear_power)
    bull_power_prev = _safe_float(prev.get("bull_power_25", prev.get("bull_power")), bull_power)
    bear_power_prev = _safe_float(prev.get("bear_power_25", prev.get("bear_power")), bear_power)

    buy_score = 0
    buy_reasons = [f"profile={params.get('profile')} tf={timeframe}"]
    if ema_fast > ema_mid > ema_slow:
        buy_score += 1
        buy_reasons.append(f"EMA stack up ({ema_fast_period}>{ema_mid_period}>{ema_slow_period})")
    if (ema_fast > ema_fast_prev) and (ema_mid >= ema_mid_prev) and (close >= ema_fast):
        buy_score += 1
        buy_reasons.append("EMA slope up + price above EMA fast")
    if (macd_line > macd_signal) and (macd_hist > 0) and (macd_hist >= macd_hist_prev):
        buy_score += 1
        buy_reasons.append("MACD bullish impulse")
    if _safe_float(params.get("rsi_buy_min"), 50.0) <= rsi <= _safe_float(params.get("rsi_buy_max"), 68.0):
        buy_score += 1
        buy_reasons.append(f"RSI in buy zone ({rsi:.1f})")
    if (bull_power > 0) and (net_power > 0) and (bear_power >= bear_power_prev):
        buy_score += 1
        buy_reasons.append("Bulls/Bears(25) buyer pressure")
    if momentum_14 >= _safe_float(params.get("momentum_buy_min"), 100.05):
        buy_score += 1
        buy_reasons.append(f"Momentum14 strong ({momentum_14:.2f})")
    if vol_ratio >= _safe_float(params.get("min_volume_ratio"), 1.0):
        buy_score += 1
        buy_reasons.append(f"Volume confirm ({vol_ratio:.2f})")

    sell_score = 0
    sell_reasons = [f"profile={params.get('profile')} tf={timeframe}"]
    if ema_fast < ema_mid < ema_slow:
        sell_score += 1
        sell_reasons.append(f"EMA stack down ({ema_fast_period}<{ema_mid_period}<{ema_slow_period})")
    if (ema_fast < ema_fast_prev) and (ema_mid <= ema_mid_prev) and (close <= ema_fast):
        sell_score += 1
        sell_reasons.append("EMA slope down + price below EMA fast")
    if (macd_line < macd_signal) and (macd_hist < 0) and (macd_hist <= macd_hist_prev):
        sell_score += 1
        sell_reasons.append("MACD bearish impulse")
    if _safe_float(params.get("rsi_sell_min"), 32.0) <= rsi <= _safe_float(params.get("rsi_sell_max"), 50.0):
        sell_score += 1
        sell_reasons.append(f"RSI in sell zone ({rsi:.1f})")
    if (bear_power < 0) and (net_power < 0) and (bull_power <= bull_power_prev):
        sell_score += 1
        sell_reasons.append("Bulls/Bears(25) seller pressure")
    if momentum_14 <= _safe_float(params.get("momentum_sell_max"), 99.95):
        sell_score += 1
        sell_reasons.append(f"Momentum14 weak ({momentum_14:.2f})")
    if vol_ratio >= _safe_float(params.get("min_volume_ratio"), 1.0):
        sell_score += 1
        sell_reasons.append(f"Volume confirm ({vol_ratio:.2f})")

    min_score = int(max(1, _safe_float(params.get("min_score"), 6)))
    if buy_score < min_score and sell_score < min_score:
        return None
    if buy_score == sell_score:
        return None

    side = "BUY" if buy_score > sell_score else "SELL"
    score = buy_score if side == "BUY" else sell_score
    reasons = buy_reasons if side == "BUY" else sell_reasons

    sl_atr_mult = _safe_float(params.get("sl_atr_mult"), 1.2)
    min_sl_pct = _safe_float(params.get("min_sl_pct"), 0.0007)
    tp_rr = _safe_float(params.get("tp_rr"), 1.8)

    sl_distance = max(atr * sl_atr_mult, close * min_sl_pct)
    if sl_distance <= 0:
        return None

    entry = close
    if side == "BUY":
        sl = entry - sl_distance
        tp1 = entry + (sl_distance * tp_rr)
        tp2 = entry + (sl_distance * (tp_rr + 0.7))
        tp3 = entry + (sl_distance * (tp_rr + 1.5))
    else:
        sl = entry + sl_distance
        tp1 = entry - (sl_distance * tp_rr)
        tp2 = entry - (sl_distance * (tp_rr + 0.7))
        tp3 = entry - (sl_distance * (tp_rr + 1.5))

    confidence = 0.64 + ((score / 7.0) * 0.24)
    confidence += min(0.04, abs(buy_score - sell_score) * 0.02)
    confidence = float(min(0.95, max(0.55, confidence)))

    reasons.append(f"Score {score}/7")
    reasons.append(
        f"MACD={macd_hist:+.5f} RSI={rsi:.1f} MOM14={momentum_14:.2f} "
        f"PWR={net_power:+.5f}"
    )

    return {
        "symbol": symbol,
        "side": side,
        "entry_type": "MARKET",
        "entry_price": float(entry),
        "sl": float(round(sl, 5)),
        "tp1": float(round(tp1, 5)),
        "tp2": float(round(tp2, 5)),
        "tp3": float(round(tp3, 5)),
        "rationale": reasons,
        "confidence": confidence,
        "model": "INDICATOR_CONFLUENCE",
    }
