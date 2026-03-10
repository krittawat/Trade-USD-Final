"""
NewsCollector — ดึงข่าวเศรษฐกิจสำหรับ Self-Training.

หน้าที่:
    1. ดึงข่าว ForexFactory calendar (reuse API จาก news_filter.py)
    2. เก็บ event data ลง SQLite (ผ่าน MemoryStore)
    3. Tag เทรดว่าอยู่ใกล้ข่าวหรือไม่ (NewsContext)
    4. คำนวณสถิติ: win_rate ต่อ news impact / currency / symbol

RAM Safety:
    - Cache ≤ 200 events ใน memory
    - Refresh ทุก 6 ชม.
    - เฉพาะ export dict สำหรับ training — ไม่สะสม raw data

การทำงานกับ Self-Training:
    - PracticeEngine เรียก get_news_context(symbol, bar_time)
    - ได้ NewsContext: impact level + event title
    - PracticeEngine tag trade result → MemoryStore.record_news_outcome()
"""

from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Config ──────────────────────────────────────────────────────────
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
REFRESH_INTERVAL_HOURS = 6
MAX_CACHED_EVENTS = 200
NEWS_WINDOW_MINUTES = 30  # ±30 นาที = ถือว่า "ใกล้ข่าว"

# Rate-limit backoff config
BACKOFF_BASE_SECONDS = 900        # 15 min initial cooldown after 429
BACKOFF_MAX_SECONDS = 6 * 3600    # 6 hours max cooldown

# Currencies affected by each symbol
SYMBOL_CURRENCIES = {
    "XAUUSD": ["USD"], "XAUUSDc": ["USD"],
    "EURUSD": ["EUR", "USD"], "EURUSDc": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"], "GBPUSDc": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"], "USDJPYc": ["USD", "JPY"],
    "BTCUSD": ["USD"], "BTCUSDc": ["USD"],
}


@dataclass
class NewsEvent:
    """ข่าวเศรษฐกิจ 1 เหตุการณ์."""
    title: str
    currency: str
    impact: str           # "HIGH" | "MEDIUM" | "LOW"
    time: datetime
    actual: str = ""
    forecast: str = ""
    previous: str = ""


@dataclass
class NewsContext:
    """บริบทข่าวสำหรับ 1 จุดเวลา — ใช้ tag เทรด."""
    near_news: bool = False           # อยู่ใกล้ข่าว (±30 min)
    impact: str = "NONE"              # HIGH, MEDIUM, LOW, NONE
    event_title: str = ""             # ชื่อข่าว
    event_currency: str = ""          # สกุลเงินของข่าว
    minutes_to_event: float = 999.0   # นาทีจนถึงข่าว (ค่าลบ = ข่าวผ่านไปแล้ว)

    def to_dict(self) -> dict:
        return {
            "near_news": self.near_news,
            "impact": self.impact,
            "event_title": self.event_title,
            "event_currency": self.event_currency,
            "minutes_to_event": round(self.minutes_to_event, 1),
        }


class NewsCollector:
    """
    ดึงและจัดการข่าวเศรษฐกิจสำหรับ self-training.

    วิธีใช้:
        collector = NewsCollector()
        await collector.refresh()  # ดึงข่าวใหม่

        # ใน PracticeEngine:
        ctx = collector.get_news_context("XAUUSDc", bar_time)
        if ctx.near_news:
            tag_trade("near_" + ctx.impact)
    """

    def __init__(self) -> None:
        self._events: list[NewsEvent] = []
        self._last_refresh: Optional[datetime] = None
        self._refresh_lock = False
        # Rate-limit backoff state
        self._rate_limit_until: Optional[datetime] = None
        self._consecutive_429s: int = 0

    # ────────────────────────────────────────────────────────────────
    # Refresh — ดึงข่าวใหม่จาก ForexFactory
    # ────────────────────────────────────────────────────────────────

    async def refresh(self) -> int:
        """
        ดึงข่าวใหม่จาก ForexFactory.

        Returns:
            จำนวน events ที่ดึงได้สำเร็จ
        """
        if self._refresh_lock:
            return len(self._events)

        # ── Rate-limit cooldown check ──
        now_utc = datetime.now(timezone.utc)
        if self._rate_limit_until and now_utc < self._rate_limit_until:
            remaining = (self._rate_limit_until - now_utc).total_seconds()
            logger.debug("news_collector_rate_limited", extra={
                "retry_after_sec": round(remaining),
                "cached_events": len(self._events),
            })
            return len(self._events)

        self._refresh_lock = True
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(FF_CALENDAR_URL)

            if r.status_code == 429:
                # Exponential backoff: 15m → 30m → 60m → ... → 6h max
                self._consecutive_429s += 1
                backoff = min(
                    BACKOFF_BASE_SECONDS * (2 ** (self._consecutive_429s - 1)),
                    BACKOFF_MAX_SECONDS,
                )
                self._rate_limit_until = now_utc + timedelta(seconds=backoff)
                logger.warning("news_collector_rate_limited_429", extra={
                    "consecutive_429s": self._consecutive_429s,
                    "backoff_seconds": backoff,
                    "retry_at": self._rate_limit_until.isoformat(),
                    "cached_events": len(self._events),
                })
                return len(self._events)

            if r.status_code != 200:
                logger.warning("news_collector_fetch_failed", extra={
                    "status": r.status_code,
                })
                return len(self._events)

            # ── Success — reset backoff ──
            self._consecutive_429s = 0
            self._rate_limit_until = None

            raw = r.json()
            parsed: list[NewsEvent] = []

            for ev in raw:
                try:
                    date_str = ev.get("date", "")
                    if not date_str:
                        continue

                    event_time = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    )

                    impact = ev.get("impact", "").upper()
                    if impact not in ("HIGH", "MEDIUM", "LOW"):
                        impact = "LOW"

                    parsed.append(NewsEvent(
                        title=ev.get("title", "Unknown"),
                        currency=ev.get("country", "").upper(),
                        impact=impact,
                        time=event_time,
                        actual=str(ev.get("actual", "")),
                        forecast=str(ev.get("forecast", "")),
                        previous=str(ev.get("previous", "")),
                    ))
                except (ValueError, TypeError, KeyError):
                    continue

            # เก็บเฉพาะ MAX_CACHED_EVENTS ล่าสุด (sorted by time)
            parsed.sort(key=lambda e: e.time, reverse=True)
            self._events = parsed[:MAX_CACHED_EVENTS]
            self._last_refresh = now_utc

            logger.info("news_collector_refreshed", extra={
                "total_events": len(self._events),
                "high_impact": sum(1 for e in self._events if e.impact == "HIGH"),
                "medium_impact": sum(1 for e in self._events if e.impact == "MEDIUM"),
            })
            return len(self._events)

        except Exception as e:
            logger.warning("news_collector_error", extra={"error": str(e)})
            return len(self._events)
        finally:
            self._refresh_lock = False

    async def ensure_fresh(self) -> None:
        """Auto-refresh ถ้าข้อมูลเก่าเกิน REFRESH_INTERVAL_HOURS."""
        # Skip if currently rate-limited
        if self._rate_limit_until and datetime.now(timezone.utc) < self._rate_limit_until:
            return

        if self._last_refresh is None:
            await self.refresh()
            return

        elapsed = (datetime.now(timezone.utc) - self._last_refresh).total_seconds()
        if elapsed > REFRESH_INTERVAL_HOURS * 3600:
            await self.refresh()

    # ────────────────────────────────────────────────────────────────
    # Get News Context — สำหรับ PracticeEngine
    # ────────────────────────────────────────────────────────────────

    def get_news_context(
        self,
        symbol: str,
        bar_time: datetime,
        window_minutes: int = NEWS_WINDOW_MINUTES,
    ) -> NewsContext:
        """
        ดูว่า bar_time อยู่ใกล้ข่าวสำคัญหรือไม่.

        Args:
            symbol: สัญลักษณ์ที่เทรด
            bar_time: เวลาของ bar
            window_minutes: ±N นาทีถือว่าใกล้ข่าว

        Returns:
            NewsContext — ใช้ tag เทรด
        """
        if not self._events:
            return NewsContext()

        currencies = SYMBOL_CURRENCIES.get(symbol, ["USD"])
        window = timedelta(minutes=window_minutes)
        best_ctx = NewsContext()
        best_distance = float("inf")

        for ev in self._events:
            # กรองเฉพาะสกุลเงินที่เกี่ยว
            if ev.currency not in currencies:
                continue

            delta = (bar_time - ev.time).total_seconds() / 60.0
            abs_delta = abs(delta)

            if abs_delta < window_minutes and abs_delta < best_distance:
                best_distance = abs_delta

                # Priority: HIGH > MEDIUM > LOW
                impact_priority = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
                if impact_priority.get(ev.impact, 0) >= impact_priority.get(best_ctx.impact, 0):
                    best_ctx = NewsContext(
                        near_news=True,
                        impact=ev.impact,
                        event_title=ev.title,
                        event_currency=ev.currency,
                        minutes_to_event=delta,
                    )

        return best_ctx

    def get_news_batch(
        self,
        symbol: str,
        bar_times: list[datetime],
        window_minutes: int = NEWS_WINDOW_MINUTES,
    ) -> dict[int, NewsContext]:
        """
        Batch news lookup — O(n+m) two-pointer สำหรับ PracticeEngine.

        แทนเรียก get_news_context() ทีละ bar (O(n×m)),
        scan events+bars พร้อมกันครั้งเดียว.

        Args:
            symbol: สัญลักษณ์ที่เทรด
            bar_times: list of bar timestamps (ต้อง sorted)
            window_minutes: ±N นาทีถือว่าใกล้ข่าว

        Returns:
            dict[int, NewsContext]: bar_index → NewsContext
        """
        if not self._events or not bar_times:
            return {}

        currencies = set(SYMBOL_CURRENCIES.get(symbol, ["USD"]))
        impact_priority = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}

        # Pre-filter events by currency and sort by time ascending
        relevant = sorted(
            [e for e in self._events if e.currency in currencies],
            key=lambda e: e.time,
        )
        if not relevant:
            return {}

        result: dict[int, NewsContext] = {}
        n_events = len(relevant)

        for bar_idx, bar_time in enumerate(bar_times):
            best = NewsContext()
            best_dist = float("inf")

            # Binary search for closest events (events are sorted)
            lo, hi = 0, n_events - 1
            while lo <= hi:
                mid = (lo + hi) // 2
                if relevant[mid].time < bar_time:
                    lo = mid + 1
                else:
                    hi = mid - 1

            # Check neighbors around the insertion point
            for j in range(max(0, lo - 3), min(n_events, lo + 3)):
                ev = relevant[j]
                delta = (bar_time - ev.time).total_seconds() / 60.0
                abs_delta = abs(delta)
                if abs_delta < window_minutes and abs_delta < best_dist:
                    if impact_priority.get(ev.impact, 0) >= impact_priority.get(best.impact, 0):
                        best_dist = abs_delta
                        best = NewsContext(
                            near_news=True,
                            impact=ev.impact,
                            event_title=ev.title,
                            event_currency=ev.currency,
                            minutes_to_event=delta,
                        )

            if best.near_news:
                result[bar_idx] = best

        return result

    def get_news_for_range(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[NewsEvent]:
        """
        ดึงข่าวทั้งหมดในช่วงเวลาที่กำหนด.

        ใช้ใน training: ดูว่าช่วงที่ backtest มีข่าวอะไรบ้าง.
        """
        if not self._events:
            return []

        currencies = SYMBOL_CURRENCIES.get(symbol, ["USD"])

        return [
            ev for ev in self._events
            if ev.currency in currencies
            and start_time <= ev.time <= end_time
        ]

    # ────────────────────────────────────────────────────────────────
    # Stats — สรุปข่าวสำหรับ Dashboard
    # ────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """สรุปข้อมูลข่าวปัจจุบัน."""
        if not self._events:
            return {
                "total_events": 0,
                "last_refresh": None,
                "impact_breakdown": {},
            }

        impact_breakdown = {}
        for ev in self._events:
            impact_breakdown[ev.impact] = impact_breakdown.get(ev.impact, 0) + 1

        # Upcoming HIGH impact events (next 24h)
        now = datetime.now(timezone.utc)
        upcoming = [
            {
                "title": ev.title,
                "currency": ev.currency,
                "time": ev.time.isoformat(),
                "minutes_away": round((ev.time - now).total_seconds() / 60, 0),
            }
            for ev in self._events
            if ev.impact == "HIGH"
            and 0 < (ev.time - now).total_seconds() < 86400  # next 24h
        ]
        upcoming.sort(key=lambda x: x["minutes_away"])

        return {
            "total_events": len(self._events),
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "impact_breakdown": impact_breakdown,
            "upcoming_high_impact": upcoming[:10],  # top 10 upcoming
        }

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def has_data(self) -> bool:
        return len(self._events) > 0
