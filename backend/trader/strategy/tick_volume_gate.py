# -*- coding: utf-8 -*-
"""
Tick-volume entry gate for live selector and risk gate.

Purpose:
- Prefer entries only when tick volume is meaningfully above baseline.
- Boost confidence when large volume aligns with candle pressure.
- Block entries when high volume clearly pushes against the signal or shows exhaustion.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import pandas as pd

from backend.trader.config.paths import SETTINGS_PATH


_DEFAULT_CFG = {
    "enabled": True,
    "entry_ratio_min": 1.15,
    "large_volume_ratio_min": 1.35,
    "strong_ratio_min": 1.60,
    "extreme_volume_ratio_min": 2.10,
    "opposite_block_ratio": 1.35,
    "climax_ratio": 2.80,
    "climax_max_body_ratio": 0.35,
    "min_directional_pressure": 0.57,
    "alignment_boost": 0.04,
    "large_alignment_boost": 0.05,
    "strong_alignment_boost": 0.07,
    "extreme_alignment_boost": 0.10,
    "counter_signal_penalty": 0.05,
    "low_volume_penalty": 0.06,
    "require_large_volume_for_strict_models": True,
    "block_neutral_large_volume": True,
    "allow_low_volume_models": ["COUNTER_TREND", "BTC_MEAN_REV"],
    "strict_entry_models": [],
    "symbol_overrides": {},
}


def _load_cfg() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        raw = ((payload or {}).get("strategy", {}) or {}).get("tick_volume_gate", {}) or {}
    except Exception:
        raw = {}

    cfg = dict(_DEFAULT_CFG)
    cfg.update({k: v for k, v in raw.items() if v is not None})
    cfg["allow_low_volume_models"] = [
        str(model).upper() for model in cfg.get("allow_low_volume_models", []) if str(model).strip()
    ]
    cfg["strict_entry_models"] = [
        str(model).upper() for model in cfg.get("strict_entry_models", []) if str(model).strip()
    ]
    normalized_overrides = {}
    for raw_symbol, raw_cfg in (cfg.get("symbol_overrides", {}) or {}).items():
        symbol_key = _normalize_symbol_key(raw_symbol)
        if not symbol_key or not isinstance(raw_cfg, dict):
            continue
        merged = dict(cfg)
        merged.update({k: v for k, v in raw_cfg.items() if v is not None})
        merged["allow_low_volume_models"] = list(cfg.get("allow_low_volume_models", []))
        merged["strict_entry_models"] = list(cfg.get("strict_entry_models", []))
        merged["symbol_overrides"] = {}
        normalized_overrides[symbol_key] = merged
    cfg["symbol_overrides"] = normalized_overrides
    return cfg

@dataclass
class TickVolumeGateResult:
    allowed: bool = True
    confidence_delta: float = 0.0
    state: str = "NEUTRAL"
    reason: str = ""
    metrics: dict = field(default_factory=dict)


def _safe_float(value, fallback: float = 0.0) -> float:
    try:
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return fallback
        return out
    except Exception:
        return fallback


def _normalize_symbol_key(symbol: str) -> str:
    raw = str(symbol or "").upper().rstrip("MC.")
    if "XAU" in raw or "GOLD" in raw:
        return "XAUUSD"
    if "XAG" in raw or "SILVER" in raw:
        return "XAGUSD"
    if "BTC" in raw:
        return "BTCUSD"
    if "OIL" in raw:
        return "USOIL"
    if "30" in raw:
        return "US30"
    if "TEC" in raw or "NAS" in raw:
        return "USTEC"
    if "EURUSD" in raw:
        return "EURUSD"
    if "GBPUSD" in raw:
        return "GBPUSD"
    if "USDJPY" in raw:
        return "USDJPY"
    return raw


def _resolve_cfg(signal: dict | None = None, context: dict | None = None) -> dict:
    symbol = ""
    if isinstance(signal, dict):
        symbol = str(signal.get("symbol", "") or "")
    if not symbol and isinstance(context, dict):
        symbol = str(context.get("symbol", "") or "")

    symbol_key = _normalize_symbol_key(symbol)
    override = (_CFG.get("symbol_overrides", {}) or {}).get(symbol_key)
    return override or _CFG


_CFG = _load_cfg()


def _strict_model(model_name: str, cfg: dict) -> bool:
    model = str(model_name or "").upper()
    strict_models = cfg.get("strict_entry_models", [])
    if strict_models:
        return model in strict_models
    return model not in set(cfg.get("allow_low_volume_models", []))


def _volume_size_tier(vol_ratio: float, cfg: dict) -> str:
    entry_ratio = _safe_float(cfg.get("entry_ratio_min"), 1.15)
    large_ratio = max(entry_ratio, _safe_float(cfg.get("large_volume_ratio_min"), 1.35))
    strong_ratio = max(large_ratio, _safe_float(cfg.get("strong_ratio_min"), 1.60))
    extreme_ratio = max(strong_ratio, _safe_float(cfg.get("extreme_volume_ratio_min"), 2.10))

    if vol_ratio < entry_ratio:
        return "LOW"
    if vol_ratio < large_ratio:
        return "NORMAL"
    if vol_ratio < strong_ratio:
        return "LARGE"
    if vol_ratio < extreme_ratio:
        return "STRONG"
    return "EXTREME"


def summarize_latest_tick_volume(latest: pd.Series, cfg: dict | None = None) -> dict:
    cfg = cfg or _CFG
    close = _safe_float(latest.get("close"), 0.0)
    open_ = _safe_float(latest.get("open"), close)
    high = _safe_float(latest.get("high"), close)
    low = _safe_float(latest.get("low"), close)
    total_range = max(0.0, high - low)

    buy_pressure = _safe_float(latest.get("buy_pressure"), 0.5)
    sell_pressure = _safe_float(latest.get("sell_pressure"), 0.5)
    if total_range > 0:
        buy_pressure = _safe_float((close - low) / total_range, buy_pressure)
        sell_pressure = _safe_float((high - close) / total_range, sell_pressure)

    body_ratio = _safe_float(latest.get("body_ratio"), 0.0)
    if total_range > 0 and body_ratio <= 0:
        body_ratio = _safe_float(abs(close - open_) / total_range, 0.0)

    vol_ratio = _safe_float(
        latest.get("tick_vol_ratio_slow"),
        _safe_float(latest.get("tick_vol_ratio"), _safe_float(latest.get("vol_ratio"), 1.0)),
    )
    vol_avg = _safe_float(
        latest.get("tick_vol_avg_slow"),
        _safe_float(latest.get("tick_vol_avg"), _safe_float(latest.get("vol_avg"), 0.0)),
    )
    current_volume = _safe_float(latest.get("tick_volume"), _safe_float(latest.get("volume"), 0.0))
    delta = _safe_float(latest.get("vol_delta"), 0.0)

    directional_side = "NEUTRAL"
    pressure_gate = _safe_float(cfg.get("min_directional_pressure"), 0.57)
    if buy_pressure >= pressure_gate and delta >= 0:
        directional_side = "BUY"
    elif sell_pressure >= pressure_gate and delta <= 0:
        directional_side = "SELL"
    elif buy_pressure >= pressure_gate and buy_pressure > sell_pressure:
        directional_side = "BUY"
    elif sell_pressure >= pressure_gate and sell_pressure > buy_pressure:
        directional_side = "SELL"
    elif close > open_ and body_ratio >= 0.30:
        directional_side = "BUY"
    elif close < open_ and body_ratio >= 0.30:
        directional_side = "SELL"

    climax_ratio = _safe_float(cfg.get("climax_ratio"), 2.8)
    climax_body = _safe_float(cfg.get("climax_max_body_ratio"), 0.35)
    is_climax = bool(
        latest.get("volume_climax", False) or
        (vol_ratio >= climax_ratio and body_ratio <= climax_body)
    )
    size_tier = _volume_size_tier(vol_ratio, cfg)

    return {
        "current_volume": current_volume,
        "vol_avg": vol_avg,
        "vol_ratio": vol_ratio,
        "body_ratio": body_ratio,
        "buy_pressure": buy_pressure,
        "sell_pressure": sell_pressure,
        "directional_side": directional_side,
        "delta": delta,
        "is_climax": is_climax,
        "size_tier": size_tier,
        "is_large_volume": size_tier in {"LARGE", "STRONG", "EXTREME"},
    }


def evaluate_tick_volume(df: pd.DataFrame, signal: dict, context: dict | None = None) -> TickVolumeGateResult:
    cfg = _resolve_cfg(signal, context)
    if not cfg.get("enabled", True):
        return TickVolumeGateResult()
    if df is None or df.empty or not isinstance(signal, dict):
        return TickVolumeGateResult()

    latest = df.iloc[-1]
    metrics = summarize_latest_tick_volume(latest, cfg=cfg)
    side = str(signal.get("side", "")).upper()
    model = str(signal.get("model", "")).upper()

    result = TickVolumeGateResult(metrics=metrics)
    vol_ratio = _safe_float(metrics.get("vol_ratio"), 1.0)
    body_ratio = _safe_float(metrics.get("body_ratio"), 0.0)
    directional_side = str(metrics.get("directional_side", "NEUTRAL")).upper()
    size_tier = str(metrics.get("size_tier", "NORMAL")).upper()
    buy_pressure = _safe_float(metrics.get("buy_pressure"), 0.5)
    sell_pressure = _safe_float(metrics.get("sell_pressure"), 0.5)
    pressure_floor = _safe_float(cfg.get("min_directional_pressure"), 0.57)
    large_ratio = max(
        _safe_float(cfg.get("entry_ratio_min"), 1.15),
        _safe_float(cfg.get("large_volume_ratio_min"), 1.35),
    )
    strong_ratio = max(large_ratio, _safe_float(cfg.get("strong_ratio_min"), 1.60))
    extreme_ratio = max(strong_ratio, _safe_float(cfg.get("extreme_volume_ratio_min"), 2.10))

    if side not in {"BUY", "SELL"}:
        result.reason = f"TickVol neutral ({vol_ratio:.2f}x, {size_tier})"
        return result

    if bool(metrics.get("is_climax", False)):
        result.allowed = False
        result.state = "CLIMAX"
        result.reason = f"TickVol climax {vol_ratio:.2f}x with weak body {body_ratio:.2f}"
        return result

    min_ratio = _safe_float(cfg.get("entry_ratio_min"), 1.15)
    if vol_ratio < min_ratio:
        if _strict_model(model, cfg):
            result.allowed = False
            result.state = "LOW_VOLUME_BLOCK"
            result.reason = f"TickVol {vol_ratio:.2f}x < entry gate {min_ratio:.2f}x"
            return result
        result.confidence_delta = -_safe_float(cfg.get("low_volume_penalty"), 0.06)
        result.state = "LOW_VOLUME_PENALTY"
        result.reason = f"TickVol soft {vol_ratio:.2f}x < {min_ratio:.2f}x"
        return result

    if (
        bool(cfg.get("require_large_volume_for_strict_models", True))
        and _strict_model(model, cfg)
        and vol_ratio < large_ratio
    ):
        result.allowed = False
        result.state = "BELOW_LARGE_VOLUME"
        result.reason = f"TickVol {vol_ratio:.2f}x < large-volume gate {large_ratio:.2f}x"
        return result

    opposite_block_ratio = _safe_float(cfg.get("opposite_block_ratio"), 1.35)
    if directional_side in {"BUY", "SELL"} and directional_side != side and vol_ratio >= opposite_block_ratio:
        result.allowed = False
        result.state = "COUNTER_PRESSURE"
        result.reason = f"TickVol pressure {directional_side} vs signal {side} ({vol_ratio:.2f}x)"
        return result

    if directional_side == side:
        strong_pressure = max(buy_pressure, sell_pressure)
        if vol_ratio >= extreme_ratio and strong_pressure >= (pressure_floor + 0.10):
            result.confidence_delta = _safe_float(cfg.get("extreme_alignment_boost"), 0.10)
            result.state = "EXTREME_ALIGNMENT"
            result.reason = f"TickVol extreme {side} {vol_ratio:.2f}x ({size_tier})"
        elif vol_ratio >= strong_ratio and strong_pressure >= (pressure_floor + 0.08):
            result.confidence_delta = _safe_float(cfg.get("strong_alignment_boost"), 0.07)
            result.state = "STRONG_ALIGNMENT"
            result.reason = f"TickVol strong {side} {vol_ratio:.2f}x ({size_tier})"
        elif vol_ratio >= large_ratio:
            result.confidence_delta = _safe_float(cfg.get("large_alignment_boost"), 0.05)
            result.state = "LARGE_ALIGNMENT"
            result.reason = f"TickVol large {side} {vol_ratio:.2f}x ({size_tier})"
        else:
            result.confidence_delta = _safe_float(cfg.get("alignment_boost"), 0.04)
            result.state = "ALIGNED"
            result.reason = f"TickVol confirms {side} {vol_ratio:.2f}x ({size_tier})"
        return result

    if directional_side == "NEUTRAL":
        if bool(cfg.get("block_neutral_large_volume", True)) and _strict_model(model, cfg) and vol_ratio >= large_ratio:
            result.allowed = False
            result.state = "NEUTRAL_LARGE_VOLUME"
            result.reason = f"TickVol large {vol_ratio:.2f}x but pressure is neutral"
            return result
        result.state = "HIGH_VOLUME_NEUTRAL"
        result.reason = f"TickVol high {vol_ratio:.2f}x but neutral pressure ({size_tier})"
        return result

    result.confidence_delta = -_safe_float(cfg.get("counter_signal_penalty"), 0.05)
    result.state = "COUNTER_PRESSURE_PENALTY"
    result.reason = f"TickVol leaning {directional_side} ({vol_ratio:.2f}x, {size_tier})"
    return result
