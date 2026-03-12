from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache

from backend.trader.config.paths import SETTINGS_PATH
from backend.trader.data.mapper import mapper


_DEFAULT_CFG = {
    "enabled_symbols": ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"],
    "allowed_timeframes": ["M1", "M3", "M5"],
    "news": {
        "impact_levels": ["HIGH"],
        "general_block_minutes_before": 30,
        "general_block_minutes_after": 30,
        "trade_minutes_before": 0,
        "trade_minutes_after": 20,
        "pre_news_range_minutes": 30,
        "min_pre_news_bars": 4,
    },
    "volume": {
        "spike_ma_period": 12,
        "spike_threshold": 1.60,
        "directional_pressure_min": 0.58,
        "retest_volume_ratio_min": 0.95,
    },
    "price_action": {
        "breakout_body_ratio_min": 0.45,
        "breakout_close_buffer_atr": 0.08,
        "max_breakout_wick_ratio": 0.35,
        "max_breakout_distance_atr": 0.70,
        "require_retest": False,
        "retest_tolerance_atr": 0.18,
        "retest_max_bars": 2,
    },
    "risk": {
        "risk_pct": 0.25,
        "atr_stop_multiplier": 1.25,
        "stop_buffer_atr": 0.20,
        "min_stop_atr": 0.90,
        "max_stop_atr": 2.20,
        "tp1_rr": 1.60,
        "tp2_rr": 2.20,
        "tp3_rr": 3.00,
    },
    "execution": {
        "entry_type": "MARKET",
        "max_spread_points": 160.0,
        "max_slippage_points": 140.0,
    },
    "symbol_overrides": {
        "XAUUSD": {
            "news": {
                "pre_news_range_minutes": 45,
                "trade_minutes_after": 25,
            },
            "volume": {
                "spike_threshold": 1.70,
            },
            "risk": {
                "atr_stop_multiplier": 1.35,
                "max_stop_atr": 2.40,
            },
            "execution": {
                "max_spread_points": 180.0,
                "max_slippage_points": 140.0,
            },
        },
        "EURUSD": {
            "execution": {
                "max_spread_points": 28.0,
                "max_slippage_points": 22.0,
            },
        },
        "GBPUSD": {
            "volume": {
                "spike_threshold": 1.55,
            },
            "execution": {
                "max_spread_points": 34.0,
                "max_slippage_points": 26.0,
            },
        },
        "USDJPY": {
            "news": {
                "trade_minutes_after": 15,
            },
            "execution": {
                "max_spread_points": 28.0,
                "max_slippage_points": 24.0,
            },
        },
    },
    "backtest": {
        "news_events_json": "backend/trader/config/news_session_events.example.json",
    },
}


def _normalize_symbol(symbol: str) -> str:
    raw = mapper.to_standard(str(symbol or "").strip())
    text = str(raw or symbol or "").upper()
    if text.endswith(("M", "C")):
        text = text[:-1]
    return text


def _merge_dicts(base: dict, overrides: dict) -> dict:
    merged = deepcopy(base)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


@lru_cache(maxsize=1)
def _load_cfg() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        payload = {}

    raw = ((payload or {}).get("strategy", {}) or {}).get("news_session_momentum", {}) or {}
    return _merge_dicts(_DEFAULT_CFG, raw if isinstance(raw, dict) else {})


def clear_news_session_config_cache() -> None:
    _load_cfg.cache_clear()


def resolve_news_session_config(symbol: str, timeframe: str, context: dict | None = None) -> dict:
    cfg = deepcopy(_load_cfg())
    standard_symbol = _normalize_symbol(symbol)
    timeframe_token = str(timeframe or "").upper().strip()

    symbol_override = (cfg.get("symbol_overrides", {}) or {}).get(standard_symbol)
    if isinstance(symbol_override, dict):
        cfg = _merge_dicts(cfg, symbol_override)

    brain_params = ((context or {}).get("brain_params") or {}).get("news_session_momentum")
    if isinstance(brain_params, dict):
        cfg = _merge_dicts(cfg, brain_params)

    cfg["standard_symbol"] = standard_symbol
    cfg["timeframe"] = timeframe_token
    cfg["enabled_symbols"] = [_normalize_symbol(sym) for sym in cfg.get("enabled_symbols", [])]
    cfg["allowed_timeframes"] = [str(tf).upper() for tf in cfg.get("allowed_timeframes", [])]
    cfg["news"]["impact_levels"] = [str(level).upper() for level in cfg["news"].get("impact_levels", ["HIGH"])]
    return cfg


def strategy_allows_symbol_timeframe(symbol: str, timeframe: str, cfg: dict) -> bool:
    standard_symbol = _normalize_symbol(symbol)
    enabled_symbols = set(cfg.get("enabled_symbols", []) or [])
    allowed_timeframes = set(cfg.get("allowed_timeframes", []) or [])

    if enabled_symbols and standard_symbol not in enabled_symbols:
        return False
    if allowed_timeframes and str(timeframe or "").upper() not in allowed_timeframes:
        return False
    return True


def execution_guard_for(cfg: dict) -> dict:
    execution_cfg = dict(cfg.get("execution", {}) or {})
    return {
        "max_spread_points": float(execution_cfg.get("max_spread_points", 0.0) or 0.0),
        "max_slippage_points": float(execution_cfg.get("max_slippage_points", 0.0) or 0.0),
    }


def risk_profile_for(cfg: dict) -> dict:
    risk_cfg = dict(cfg.get("risk", {}) or {})
    return {
        "use_signal_levels": True,
        "risk_pct": float(risk_cfg.get("risk_pct", 0.25) or 0.25),
        "min_stop_atr": float(risk_cfg.get("min_stop_atr", 0.9) or 0.9),
        "max_stop_atr": float(risk_cfg.get("max_stop_atr", 2.2) or 2.2),
    }

