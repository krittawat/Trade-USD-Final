"""
Runtime Metrics — ตัวเลข runtime ของระบบ.

เก็บ: cycle count, uptime, orders sent, errors count, etc.
ใช้แสดงบน dashboard health panel.
"""

from datetime import datetime, timezone

from app.core.logging import get_logger

logger = get_logger(__name__)


class RuntimeMetrics:
    """เก็บ metrics runtime — ไม่ persist, รีเซ็ตเมื่อ restart."""

    def __init__(self) -> None:
        self.start_time = datetime.now(timezone.utc)
        self.total_cycles = 0
        self.total_signals = 0
        self.total_blocked = 0
        self.total_orders = 0
        self.total_errors = 0

    def increment(self, metric: str, count: int = 1) -> None:
        """เพิ่มค่า metric."""
        current = getattr(self, metric, 0)
        setattr(self, metric, current + count)

    def get_summary(self) -> dict:
        """ดึง metrics ทั้งหมดเป็น dict."""
        uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        return {
            "uptime_seconds": round(uptime),
            "total_cycles": self.total_cycles,
            "total_signals": self.total_signals,
            "total_blocked": self.total_blocked,
            "total_orders": self.total_orders,
            "total_errors": self.total_errors,
        }
