"""
News Filter — High-Impact Event Protection.
Blocks trading 30 mins before and after high-impact economic news.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict

import httpx

logger = logging.getLogger("opus_logger")

DEFAULT_BLOCK_MINUTES = 30
REFRESH_INTERVAL_HOURS = 6

# Currencies affected by each symbol in the trader engine
SYMBOL_CURRENCIES = {
    "XAUUSD": ["USD"],
    "XAGUSD": ["USD"],
    "BTCUSD": ["USD"],
}

class NewsFilter:
    def __init__(self, block_minutes: int = DEFAULT_BLOCK_MINUTES):
        self.block_minutes = block_minutes
        self._news_events: List[Dict] = []
        self._last_refresh: Optional[datetime] = None

    def is_safe(self, symbol: str, now: Optional[datetime] = None) -> bool:
        """
        Returns True if safe to trade, False if inside news window.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # Sync refresh check (triggered in main loop normally, but safe here)
        if self._should_refresh(now):
            # In a trading loop, we don't want to block on HTTP
            # The main.py should call refresh_async periodically
            pass

        if not self._news_events:
            return True

        currencies = SYMBOL_CURRENCIES.get(symbol, ["USD"])
        window = timedelta(minutes=self.block_minutes)

        for event in self._news_events:
            if event.get("impact") != "HIGH":
                continue

            if event.get("currency") not in currencies:
                continue

            event_time = event.get("time")
            if event_time and abs((now - event_time).total_seconds()) < window.total_seconds():
                logger.warning(f"⚠️ [NEWS BLOCK] {symbol} inside {event['impact']} impact news: {event['title']} ({event['currency']})")
                return False

        return True

    def _should_refresh(self, now: datetime) -> bool:
        if self._last_refresh is None:
            return True
        return (now - self._last_refresh).total_seconds() > (REFRESH_INTERVAL_HOURS * 3600)

    async def refresh_async(self):
        """Async fetch from ForexFactory calendar."""
        try:
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    raw_events = r.json()
                    new_events = []
                    for ev in raw_events:
                        try:
                            date_str = ev.get("date", "")
                            if not date_str: continue
                            
                            event_time = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                            new_events.append({
                                "title": ev.get("title", "Unknown"),
                                "currency": ev.get("country", "").upper(),
                                "time": event_time,
                                "impact": ev.get("impact", "").upper(),
                            })
                        except Exception:
                            continue
                    
                    self._news_events = new_events
                    self._last_refresh = datetime.now(timezone.utc)
                    logger.info(f"📰 News Filter refreshed: {len(self._news_events)} events loaded.")
                else:
                    logger.warning(f"📰 News refresh failed: HTTP {r.status_code}")
        except Exception as e:
            logger.error(f"📰 News refresh error: {e}")

# Singleton for engine-wide use
news_filter = NewsFilter()
