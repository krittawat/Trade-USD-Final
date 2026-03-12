from __future__ import annotations

import math
from typing import Any

import pandas as pd


_TF_TO_MINUTES = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M6": 6,
    "M10": 10,
    "M12": 12,
    "M15": 15,
    "M20": 20,
    "M30": 30,
    "H1": 60,
    "H2": 120,
    "H4": 240,
}


def safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return fallback
    if math.isnan(out) or math.isinf(out):
        return fallback
    return out


def to_utc_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            ts = pd.to_datetime(value, unit="s", utc=True, errors="coerce")
        else:
            ts = pd.to_datetime(value, utc=True, errors="coerce")
    except Exception:
        ts = pd.NaT
    if pd.isna(ts):
        return None
    if getattr(ts, "tzinfo", None) is None:
        ts = ts.tz_localize("UTC")
    return ts


def ensure_time_series(df: pd.DataFrame) -> pd.Series:
    if "time" in df.columns:
        sample = df["time"]
        if pd.api.types.is_numeric_dtype(sample):
            return pd.to_datetime(sample, unit="s", utc=True, errors="coerce")
        return pd.to_datetime(sample, utc=True, errors="coerce")
    if isinstance(df.index, pd.DatetimeIndex):
        if df.index.tz is None:
            return pd.Series(df.index.tz_localize("UTC"), index=df.index)
        return pd.Series(df.index.tz_convert("UTC"), index=df.index)
    return pd.Series(pd.NaT, index=df.index)


def timeframe_minutes(timeframe: str) -> int:
    return int(_TF_TO_MINUTES.get(str(timeframe or "").upper(), 5))


def build_pre_news_range(df: pd.DataFrame, event_time, cfg: dict) -> dict | None:
    event_ts = to_utc_timestamp(event_time)
    if event_ts is None or df is None or df.empty:
        return None

    news_cfg = cfg.get("news", {}) or {}
    lookback_minutes = int(news_cfg.get("pre_news_range_minutes", 30) or 30)
    min_bars = int(news_cfg.get("min_pre_news_bars", 4) or 4)
    window_start = event_ts - pd.Timedelta(minutes=lookback_minutes)
    timestamps = ensure_time_series(df)
    mask = (timestamps >= window_start) & (timestamps < event_ts)
    window = df.loc[mask]
    if len(window) < min_bars:
        return None

    range_high = safe_float(window["high"].max())
    range_low = safe_float(window["low"].min())
    range_width = range_high - range_low
    if range_width <= 0:
        return None

    return {
        "high": range_high,
        "low": range_low,
        "width": range_width,
        "bars": int(len(window)),
        "start": window_start.to_pydatetime(),
        "end": event_ts.to_pydatetime(),
    }


def volume_spike_metrics(df: pd.DataFrame, cfg: dict, side: str = "") -> dict:
    volume_cfg = cfg.get("volume", {}) or {}
    ma_period = max(2, int(volume_cfg.get("spike_ma_period", 12) or 12))
    if df is None or len(df) < (ma_period + 1):
        return {"passed": False, "ratio": 0.0, "threshold": float(volume_cfg.get("spike_threshold", 1.6) or 1.6)}

    latest = df.iloc[-1]
    history = df.iloc[-(ma_period + 1):-1]
    if history.empty:
        return {"passed": False, "ratio": 0.0, "threshold": float(volume_cfg.get("spike_threshold", 1.6) or 1.6)}

    volume_col = "tick_volume" if "tick_volume" in df.columns else ("volume" if "volume" in df.columns else "")
    if not volume_col:
        return {"passed": False, "ratio": 0.0, "threshold": float(volume_cfg.get("spike_threshold", 1.6) or 1.6)}

    current_volume = safe_float(latest.get(volume_col), 0.0)
    baseline = safe_float(history[volume_col].mean(), 0.0)
    ratio = current_volume / baseline if baseline > 0 else 0.0
    directional_pressure = safe_float(
        latest.get("buy_pressure" if side == "BUY" else "sell_pressure", 0.5),
        0.5,
    )
    threshold = float(volume_cfg.get("spike_threshold", 1.6) or 1.6)
    pressure_floor = float(volume_cfg.get("directional_pressure_min", 0.58) or 0.58)
    passed = ratio >= threshold and directional_pressure >= pressure_floor
    return {
        "passed": passed,
        "ratio": ratio,
        "threshold": threshold,
        "baseline": baseline,
        "current_volume": current_volume,
        "directional_pressure": directional_pressure,
    }


def passes_wick_trap_filter(bar: pd.Series, side: str, cfg: dict) -> bool:
    max_wick_ratio = float((cfg.get("price_action", {}) or {}).get("max_breakout_wick_ratio", 0.35) or 0.35)
    if side == "BUY":
        return safe_float(bar.get("upper_wick_ratio"), 0.0) <= max_wick_ratio
    if side == "SELL":
        return safe_float(bar.get("lower_wick_ratio"), 0.0) <= max_wick_ratio
    return False


def breakout_confirmation(df: pd.DataFrame, range_info: dict, cfg: dict) -> dict | None:
    latest = df.iloc[-1]
    atr = max(safe_float(latest.get("atr"), 0.0), 1e-9)
    price_cfg = cfg.get("price_action", {}) or {}
    body_ratio_floor = float(price_cfg.get("breakout_body_ratio_min", 0.45) or 0.45)
    close_buffer = atr * float(price_cfg.get("breakout_close_buffer_atr", 0.08) or 0.08)
    max_breakout_distance = atr * float(price_cfg.get("max_breakout_distance_atr", 0.70) or 0.70)

    close = safe_float(latest.get("close"), 0.0)
    open_price = safe_float(latest.get("open"), close)
    body_ratio = safe_float(latest.get("body_ratio"), 0.0)
    buy_pressure = safe_float(latest.get("buy_pressure"), 0.5)
    sell_pressure = safe_float(latest.get("sell_pressure"), 0.5)
    pressure_floor = float((cfg.get("volume", {}) or {}).get("directional_pressure_min", 0.58) or 0.58)

    if (
        close > (range_info["high"] + close_buffer)
        and close > open_price
        and body_ratio >= body_ratio_floor
        and buy_pressure >= pressure_floor
    ):
        breakout_distance = close - range_info["high"]
        if breakout_distance <= max_breakout_distance and passes_wick_trap_filter(latest, "BUY", cfg):
            return {
                "side": "BUY",
                "confirmation": "breakout",
                "breakout_distance": breakout_distance,
            }

    if (
        close < (range_info["low"] - close_buffer)
        and close < open_price
        and body_ratio >= body_ratio_floor
        and sell_pressure >= pressure_floor
    ):
        breakout_distance = range_info["low"] - close
        if breakout_distance <= max_breakout_distance and passes_wick_trap_filter(latest, "SELL", cfg):
            return {
                "side": "SELL",
                "confirmation": "breakout",
                "breakout_distance": breakout_distance,
            }

    return None


def retest_confirmation(df: pd.DataFrame, range_info: dict, cfg: dict) -> dict | None:
    if len(df) < 2:
        return None

    latest = df.iloc[-1]
    atr = max(safe_float(latest.get("atr"), 0.0), 1e-9)
    price_cfg = cfg.get("price_action", {}) or {}
    tolerance = atr * float(price_cfg.get("retest_tolerance_atr", 0.18) or 0.18)
    close_buffer = atr * float(price_cfg.get("breakout_close_buffer_atr", 0.08) or 0.08)
    body_ratio_floor = float(price_cfg.get("breakout_body_ratio_min", 0.45) or 0.45)
    max_bars = max(1, int(price_cfg.get("retest_max_bars", 2) or 2))
    breakout_history = df.tail(max_bars + 1).iloc[:-1]

    for _, previous in breakout_history.iloc[::-1].iterrows():
        if (
            safe_float(previous.get("close"), 0.0) > (range_info["high"] + close_buffer)
            and safe_float(latest.get("low"), 0.0) <= (range_info["high"] + tolerance)
            and safe_float(latest.get("close"), 0.0) > (range_info["high"] + close_buffer)
            and safe_float(latest.get("close"), 0.0) > safe_float(latest.get("open"), 0.0)
            and safe_float(latest.get("body_ratio"), 0.0) >= body_ratio_floor
            and passes_wick_trap_filter(latest, "BUY", cfg)
        ):
            return {
                "side": "BUY",
                "confirmation": "retest",
                "breakout_distance": safe_float(latest.get("close"), 0.0) - range_info["high"],
            }

        if (
            safe_float(previous.get("close"), 0.0) < (range_info["low"] - close_buffer)
            and safe_float(latest.get("high"), 0.0) >= (range_info["low"] - tolerance)
            and safe_float(latest.get("close"), 0.0) < (range_info["low"] - close_buffer)
            and safe_float(latest.get("close"), 0.0) < safe_float(latest.get("open"), 0.0)
            and safe_float(latest.get("body_ratio"), 0.0) >= body_ratio_floor
            and passes_wick_trap_filter(latest, "SELL", cfg)
        ):
            return {
                "side": "SELL",
                "confirmation": "retest",
                "breakout_distance": range_info["low"] - safe_float(latest.get("close"), 0.0),
            }

    return None


def build_trade_levels(side: str, entry: float, latest: pd.Series, range_info: dict, cfg: dict) -> dict | None:
    atr = safe_float(latest.get("atr"), 0.0)
    if entry <= 0 or atr <= 0:
        return None

    risk_cfg = cfg.get("risk", {}) or {}
    atr_stop = atr * float(risk_cfg.get("atr_stop_multiplier", 1.25) or 1.25)
    stop_buffer = atr * float(risk_cfg.get("stop_buffer_atr", 0.20) or 0.20)
    min_risk = atr * float(risk_cfg.get("min_stop_atr", 0.90) or 0.90)
    max_risk = atr * float(risk_cfg.get("max_stop_atr", 2.20) or 2.20)

    if side == "BUY":
        structural_stop = min(range_info["low"], safe_float(latest.get("low"), entry)) - stop_buffer
        raw_risk = entry - structural_stop
    else:
        structural_stop = max(range_info["high"], safe_float(latest.get("high"), entry)) + stop_buffer
        raw_risk = structural_stop - entry

    risk_distance = max(raw_risk, atr_stop, min_risk)
    if max_risk > 0:
        risk_distance = min(risk_distance, max_risk)
    if risk_distance <= 0:
        return None

    tp1_rr = float(risk_cfg.get("tp1_rr", 1.60) or 1.60)
    tp2_rr = float(risk_cfg.get("tp2_rr", 2.20) or 2.20)
    tp3_rr = float(risk_cfg.get("tp3_rr", 3.00) or 3.00)

    if side == "BUY":
        stop_loss = entry - risk_distance
        return {
            "sl": stop_loss,
            "tp1": entry + (risk_distance * tp1_rr),
            "tp2": entry + (risk_distance * tp2_rr),
            "tp3": entry + (risk_distance * tp3_rr),
            "risk_distance": risk_distance,
        }

    stop_loss = entry + risk_distance
    return {
        "sl": stop_loss,
        "tp1": entry - (risk_distance * tp1_rr),
        "tp2": entry - (risk_distance * tp2_rr),
        "tp3": entry - (risk_distance * tp3_rr),
        "risk_distance": risk_distance,
    }
