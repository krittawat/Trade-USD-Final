# -*- coding: utf-8 -*-
"""
Rapid Pullback strategy for short-term intraday trading.

Design goals:
- Follow the dominant short-term trend only.
- Enter on shallow pullbacks into EMA/VWAP support or resistance.
- Require momentum re-expansion to avoid catching weak bounces.
- Keep stops tight but not absurdly small.
"""
from __future__ import annotations

import json
import math
import pandas as pd

from backend.trader.config.paths import SETTINGS_PATH

with open(SETTINGS_PATH, "r", encoding="utf-8") as _f:
    _STRATEGY_CFG = json.load(_f).get("strategy", {})

_RAPID_CFG = _STRATEGY_CFG.get("rapid_pullback", {})


DEFAULT_PARAMS = {
    "lookback": 6,
    "touch_atr": 0.35,
    "sl_buffer_atr": 0.25,
    "min_sl_atr": 0.75,
    "max_sl_atr": 1.80,
    "tp1_rr": 1.6,
    "tp2_rr": 2.2,
    "tp3_rr": 3.0,
    "min_body_ratio": 0.45,
    "min_vol_ratio": 1.00,
    "min_roc": 0.04,
    "min_di_gap": 2.0,
    "min_rsi_buy": 52.0,
    "max_rsi_buy": 72.0,
    "min_rsi_sell": 28.0,
    "max_rsi_sell": 48.0,
}


SYMBOL_PARAMS = {
    "XAU": {
        "touch_atr": 0.30,
        "sl_buffer_atr": 0.30,
        "min_sl_atr": 0.85,
        "max_sl_atr": 1.60,
        "min_roc": 0.03,
        "min_di_gap": 2.5,
        "tp1_rr": 1.5,
        "tp2_rr": 2.1,
        "tp3_rr": 2.8,
    },
    "BTC": {
        "touch_atr": 0.45,
        "sl_buffer_atr": 0.35,
        "min_sl_atr": 1.10,
        "max_sl_atr": 2.20,
        "min_roc": 0.08,
        "min_di_gap": 3.5,
        "min_vol_ratio": 1.05,
        "tp1_rr": 1.8,
        "tp2_rr": 2.5,
        "tp3_rr": 3.4,
    },
    "USOIL": {
        "touch_atr": 0.40,
        "sl_buffer_atr": 0.30,
        "min_sl_atr": 0.90,
        "max_sl_atr": 1.90,
        "min_roc": 0.05,
        "min_di_gap": 3.0,
        "min_vol_ratio": 1.05,
    },
}


def _normalize_symbol(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    if sym.endswith(("M", "C")):
        sym = sym[:-1]
    return sym


def _match_symbol_key(symbol: str, mapping: dict) -> str | None:
    std = _normalize_symbol(symbol)
    for key in mapping:
        probe = _normalize_symbol(str(key))
        if probe and probe in std:
            return str(key)
    return None


def _is_allowed(symbol: str, timeframe: str) -> bool:
    allowed_symbols = list(_RAPID_CFG.get("allowed_symbols", []))
    allowed_timeframes = [str(tf).upper() for tf in _RAPID_CFG.get("allowed_timeframes", [])]

    if allowed_symbols:
        if _match_symbol_key(symbol, {sym: True for sym in allowed_symbols}) is None:
            return False

    if allowed_timeframes and timeframe:
        if str(timeframe).upper() not in allowed_timeframes:
            return False

    return True


def _params_for_context(symbol: str, timeframe: str, context: dict) -> dict:
    params = DEFAULT_PARAMS.copy()
    params.update(_RAPID_CFG.get("defaults", {}))

    sym = (symbol or "").upper()
    for key, overrides in SYMBOL_PARAMS.items():
        if key in sym:
            params.update(overrides)

    market_presets = _RAPID_CFG.get("market_presets", {})
    market_key = _match_symbol_key(symbol, market_presets)
    if market_key:
        params.update(market_presets.get(market_key, {}))

    tf = str(timeframe or "").upper()
    timeframe_presets = _RAPID_CFG.get("timeframe_presets", {})
    if tf and tf in timeframe_presets:
        params.update(timeframe_presets[tf])

    combo_presets = _RAPID_CFG.get("symbol_timeframe_presets", {})
    combo_key = _match_symbol_key(symbol, combo_presets)
    if combo_key:
        tf_cfg = combo_presets.get(combo_key, {})
        if tf and tf in tf_cfg:
            params.update(tf_cfg[tf])

    brain_params = (context.get("brain_params") or {}).get("rapid_pullback")
    if isinstance(brain_params, dict):
        params.update(brain_params)

    return params


def _safe_float(value, fallback: float = 0.0) -> float:
    try:
        out = float(value)
        if math.isnan(out):
            return fallback
        return out
    except Exception:
        return fallback


def _regime_allows(regime_name: str, side: str) -> bool:
    regime = (regime_name or "").lower()
    if "compression" in regime or "sideways" in regime or "distribution" in regime or "accumulation" in regime:
        return False
    if "down" in regime and side == "BUY":
        return False
    if "up" in regime and side == "SELL":
        return False
    return ("trend" in regime) or ("expansion" in regime)


def _near_pullback_zone(
    latest: pd.Series,
    recent: pd.DataFrame,
    atr: float,
    side: str,
    params: dict,
) -> bool:
    touch_dist = atr * float(params["touch_atr"])
    if side == "BUY":
        lows = recent["low"]
        return (
            (lows - recent["ema_fast"]).abs().min() <= touch_dist
            or (lows - recent["vwap"]).abs().min() <= touch_dist
            or (float(latest["low"]) <= float(latest["ema_fast"]) + touch_dist)
            or (float(latest["low"]) <= float(latest["vwap"]) + touch_dist)
        )

    highs = recent["high"]
    return (
        (highs - recent["ema_fast"]).abs().min() <= touch_dist
        or (highs - recent["vwap"]).abs().min() <= touch_dist
        or (float(latest["high"]) >= float(latest["ema_fast"]) - touch_dist)
        or (float(latest["high"]) >= float(latest["vwap"]) - touch_dist)
    )


def _build_signal(
    side: str,
    latest: pd.Series,
    recent: pd.DataFrame,
    symbol: str,
    params: dict,
    rationale: list[str],
    confidence: float,
) -> dict | None:
    entry = _safe_float(latest.get("close"))
    atr = _safe_float(latest.get("atr"), entry * 0.005)
    if entry <= 0 or atr <= 0:
        return None

    lookback = int(params["lookback"])
    recent_slice = recent.tail(lookback)
    if recent_slice.empty:
        return None

    if side == "BUY":
        swing = _safe_float(recent_slice["low"].min())
        sl = swing - (atr * float(params["sl_buffer_atr"]))
        risk = entry - sl
        if risk <= 0:
            return None
        sl_atr = risk / atr
        if sl_atr < float(params["min_sl_atr"]) or sl_atr > float(params["max_sl_atr"]):
            return None
        return {
            "symbol": symbol,
            "side": "BUY",
            "entry_type": "MARKET",
            "entry_price": round(entry, 5),
            "sl": round(sl, 5),
            "tp1": round(entry + (risk * float(params["tp1_rr"])), 5),
            "tp2": round(entry + (risk * float(params["tp2_rr"])), 5),
            "tp3": round(entry + (risk * float(params["tp3_rr"])), 5),
            "rationale": rationale,
            "confidence": min(0.95, confidence),
            "model": "RAPID_PULLBACK",
        }

    swing = _safe_float(recent_slice["high"].max())
    sl = swing + (atr * float(params["sl_buffer_atr"]))
    risk = sl - entry
    if risk <= 0:
        return None
    sl_atr = risk / atr
    if sl_atr < float(params["min_sl_atr"]) or sl_atr > float(params["max_sl_atr"]):
        return None
    return {
        "symbol": symbol,
        "side": "SELL",
        "entry_type": "MARKET",
        "entry_price": round(entry, 5),
        "sl": round(sl, 5),
        "tp1": round(entry - (risk * float(params["tp1_rr"])), 5),
        "tp2": round(entry - (risk * float(params["tp2_rr"])), 5),
        "tp3": round(entry - (risk * float(params["tp3_rr"])), 5),
        "rationale": rationale,
        "confidence": min(0.95, confidence),
        "model": "RAPID_PULLBACK",
    }


def signal_rapid_pullback(df: pd.DataFrame, context: dict) -> dict | None:
    if len(df) < 80:
        return None

    symbol = context.get("symbol", "UNKNOWN")
    timeframe = str(context.get("timeframe", "") or "").upper()
    if not _is_allowed(symbol, timeframe):
        return None

    params = _params_for_context(symbol, timeframe, context)
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    recent = df.iloc[-8:]

    close = _safe_float(latest.get("close"))
    atr = _safe_float(latest.get("atr"), close * 0.005)
    if close <= 0 or atr <= 0:
        return None

    regime_name = context.get("regime_result", {}).get("regime", "")

    ema_fast = _safe_float(latest.get("ema_fast"), close)
    ema_slow = _safe_float(latest.get("ema_slow"), close)
    ema_200 = _safe_float(latest.get("ema_200"), close)
    vwap = _safe_float(latest.get("vwap"), close)
    rsi = _safe_float(latest.get("rsi"), 50.0)
    roc = _safe_float(latest.get("roc_5"), 0.0)
    body_ratio = _safe_float(latest.get("body_ratio"), 0.0)
    vol_ratio = _safe_float(latest.get("vol_ratio"), 0.0)
    plus_di = _safe_float(latest.get("plus_di"), 0.0)
    minus_di = _safe_float(latest.get("minus_di"), 0.0)
    is_uptrend = bool(latest.get("is_uptrend", False))
    is_downtrend = bool(latest.get("is_downtrend", False))
    lower_wick = _safe_float(latest.get("lower_wick_ratio"), 0.0)
    upper_wick = _safe_float(latest.get("upper_wick_ratio"), 0.0)

    min_body_ratio = float(params["min_body_ratio"])
    min_vol_ratio = float(params["min_vol_ratio"])

    if body_ratio < min_body_ratio or vol_ratio < min_vol_ratio:
        return None

    bullish_stack = ema_fast > ema_slow and close > ema_200
    bearish_stack = ema_fast < ema_slow and close < ema_200
    bull_trend = bullish_stack and close >= (ema_fast - 0.10 * atr) and close >= (vwap - 0.20 * atr) and not is_downtrend
    bear_trend = bearish_stack and close <= (ema_fast + 0.10 * atr) and close <= (vwap + 0.20 * atr) and not is_uptrend

    # BUY: shallow pullback into fast support followed by re-expansion.
    if _regime_allows(regime_name, "BUY") and bull_trend:
        di_gap = plus_di - minus_di
        near_pullback = _near_pullback_zone(latest, recent, atr, "BUY", params)
        resumed_up = (
            close >= (ema_fast - 0.05 * atr)
            and close >= _safe_float(latest.get("open"), close)
            and close > _safe_float(prev.get("close"), close)
        )
        if (
            near_pullback
            and resumed_up
            and lower_wick >= 0.12
            and di_gap >= float(params["min_di_gap"])
            and roc >= float(params["min_roc"])
            and float(params["min_rsi_buy"]) <= rsi <= float(params["max_rsi_buy"])
        ):
            rationale = [
                f"Rapid pullback buy in {regime_name or 'trend'}",
                f"EMA/VWAP reclaim after pullback ({vol_ratio:.2f}x vol)",
                f"DI+ lead {di_gap:.1f}, RSI {rsi:.1f}, ROC {roc:.2f}",
            ]
            confidence = 0.66
            if close > _safe_float(prev.get("high"), close):
                confidence += 0.05
            if vol_ratio >= 1.20:
                confidence += 0.04
            if lower_wick >= 0.25:
                confidence += 0.03
            return _build_signal("BUY", latest, recent, symbol, params, rationale, confidence)

    # SELL: mirrored setup into fast resistance.
    if _regime_allows(regime_name, "SELL") and bear_trend:
        di_gap = minus_di - plus_di
        near_pullback = _near_pullback_zone(latest, recent, atr, "SELL", params)
        resumed_down = (
            close <= (ema_fast + 0.05 * atr)
            and close <= _safe_float(latest.get("open"), close)
            and close < _safe_float(prev.get("close"), close)
        )
        if (
            near_pullback
            and resumed_down
            and upper_wick >= 0.12
            and di_gap >= float(params["min_di_gap"])
            and roc <= -float(params["min_roc"])
            and float(params["min_rsi_sell"]) <= rsi <= float(params["max_rsi_sell"])
        ):
            rationale = [
                f"Rapid pullback sell in {regime_name or 'trend'}",
                f"EMA/VWAP rejection after pullback ({vol_ratio:.2f}x vol)",
                f"DI- lead {di_gap:.1f}, RSI {rsi:.1f}, ROC {roc:.2f}",
            ]
            confidence = 0.66
            if close < _safe_float(prev.get("low"), close):
                confidence += 0.05
            if vol_ratio >= 1.20:
                confidence += 0.04
            if upper_wick >= 0.25:
                confidence += 0.03
            return _build_signal("SELL", latest, recent, symbol, params, rationale, confidence)

    return None
