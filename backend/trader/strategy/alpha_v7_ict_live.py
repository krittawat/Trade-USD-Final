from __future__ import annotations

import copy
import json
import logging

import pandas as pd

from backend.app.domain.models import SymbolProfile
from backend.app.strategy.templates.alpha_v7_ict import AlphaV7ICTStrategy
from backend.trader.config.paths import SETTINGS_PATH
from backend.trader.data.mapper import mapper


logger = logging.getLogger("opus_logger")

_DEFAULT_CFG = {
    "allowed_timeframes": ["M5", "M15", "H1"],
    "defaults": {
        "min_adx": 17.0,
        "min_rr": 1.75,
        "tp_rr": 2.3,
        "range_lookback": 34,
        "sweep_lookback": 24,
        "sweep_confirm_bars": 6,
        "displacement_window": 4,
        "min_displacement_atr": 0.48,
        "displacement_body_ratio": 0.50,
        "flow_body_ratio": 0.62,
        "fvg_lookback": 10,
        "fvg_min_size_atr": 0.14,
        "stop_buffer_atr": 0.18,
        "retest_buffer_atr": 0.14,
        "retest_lookback_bars": 3,
        "retest_hold_buffer_atr": 0.10,
        "max_reentry_distance_atr": 0.50,
        "premium_discount_buffer": 0.08,
        "rsi_buy_min": 52.0,
        "rsi_sell_max": 48.0,
        "confidence_floor": 0.70,
        "require_prime_window": True,
    },
    "market_presets": {
        "metals": {
            "min_adx": 17.0,
            "min_rr": 1.9,
            "tp_rr": 2.4,
            "min_displacement_atr": 0.50,
            "fvg_min_size_atr": 0.10,
            "stop_buffer_atr": 0.16,
            "premium_discount_buffer": 0.06,
            "confidence_floor": 0.73,
        },
        "crypto": {
            "min_adx": 20.0,
            "min_rr": 2.1,
            "tp_rr": 2.8,
            "sweep_lookback": 30,
            "min_displacement_atr": 0.75,
            "fvg_min_size_atr": 0.18,
            "stop_buffer_atr": 0.24,
            "premium_discount_buffer": 0.10,
            "require_prime_window": False,
            "confidence_floor": 0.74,
        },
        "energy": {
            "min_adx": 18.0,
            "min_rr": 1.9,
            "tp_rr": 2.5,
            "min_displacement_atr": 0.62,
            "fvg_min_size_atr": 0.14,
            "stop_buffer_atr": 0.22,
        },
        "indices": {
            "min_adx": 19.0,
            "min_rr": 2.0,
            "tp_rr": 2.6,
            "sweep_lookback": 28,
            "min_displacement_atr": 0.68,
            "fvg_min_size_atr": 0.16,
            "stop_buffer_atr": 0.24,
            "confidence_floor": 0.74,
        },
        "forex": {
            "min_adx": 16.0,
            "min_rr": 1.7,
            "tp_rr": 2.2,
            "min_displacement_atr": 0.38,
            "fvg_min_size_atr": 0.09,
            "stop_buffer_atr": 0.14,
            "premium_discount_buffer": 0.10,
            "confidence_floor": 0.70,
        },
    },
    "timeframe_presets": {
        "M5": {
            "range_lookback": 30,
            "sweep_lookback": 22,
            "sweep_confirm_bars": 5,
            "retest_lookback_bars": 4,
            "max_reentry_distance_atr": 0.42,
        },
        "M15": {
            "range_lookback": 38,
            "sweep_lookback": 26,
            "sweep_confirm_bars": 6,
            "retest_lookback_bars": 3,
            "max_reentry_distance_atr": 0.46,
        },
        "H1": {
            "range_lookback": 28,
            "sweep_lookback": 18,
            "sweep_confirm_bars": 4,
            "min_displacement_atr": 0.45,
            "fvg_lookback": 5,
            "require_prime_window": False,
        },
    },
    "symbol_presets": {
        "XAUUSD": {
            "min_rr": 2.0,
            "tp_rr": 2.5,
            "fvg_min_size_atr": 0.09,
            "confidence_floor": 0.76,
        },
        "XAGUSD": {
            "min_rr": 1.9,
            "tp_rr": 2.5,
            "stop_buffer_atr": 0.20,
        },
        "BTCUSD": {
            "min_adx": 20.0,
            "min_rr": 2.2,
            "tp_rr": 3.0,
            "min_displacement_atr": 0.82,
            "sweep_lookback": 32,
            "fvg_min_size_atr": 0.16,
            "max_reentry_distance_atr": 0.60,
            "confidence_floor": 0.77,
        },
        "USOIL": {
            "min_adx": 20.0,
            "min_rr": 2.0,
            "tp_rr": 2.7,
        },
        "US30": {
            "min_adx": 22.0,
            "min_rr": 2.1,
            "tp_rr": 2.8,
        },
        "USTEC": {
            "min_adx": 22.0,
            "min_rr": 2.1,
            "tp_rr": 2.8,
        },
        "EURUSD": {
            "min_adx": 15.0,
            "min_rr": 1.7,
            "tp_rr": 2.1,
        },
        "GBPUSD": {
            "min_adx": 16.0,
            "min_rr": 1.8,
            "tp_rr": 2.2,
        },
        "USDJPY": {
            "min_adx": 16.0,
            "min_rr": 1.8,
            "tp_rr": 2.2,
        },
    },
    "symbol_timeframe_presets": {
        "XAUUSD": {
            "M5": {"range_lookback": 28, "min_displacement_atr": 0.54, "fvg_min_size_atr": 0.08, "max_reentry_distance_atr": 0.38},
            "M15": {"range_lookback": 36, "min_displacement_atr": 0.50, "fvg_min_size_atr": 0.09},
        },
        "BTCUSD": {
            "M5": {"min_adx": 19.0, "min_displacement_atr": 0.74, "fvg_min_size_atr": 0.14, "max_reentry_distance_atr": 0.70},
            "M15": {"min_adx": 19.0, "min_displacement_atr": 0.72, "fvg_min_size_atr": 0.15, "tp_rr": 2.9},
        },
        "EURUSD": {
            "M5": {"min_displacement_atr": 0.40, "fvg_min_size_atr": 0.10},
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_cfg() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        raw = ((payload or {}).get("strategy", {}) or {}).get("alpha_v7_ict", {}) or {}
    except Exception:
        raw = {}
    return _deep_merge(_DEFAULT_CFG, raw if isinstance(raw, dict) else {})


_CFG = _load_cfg()


def _normalize_symbol_key(symbol: str) -> str:
    standardized = str(mapper.to_standard(str(symbol or "").strip()) or symbol or "").upper()
    if standardized.endswith(("M", "C")):
        standardized = standardized[:-1]
    return standardized


def _market_key(symbol: str) -> str:
    sym = _normalize_symbol_key(symbol)
    if any(token in sym for token in ["XAU", "XAG", "GOLD", "SILVER"]):
        return "metals"
    if "BTC" in sym:
        return "crypto"
    if "OIL" in sym:
        return "energy"
    if any(token in sym for token in ["US30", "USTEC", "NAS", "DJ"]):
        return "indices"
    if any(token in sym for token in ["EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "USD"]):
        return "forex"
    return "other"


def _resolve_strategy_params(symbol: str, timeframe: str, context: dict | None = None) -> dict:
    ctx = context if isinstance(context, dict) else {}
    params = dict((_CFG.get("defaults") or {}))
    symbol_key = _normalize_symbol_key(symbol)
    market = _market_key(symbol)
    tf_key = str(timeframe or "M5").upper()

    market_cfg = (_CFG.get("market_presets", {}) or {}).get(market)
    if isinstance(market_cfg, dict):
        params.update(market_cfg)

    timeframe_cfg = (_CFG.get("timeframe_presets", {}) or {}).get(tf_key)
    if isinstance(timeframe_cfg, dict):
        params.update(timeframe_cfg)

    symbol_cfg = (_CFG.get("symbol_presets", {}) or {}).get(symbol_key)
    if isinstance(symbol_cfg, dict):
        params.update(symbol_cfg)

    symbol_tf_cfg = ((_CFG.get("symbol_timeframe_presets", {}) or {}).get(symbol_key, {}) or {}).get(tf_key)
    if isinstance(symbol_tf_cfg, dict):
        params.update(symbol_tf_cfg)

    session_key = str(ctx.get("session") or ctx.get("current_session") or "").upper().strip()
    session_cfg = (_CFG.get("session_presets", {}) or {}).get(session_key)
    if isinstance(session_cfg, dict):
        params.update(session_cfg)

    symbol_session_cfg = ((_CFG.get("symbol_session_presets", {}) or {}).get(symbol_key, {}) or {}).get(session_key)
    if isinstance(symbol_session_cfg, dict):
        params.update(symbol_session_cfg)

    brain_params = (ctx.get("brain_params", {}) or {}).get("alpha_v7_ict", {})
    if isinstance(brain_params, dict):
        params.update(brain_params)

    params["allowed_timeframes"] = list(_CFG.get("allowed_timeframes", ["M5", "M15", "H1"]))
    params["timeframe"] = tf_key
    return params


def signal_alpha_v7_ict(df: pd.DataFrame, context: dict) -> dict | None:
    symbol = str((context or {}).get("symbol") or "UNKNOWN")
    timeframe = str((context or {}).get("timeframe") or "M5").upper()
    params = _resolve_strategy_params(symbol, timeframe, context)
    strategy = AlphaV7ICTStrategy(symbol=symbol, **params)
    profile = SymbolProfile(symbol=symbol)

    try:
        decision = strategy.analyze(df, profile, timeframe=timeframe, context=context)
        if decision.action.name not in ("BUY", "SELL"):
            return None

        entry = float(df["close"].iloc[-1])
        sl = float(decision.stop_loss)
        risk = abs(entry - sl)
        if risk <= 0:
            return None

        primary_tp = float(decision.take_profit)
        rr_primary = abs(primary_tp - entry) / risk if risk > 0 else 0.0
        rr_secondary = max(rr_primary * 1.35, rr_primary + 0.55)
        rr_runner = max(rr_primary * 1.85, rr_primary + 1.10)

        if decision.action.name == "BUY":
            tp2 = entry + (risk * rr_secondary)
            tp3 = entry + (risk * rr_runner)
        else:
            tp2 = entry - (risk * rr_secondary)
            tp3 = entry - (risk * rr_runner)

        rationale = [decision.reason] + [str(tag) for tag in decision.tags if str(tag).strip()]
        return {
            "symbol": symbol,
            "side": decision.action.name,
            "entry_type": "MARKET",
            "entry_price": entry,
            "sl": round(sl, 5),
            "tp1": round(primary_tp, 5),
            "tp2": round(tp2, 5),
            "tp3": round(tp3, 5),
            "rationale": rationale,
            "confidence": float(decision.confidence),
            "model": "ALPHA_V7_ICT",
        }
    except Exception as exc:
        logger.error("Error in alpha_v7_ict_live: %s", exc, exc_info=True)
        return None
