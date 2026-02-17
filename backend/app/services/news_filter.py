"""
News Filter — ตรวจสอบช่วงข่าวสำคัญ.

กฎ:
    - บล็อกเทรด 30 นาทีก่อนและหลังข่าว high-impact
    - ดึงข่าวจาก ForexFactory calendar (free API)
    - Cache in-memory (refresh ทุก 6 ชม.)
    - ถ้าดึงข่าวไม่ได้ → ถือว่าปลอดภัย (allow trading)
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_BLOCK_MINUTES = 30
REFRESH_INTERVAL_HOURS = 6

# Currencies affected by each symbol
SYMBOL_CURRENCIES = {
    "XAUUSD": ["USD"],
    "XAUUSDm": ["USD"],
    "EURUSD": ["EUR", "USD"],
    "EURUSDm": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "GBPUSDm": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"],
    "USDJPYm": ["USD", "JPY"],
    "BTCUSD": ["USD"],
    "BTCUSDm": ["USD"],
}


class NewsFilter:
    """ตรวจสอบว่าปลอดภัยจากข่าวหรือไม่."""

    def __init__(self, block_minutes: int = DEFAULT_BLOCK_MINUTES) -> None:
        self.block_minutes = block_minutes
        self._news_events: list[dict] = []
        self._last_refresh: Optional[datetime] = None

    def is_safe(self, symbol: str, now: datetime | None = None) -> bool:
        """
        ตรวจว่าปลอดภัยจากข่าวหรือไม่.

        Returns:
            True = ปลอดภัย ไม่มีข่าวใกล้
            False = อยู่ในช่วงข่าว → ห้ามเทรด
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # Auto-refresh if stale
        if self._should_refresh(now):
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Can't await in sync context — use cached data
                    pass
                else:
                    loop.run_until_complete(self.refresh())
            except RuntimeError:
                pass

        # ถ้าไม่มีข้อมูลข่าว → assume safe (allow trading)
        if not self._news_events:
            return True

        # Get affected currencies for this symbol
        currencies = SYMBOL_CURRENCIES.get(symbol, ["USD"])

        window = timedelta(minutes=self.block_minutes)

        for event in self._news_events:
            event_time = event.get("time")
            event_currency = event.get("currency", "")
            event_impact = event.get("impact", "")

            # Only block on HIGH impact news
            if event_impact != "HIGH":
                continue

            # Only block if the event's currency affects our symbol
            if event_currency not in currencies:
                continue

            if event_time and abs((now - event_time).total_seconds()) < window.total_seconds():
                logger.warning("news_block", extra={
                    "symbol": symbol,
                    "event": event.get("title", "Unknown"),
                    "event_time": event_time.isoformat(),
                    "currency": event_currency,
                })
                return False

        return True

    def _should_refresh(self, now: datetime) -> bool:
        if self._last_refresh is None:
            return True
        hours_since = (now - self._last_refresh).total_seconds() / 3600
        return hours_since >= REFRESH_INTERVAL_HOURS

    async def refresh(self) -> None:
        """ดึงข่าวใหม่จาก free forex calendar API."""
        try:
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    raw_events = r.json()
                    self._news_events = []
                    for ev in raw_events:
                        try:
                            # Parse date+time
                            date_str = ev.get("date", "")
                            if date_str:
                                event_time = datetime.fromisoformat(
                                    date_str.replace("Z", "+00:00")
                                )
                            else:
                                continue

                            impact = ev.get("impact", "").upper()
                            self._news_events.append({
                                "title": ev.get("title", "Unknown"),
                                "currency": ev.get("country", "").upper(),
                                "time": event_time,
                                "impact": impact,
                            })
                        except (ValueError, TypeError):
                            continue

                    self._last_refresh = datetime.now(timezone.utc)
                    logger.info("news_refreshed", extra={
                        "total_events": len(self._news_events),
                        "high_impact": sum(1 for e in self._news_events if e["impact"] == "HIGH"),
                    })
                else:
                    logger.warning("news_refresh_failed", extra={"status": r.status_code})
        except Exception as e:
            logger.warning("news_refresh_error", extra={"error": str(e)})
            # Don't clear existing cached events on refresh failure
