"""
Feature Store — เก็บ aggregated features (ไม่เก็บ raw tick).

หน้าที่:
    - สรุปค่า indicator (mean/std/min/max) ต่อ regime
    - ใช้ DuckDB สำหรับคำนวณ aggregation
    - เก็บผลลัพธ์ใน SQLite (compact)
"""

from app.core.logging import get_logger

logger = get_logger(__name__)


class FeatureStore:
    """
    เก็บ feature aggregates — ข้อมูลสรุปจาก indicators.
    
    ไม่เก็บ raw data — เก็บเฉพาะสถิติสรุป.
    """

    def __init__(self, memory_store=None) -> None:
        self.memory = memory_store

    async def update_features(self, symbol: str, features: dict) -> None:
        """
        อัปเดต feature aggregates สำหรับสัญลักษณ์.
        
        Args:
            symbol: สัญลักษณ์ เช่น XAUUSD
            features: dict ของค่า indicator เช่น {"rsi": 65.2, "atr": 1.5}
        """
        # TODO: rolling update mean/std/min/max
        logger.debug("update_features", extra={"symbol": symbol, "count": len(features)})

    async def get_features(self, symbol: str, regime: str = "UNKNOWN") -> dict:
        """ดึง aggregated features สำหรับสัญลักษณ์ + regime."""
        # TODO: query จาก memory store
        return {}
