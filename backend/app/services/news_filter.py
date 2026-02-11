"""
News Filter — ตรวจสอบช่วงข่าวสำคัญ.

กฎ:
    - บล็อกเทรด 30 นาทีก่อนและหลังข่าว high-impact
    - ข่าวดึงจาก API ภายนอก (pluggable)
    - ถ้าดึงข่าวไม่ได้ → ถือว่าไม่ปลอดภัย (conservative)

TODO:
    - Implement ดึงข่าวจาก ForexFactory / Investing.com
    - Cache ข่าวใน SQLite (ไม่ต้องดึงซ้ำ)
"""

from datetime import datetime, timezone, timedelta

from app.core.logging import get_logger

logger = get_logger(__name__)

# ระยะเวลาบล็อกรอบข่าว (นาที)
DEFAULT_BLOCK_MINUTES = 30


class NewsFilter:
    """
    ตรวจสอบว่าปลอดภัยจากข่าวหรือไม่.
    
    Pluggable — เปลี่ยน data source ได้ง่าย.
    """

    def __init__(self, block_minutes: int = DEFAULT_BLOCK_MINUTES) -> None:
        self.block_minutes = block_minutes
        # TODO: cache ข่าวจาก external API
        self._news_events: list[dict] = []

    def is_safe(self, symbol: str, now: datetime | None = None) -> bool:
        """
        ตรวจว่าปลอดภัยจากข่าวหรือไม่.
        
        Args:
            symbol: สัญลักษณ์ (เช่น XAUUSD → ตรวจข่าว USD)
            now: เวลาปัจจุบัน (ถ้าไม่ระบุ จะใช้ UTC now)
        
        Returns:
            True = ปลอดภัย ไม่มีข่าวใกล้
            False = อยู่ในช่วงข่าว → ห้ามเทรด
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # ตรวจสอบว่ามีข่าว high-impact ใน ±block_minutes
        window = timedelta(minutes=self.block_minutes)

        for event in self._news_events:
            event_time = event.get("time")
            if event_time and abs((now - event_time).total_seconds()) < window.total_seconds():
                logger.warning("news_block", extra={
                    "symbol": symbol,
                    "event": event.get("title", "Unknown"),
                    "event_time": event_time.isoformat(),
                })
                return False

        return True

    async def refresh(self) -> None:
        """ดึงข่าวใหม่จาก external API."""
        # TODO: implement data fetching
        logger.info("news_refresh")
