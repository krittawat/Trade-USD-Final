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
    "strong_ratio_min": 1.60,
    "opposite_block_ratio": 1.35,
    "climax_ratio": 2.80,
    "climax_max_body_ratio": 0.35,
    "min_directional_pressure": 0.57,
    "alignment_boost": 0.04,
    "strong_alignment_boost": 0.07,
    "counter_signal_penalty": 0.05,
    "low_volume_penalty": 0.06,
    "allow_low_volume_models": ["COUNTER_TREND", "BTC_MEAN_REV"],
    "strict_entry_models": [],
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
    return cfg


_CFG = _load_cfg()


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


def _strict_model(model_name: str) -> bool:
    model = str(model_name or "").upper()
    strict_models = _CFG.get("strict_entry_models", [])
    if strict_models:
        return model in strict_models
    return model not in set(_CFG.get("allow_low_volume_models", []))


def summarize_latest_tick_volume(latest: pd.Series) -> dict:
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
    pressure_gate = _safe_float(_CFG.get("min_directional_pressure"), 0.57)
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

    climax_ratio = _safe_float(_CFG.get("climax_ratio"), 2.8)
    climax_body = _safe_float(_CFG.get("climax_max_body_ratio"), 0.35)
    is_climax = bool(
        latest.get("volume_climax", False) or
        (vol_ratio >= climax_ratio and body_ratio <= climax_body)
    )

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
    }


def evaluate_tick_volume(df: pd.DataFrame, signal: dict, context: dict | None = None) -> TickVolumeGateResult:
    if not _CFG.get("enabled", True):
        return TickVolumeGateResult()
    if df is None or df.empty or not isinstance(signal, dict):
        return TickVolumeGateResult()

    latest = df.iloc[-1]
    metrics = summarize_latest_tick_volume(latest)
    side = str(signal.get("side", "")).upper()
    model = str(signal.get("model", "")).upper()

    result = TickVolumeGateResult(metrics=metrics)
    vol_ratio = _safe_float(metrics.get("vol_ratio"), 1.0)
    body_ratio = _safe_float(metrics.get("body_ratio"), 0.0)
    directional_side = str(metrics.get("directional_side", "NEUTRAL")).upper()
    buy_pressure = _safe_float(metrics.get("buy_pressure"), 0.5)
    sell_pressure = _safe_float(metrics.get("sell_pressure"), 0.5)

    if side not in {"BUY", "SELL"}:
        result.reason = f"TickVol neutral ({vol_ratio:.2f}x)"
        return result

    if bool(metrics.get("is_climax", False)):
        result.allowed = False
        result.state = "CLIMAX"
        result.reason = f"TickVol climax {vol_ratio:.2f}x with weak body {body_ratio:.2f}"
        return result

    min_ratio = _safe_float(_CFG.get("entry_ratio_min"), 1.15)
    if vol_ratio < min_ratio:
        if _strict_model(model):
            result.allowed = False
            result.state = "LOW_VOLUME_BLOCK"
            result.reason = f"TickVol {vol_ratio:.2f}x < entry gate {min_ratio:.2f}x"
            return result
        result.confidence_delta = -_safe_float(_CFG.get("low_volume_penalty"), 0.06)
        result.state = "LOW_VOLUME_PENALTY"
        result.reason = f"TickVol soft {vol_ratio:.2f}x < {min_ratio:.2f}x"
        return result

    opposite_block_ratio = _safe_float(_CFG.get("opposite_block_ratio"), 1.35)
    if directional_side in {"BUY", "SELL"} and directional_side != side and vol_ratio >= opposite_block_ratio:
        result.allowed = False
        result.state = "COUNTER_PRESSURE"
        result.reason = f"TickVol pressure {directional_side} vs signal {side} ({vol_ratio:.2f}x)"
        return result

    if directional_side == side:
        strong_ratio = _safe_float(_CFG.get("strong_ratio_min"), 1.60)
        strong_pressure = max(buy_pressure, sell_pressure)
        if vol_ratio >= strong_ratio and strong_pressure >= (_safe_float(_CFG.get("min_directional_pressure"), 0.57) + 0.08):
            result.confidence_delta = _safe_float(_CFG.get("strong_alignment_boost"), 0.07)
            result.state = "STRONG_ALIGNMENT"
            result.reason = f"TickVol strong {side} {vol_ratio:.2f}x"
        else:
            result.confidence_delta = _safe_float(_CFG.get("alignment_boost"), 0.04)
            result.state = "ALIGNED"
            result.reason = f"TickVol confirms {side} {vol_ratio:.2f}x"
        return result

    if directional_side == "NEUTRAL":
        result.state = "HIGH_VOLUME_NEUTRAL"
        result.reason = f"TickVol high {vol_ratio:.2f}x but neutral pressure"
        return result

    result.confidence_delta = -_safe_float(_CFG.get("counter_signal_penalty"), 0.05)
    result.state = "COUNTER_PRESSURE_PENALTY"
    result.reason = f"TickVol leaning {directional_side} ({vol_ratio:.2f}x)"
    return result
