"""
Strategy invocation helpers.

This module centralizes:
1) Safe strategy analyze() invocation with signature fallbacks.
2) Parameter normalization between training/evolution outputs and live strategy kwargs.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.domain.enums import RegimeType


_SIGNATURE_MISMATCH_MARKERS = (
    "unexpected keyword argument",
    "required positional argument",
    "positional argument",
    "takes ",
    "got an unexpected",
)


def is_signature_mismatch(err: TypeError) -> bool:
    """Return True when TypeError likely comes from callable signature mismatch."""
    msg = str(err)
    return any(marker in msg for marker in _SIGNATURE_MISMATCH_MARKERS)


# Map training/evolution keys to strategy kwargs commonly used in this codebase.
PARAM_ALIASES: dict[str, str] = {
    "atr_multiplier": "sl_atr_mult",
    "rr_ratio": "rr_target",
    "adx_threshold": "adx_period",
    "rsi_overbought": "rsi_ob",
    "rsi_oversold": "rsi_os",
    "sl_atr": "sl_atr_mult",
    "tp_rr": "rr_target",
    "take_profit_rr": "rr_target",
    "stop_loss_atr": "sl_atr_mult",
    
    # Training outputs (uppercase)
    "SL_ATR_MULT": "sl_atr_mult",
    "TP_ATR_MULT": "tp_atr_mult",
    "MIN_SCORE_TRADE": "confidence_min",
    "MIN_CONFIDENCE": "confidence_min",
    "RR_TARGET": "rr_target",
}


_BOOL_PARAM_KEYS = {"use_volume_filter"}
_INT_PARAM_KEYS = {
    "rsi_period",
    "adx_period",
    "ema_fast",
    "ema_slow",
    "bb_period",
    "stoch_period",
    "stoch_smooth",
}

# (min, max)
_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "sl_atr_mult": (0.5, 5.0),
    "tp_atr_mult": (1.0, 10.0),
    "rr_target": (1.0, 6.0),
    "rsi_period": (5.0, 50.0),
    "rsi_ob": (55.0, 95.0),
    "rsi_os": (5.0, 45.0),
    "adx_period": (5.0, 50.0),
    "ema_fast": (3.0, 50.0),
    "ema_slow": (10.0, 200.0),
    "bb_period": (5.0, 60.0),
    "bb_std": (1.0, 4.0),
    "stoch_period": (3.0, 50.0),
    "stoch_smooth": (1.0, 20.0),
    "confidence_min": (0.3, 0.95),
    "min_rvol": (0.5, 3.0),
}

_ALLOWED_PARAM_KEYS = set(_PARAM_BOUNDS.keys()) | _BOOL_PARAM_KEYS


def _coerce_scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        raw = value.strip()
        low = raw.lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
        try:
            return float(raw)
        except ValueError:
            return raw

    return value


def _clamp_param(key: str, value: Any) -> Any:
    if key in _BOOL_PARAM_KEYS:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "on"}
        return False

    if key not in _PARAM_BOUNDS:
        return value

    if not isinstance(value, (int, float)):
        return value

    min_v, max_v = _PARAM_BOUNDS[key]
    clamped = max(min_v, min(max_v, float(value)))

    if key in _INT_PARAM_KEYS:
        return int(round(clamped))
    return round(clamped, 4)


def normalize_strategy_params(raw_params: Mapping[str, Any] | None) -> dict[str, Any]:
    """
    Normalize params from memory/training to safe strategy kwargs.

    - applies alias mapping
    - coerces bool/float strings
    - clamps values into safe ranges
    - drops unknown keys
    """
    if not raw_params:
        return {}

    normalized: dict[str, Any] = {}
    for raw_key, raw_val in raw_params.items():
        # First check explicit aliases (like SL_ATR_MULT), then fallback to lowercase raw_key
        key = PARAM_ALIASES.get(raw_key, PARAM_ALIASES.get(raw_key.lower(), raw_key.lower()))
        
        if key not in _ALLOWED_PARAM_KEYS:
            continue

        coerced = _coerce_scalar(raw_val)
        normalized[key] = _clamp_param(key, coerced)

    return normalized


def analyze_with_fallback(
    strategy: Any,
    candles,
    profile,
    regime: RegimeType = RegimeType.UNKNOWN,
    extra_kwargs: Mapping[str, Any] | None = None,
):
    """
    Invoke strategy.analyze() with compatibility fallbacks.

    Strategies in this codebase have mixed signatures. This helper tries a
    small set of common call forms and only retries when TypeError indicates a
    signature mismatch.
    """
    kwargs = dict(extra_kwargs or {})
    last_type_error: TypeError | None = None

    attempts = [
        lambda: strategy.analyze(candles=candles, profile=profile, regime=regime, **kwargs),
        lambda: strategy.analyze(candles=candles, profile=profile, **kwargs),
        lambda: strategy.analyze(candles, profile, regime, **kwargs),
        lambda: strategy.analyze(candles, profile, **kwargs),
        lambda: strategy.analyze(candles, profile, regime),
        lambda: strategy.analyze(candles, profile),
        lambda: strategy.analyze(candles),
    ]

    for attempt in attempts:
        try:
            return attempt()
        except TypeError as err:
            if not is_signature_mismatch(err):
                raise
            last_type_error = err

    if last_type_error is not None:
        raise last_type_error
    raise RuntimeError("Unable to invoke strategy.analyze()")
