from __future__ import annotations

import logging
from datetime import timezone

import pandas as pd

from backend.trader.services.news_filter import news_filter

from .news_session_config import (
    execution_guard_for,
    resolve_news_session_config,
    risk_profile_for,
    strategy_allows_symbol_timeframe,
)
from .news_session_filters import (
    build_pre_news_range,
    build_trade_levels,
    breakout_confirmation,
    retest_confirmation,
    safe_float,
    to_utc_timestamp,
    volume_spike_metrics,
)


logger = logging.getLogger("opus_logger")


def _serialize_event(event: dict | None) -> dict:
    if not isinstance(event, dict):
        return {}
    payload = dict(event)
    event_time = payload.get("time")
    if event_time is not None:
        ts = to_utc_timestamp(event_time)
        payload["time"] = ts.isoformat() if ts is not None else str(event_time)
    return payload


def _resolve_now(context: dict, df: pd.DataFrame):
    if isinstance(context, dict) and context.get("current_time") is not None:
        ts = to_utc_timestamp(context.get("current_time"))
        if ts is not None:
            return ts
    if df is not None and not df.empty:
        time_value = df.iloc[-1].get("time")
        ts = to_utc_timestamp(time_value)
        if ts is not None:
            return ts
    return pd.Timestamp.now(tz=timezone.utc)


def _confidence_from_setup(candidate: dict, volume_metrics: dict, range_info: dict) -> float:
    confidence = 0.72
    confidence += min(0.10, max(0.0, safe_float(volume_metrics.get("ratio"), 0.0) - 1.0) * 0.08)
    if candidate.get("confirmation") == "retest":
        confidence += 0.04
    range_width = safe_float(range_info.get("width"), 0.0)
    if range_width > 0:
        confidence += min(0.04, range_width * 0.02)
    return float(min(0.93, max(0.0, confidence)))


def signal_news_session_momentum(df: pd.DataFrame, context: dict) -> dict | None:
    if df is None or len(df) < 20:
        return None

    symbol = str((context or {}).get("symbol", "UNKNOWN") or "UNKNOWN")
    timeframe = str((context or {}).get("timeframe", "") or "").upper()
    cfg = resolve_news_session_config(symbol, timeframe, context)
    if not strategy_allows_symbol_timeframe(symbol, timeframe, cfg):
        return None

    now_ts = _resolve_now(context or {}, df)
    news_cfg = cfg.get("news", {}) or {}
    news_ctx = (context or {}).get("news_context")
    if not isinstance(news_ctx, dict):
        news_ctx = news_filter.get_event_context(
            symbol,
            now=now_ts.to_pydatetime(),
            general_block_minutes_before=int(news_cfg.get("general_block_minutes_before", 30) or 30),
            general_block_minutes_after=int(news_cfg.get("general_block_minutes_after", 30) or 30),
            trade_minutes_before=int(news_cfg.get("trade_minutes_before", 0) or 0),
            trade_minutes_after=int(news_cfg.get("trade_minutes_after", 20) or 20),
            impact_levels=news_cfg.get("impact_levels", ["HIGH"]),
        )

    if not bool(news_ctx.get("trade_window_active", False)):
        return None

    active_event = news_ctx.get("active_event")
    if not isinstance(active_event, dict):
        return None

    range_info = build_pre_news_range(df, active_event.get("time"), cfg)
    if range_info is None:
        return None

    latest = df.iloc[-1]
    entry = safe_float(latest.get("close"), 0.0)
    atr = safe_float(latest.get("atr"), 0.0)
    if entry <= 0 or atr <= 0:
        return None

    market_state = (context or {}).get("market_state", {}) or {}
    guard = execution_guard_for(cfg)
    current_spread = safe_float(market_state.get("spread"), 0.0)
    max_spread = safe_float(guard.get("max_spread_points"), 0.0)
    if max_spread > 0 and current_spread > max_spread:
        logger.info(
            f"📰 [NEWS MOMENTUM] {symbol} skipped: spread {current_spread:.1f} > {max_spread:.1f}"
        )
        return None

    candidate = breakout_confirmation(df, range_info, cfg)
    require_retest = bool((cfg.get("price_action", {}) or {}).get("require_retest", False))
    if require_retest or candidate is None:
        retest_candidate = retest_confirmation(df, range_info, cfg)
        if require_retest:
            candidate = retest_candidate
        elif candidate is None:
            candidate = retest_candidate

    if candidate is None:
        return None

    side = str(candidate.get("side", "")).upper()
    volume_metrics = volume_spike_metrics(df, cfg, side=side)
    volume_floor = float((cfg.get("volume", {}) or {}).get("retest_volume_ratio_min", 0.95) or 0.95)
    if not volume_metrics.get("passed", False):
        if candidate.get("confirmation") != "retest" or safe_float(volume_metrics.get("ratio"), 0.0) < volume_floor:
            return None

    levels = build_trade_levels(side, entry, latest, range_info, cfg)
    if levels is None:
        return None

    confidence = _confidence_from_setup(candidate, volume_metrics, range_info)
    event_payload = _serialize_event(active_event)
    signal = {
        "symbol": symbol,
        "side": side,
        "entry_type": str((cfg.get("execution", {}) or {}).get("entry_type", "MARKET") or "MARKET").upper(),
        "entry_price": round(entry, 5),
        "sl": round(safe_float(levels["sl"]), 5),
        "tp1": round(safe_float(levels["tp1"]), 5),
        "tp2": round(safe_float(levels["tp2"]), 5),
        "tp3": round(safe_float(levels["tp3"]), 5),
        "confidence": confidence,
        "model": "NEWS_SESSION_MOMENTUM",
        "rationale": [
            f"High-impact {event_payload.get('currency', 'NEWS')} event window: {event_payload.get('title', 'Unknown event')}",
            f"Pre-news range breakout {candidate.get('confirmation')} above/below {range_info['bars']} bars",
            f"Volume spike {safe_float(volume_metrics.get('ratio'), 0.0):.2f}x >= {safe_float(volume_metrics.get('threshold'), 0.0):.2f}x",
            f"ATR-aware risk {safe_float(levels.get('risk_distance'), 0.0):.3f} with spread cap {max_spread:.1f}",
        ],
        "engine_use_signal_levels": True,
        "risk_profile": risk_profile_for(cfg),
        "execution_guard": guard,
        "news_event": event_payload,
        "news_window": {
            "trade_window_active": bool(news_ctx.get("trade_window_active", False)),
            "general_block_active": bool(news_ctx.get("general_block_active", False)),
            "minutes_to_event": news_ctx.get("minutes_to_event"),
            "minutes_since_event": news_ctx.get("minutes_since_event"),
        },
        "pre_news_range": {
            "high": round(range_info["high"], 5),
            "low": round(range_info["low"], 5),
            "width": round(range_info["width"], 5),
            "bars": int(range_info["bars"]),
        },
        "volume_metrics": {
            "ratio": round(safe_float(volume_metrics.get("ratio"), 0.0), 4),
            "threshold": round(safe_float(volume_metrics.get("threshold"), 0.0), 4),
            "directional_pressure": round(safe_float(volume_metrics.get("directional_pressure"), 0.0), 4),
        },
        "setup_type": candidate.get("confirmation", "breakout"),
    }

    logger.info(
        f"📰 [NEWS MOMENTUM] {symbol} {side} {event_payload.get('title', 'event')} "
        f"{candidate.get('confirmation', 'breakout')} | vol {safe_float(volume_metrics.get('ratio'), 0.0):.2f}x "
        f"| range {range_info['low']:.2f}-{range_info['high']:.2f}"
    )
    return signal
