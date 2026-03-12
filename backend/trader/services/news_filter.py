"""
News Filter and event context for high-impact releases.

Existing behaviour remains available through `is_safe(symbol)`, while
strategy-aware integrations can request a richer event window context.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from backend.trader.config.paths import PROJECT_ROOT, SETTINGS_PATH
from backend.trader.data.mapper import mapper


logger = logging.getLogger("opus_logger")

DEFAULT_BLOCK_MINUTES = 30
REFRESH_INTERVAL_HOURS = 6


def _normalize_symbol(symbol: str) -> str:
    raw = str(mapper.to_standard(str(symbol or "").strip()) or symbol or "").upper()
    if raw.endswith(("M", "C")):
        raw = raw[:-1]
    return raw


def _currencies_for_symbol(symbol: str) -> List[str]:
    standard_symbol = _normalize_symbol(symbol)
    if len(standard_symbol) >= 6 and standard_symbol[:3].isalpha() and standard_symbol[3:6].isalpha():
        base = standard_symbol[:3]
        quote = standard_symbol[3:6]
        if base in {"XAU", "XAG", "BTC", "ETH"}:
            return [quote]
        return [base, quote]

    if any(token in standard_symbol for token in ("XAU", "XAG", "BTC", "ETH", "USOIL", "UKOIL", "US30", "USTEC", "NAS")):
        return ["USD"]
    return ["USD"]


def _coerce_timestamp(value) -> Optional[datetime]:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            ts = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            text = str(value).strip()
            if not text:
                return None
            ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
    except Exception:
        return None
    return ts


def _resolve_config_path(raw_path: str) -> Path:
    candidate = Path(str(raw_path or "").strip())
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


class NewsFilter:
    def __init__(self, block_minutes: int = DEFAULT_BLOCK_MINUTES):
        self.block_minutes = int(block_minutes or DEFAULT_BLOCK_MINUTES)
        self._news_events: List[Dict] = []
        self._last_refresh: Optional[datetime] = None

    def is_safe(self, symbol: str, now: Optional[datetime] = None) -> bool:
        context = self.get_event_context(symbol, now=now)
        return not bool(context.get("general_block_active", False))

    def set_events(self, events: List[Dict]) -> int:
        normalized: List[Dict] = []
        for event in events or []:
            parsed = self._normalize_event(event)
            if parsed is not None:
                normalized.append(parsed)
        normalized.sort(key=lambda row: row["time"])
        self._news_events = normalized
        self._last_refresh = datetime.now(timezone.utc)
        return len(normalized)

    def clear_events(self) -> None:
        self._news_events = []
        self._last_refresh = None

    def load_events_from_json(self, path: str) -> int:
        candidate = _resolve_config_path(path)
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("events", [])
        if not isinstance(payload, list):
            raise ValueError("news event file must contain a list or {\"events\": [...]}")
        loaded = self.set_events(payload)
        logger.info(f"📰 Loaded {loaded} news events from {candidate}")
        return loaded

    def relevant_events(self, symbol: str, impact_levels: Optional[List[str]] = None) -> List[Dict]:
        currencies = set(_currencies_for_symbol(symbol))
        levels = {str(level).upper() for level in (impact_levels or ["HIGH"])}
        return [
            event
            for event in self._news_events
            if str(event.get("impact", "")).upper() in levels
            and str(event.get("currency", "")).upper() in currencies
        ]

    def get_active_event(
        self,
        symbol: str,
        *,
        now: Optional[datetime] = None,
        minutes_before: int | None = None,
        minutes_after: int | None = None,
        impact_levels: Optional[List[str]] = None,
    ) -> Optional[Dict]:
        now_utc = _coerce_timestamp(now) or datetime.now(timezone.utc)
        before = int(self.block_minutes if minutes_before is None else minutes_before)
        after = int(self.block_minutes if minutes_after is None else minutes_after)
        relevant = self.relevant_events(symbol, impact_levels=impact_levels)
        for event in relevant:
            delta = (now_utc - event["time"]).total_seconds() / 60.0
            if (-before) <= delta <= after:
                return dict(event)
        return None

    def get_event_context(
        self,
        symbol: str,
        *,
        now: Optional[datetime] = None,
        general_block_minutes_before: int | None = None,
        general_block_minutes_after: int | None = None,
        trade_minutes_before: int = 0,
        trade_minutes_after: int = 20,
        impact_levels: Optional[List[str]] = None,
    ) -> Dict:
        now_utc = _coerce_timestamp(now) or datetime.now(timezone.utc)
        general_before = int(self.block_minutes if general_block_minutes_before is None else general_block_minutes_before)
        general_after = int(self.block_minutes if general_block_minutes_after is None else general_block_minutes_after)

        active_general = self.get_active_event(
            symbol,
            now=now_utc,
            minutes_before=general_before,
            minutes_after=general_after,
            impact_levels=impact_levels,
        )
        active_trade = self.get_active_event(
            symbol,
            now=now_utc,
            minutes_before=int(trade_minutes_before or 0),
            minutes_after=int(trade_minutes_after or 20),
            impact_levels=impact_levels,
        )
        active_event = active_trade or active_general

        minutes_to_event = None
        minutes_since_event = None
        if active_event is not None:
            minutes_to_event = round((active_event["time"] - now_utc).total_seconds() / 60.0, 2)
            minutes_since_event = round((now_utc - active_event["time"]).total_seconds() / 60.0, 2)

        return {
            "symbol": _normalize_symbol(symbol),
            "currencies": _currencies_for_symbol(symbol),
            "active_event": dict(active_event) if active_event is not None else None,
            "general_block_active": active_general is not None,
            "trade_window_active": active_trade is not None,
            "minutes_to_event": minutes_to_event,
            "minutes_since_event": minutes_since_event,
        }

    def _should_refresh(self, now: datetime) -> bool:
        if self._last_refresh is None:
            return True
        return (now - self._last_refresh).total_seconds() > (REFRESH_INTERVAL_HOURS * 3600)

    def _normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        if not isinstance(raw_event, dict):
            return None
        event_time = _coerce_timestamp(
            raw_event.get("time")
            or raw_event.get("date")
            or raw_event.get("datetime")
        )
        if event_time is None:
            return None

        currency = (
            raw_event.get("currency")
            or raw_event.get("country")
            or raw_event.get("ccy")
            or ""
        )
        currency = str(currency).upper().strip()
        impact = str(raw_event.get("impact", raw_event.get("importance", "")) or "").upper().strip()
        if not currency or not impact:
            return None

        return {
            "title": str(raw_event.get("title", raw_event.get("event", "Unknown")) or "Unknown"),
            "currency": currency,
            "time": event_time,
            "impact": impact,
        }

    async def refresh_async(self):
        try:
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    logger.warning(f"📰 News refresh failed: HTTP {response.status_code}")
                    return

                raw_events = response.json()
                if not isinstance(raw_events, list):
                    logger.warning("📰 News refresh failed: unexpected payload")
                    return

                loaded = self.set_events(raw_events)
                logger.info(f"📰 News Filter refreshed: {loaded} events loaded.")
        except Exception as exc:
            logger.error(f"📰 News refresh error: {exc}")

    def load_configured_backtest_events(self) -> int:
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return 0

        strategy_cfg = ((payload or {}).get("strategy", {}) or {}).get("news_session_momentum", {}) or {}
        backtest_cfg = strategy_cfg.get("backtest", {}) or {}
        raw_path = str(backtest_cfg.get("news_events_json", "") or "").strip()
        if not raw_path:
            return 0
        candidate = _resolve_config_path(raw_path)
        if not candidate.exists():
            return 0
        return self.load_events_from_json(str(candidate))


news_filter = NewsFilter()
