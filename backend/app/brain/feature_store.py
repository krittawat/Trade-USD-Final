"""
Feature Store — เก็บ aggregated features (ไม่เก็บ raw tick).

หน้าที่:
    - สรุปค่า indicator (mean/std/min/max) ต่อ symbol + regime
    - ใช้ MemoryStore (SQLite) สำหรับ persist
    - เก็บผลลัพธ์ compact — ใช้ rolling update (ไม่ต้อง recompute ทุกรอบ)

กฎ RAM (8GB mode):
    - ไม่เก็บ raw tick/candle data
    - ใช้ running statistics formula (Welford's algorithm)
    - ค่าที่เก็บ: count, mean, m2 (สำหรับคำนวณ std), min, max

ตัวอย่างการใช้:
    feature_store = FeatureStore(memory_store=memory)
    await feature_store.update_features("XAUUSDm", {"rsi": 65.2, "atr": 1.5})
    features = await feature_store.get_features("XAUUSDm", regime="TRENDING_UP")
"""

import math
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class FeatureStore:
    """
    เก็บ feature aggregates — ข้อมูลสรุปจาก indicators.

    ไม่เก็บ raw data — เก็บเฉพาะสถิติสรุป (mean, std, min, max).
    ใช้ Welford's online algorithm สำหรับ running mean/variance.
    """

    def __init__(self, memory_store=None) -> None:
        """
        Args:
            memory_store: MemoryStore instance สำหรับ persist ลง SQLite
        """
        self.memory = memory_store

    async def update_features(self, symbol: str, features: dict,
                               regime: str = "UNKNOWN") -> None:
        """
        อัปเดต feature aggregates สำหรับสัญลักษณ์.

        ใช้ Welford's algorithm สำหรับ running statistics:
            - count += 1
            - delta = value - mean
            - mean += delta / count
            - delta2 = value - mean (หลังอัปเดต)
            - m2 += delta * delta2
            - std = sqrt(m2 / count)

        Args:
            symbol: สัญลักษณ์ เช่น XAUUSDm
            features: dict ของค่า indicator เช่น {"rsi": 65.2, "atr": 1.5}
            regime: สภาวะตลาด เช่น TRENDING_UP
        """
        if not self.memory or not features:
            return

        try:
            for name, value in features.items():
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    continue  # ข้ามค่า NaN / None

                self.memory.update_feature_aggregate(
                    symbol=symbol,
                    regime=regime,
                    feature_name=name,
                    value=float(value),
                )

            logger.debug("features_updated", extra={
                "symbol": symbol,
                "regime": regime,
                "count": len(features),
            })
        except Exception as e:
            logger.error("feature_update_error", extra={
                "symbol": symbol,
                "error": str(e),
            }, exc_info=True)

    async def get_features(self, symbol: str,
                            regime: str = "UNKNOWN") -> dict:
        """
        ดึง aggregated features สำหรับสัญลักษณ์ + regime.

        Returns:
            dict: {
                "rsi": {"mean": 55.3, "std": 12.1, "min": 20.0, "max": 85.0, "count": 500},
                "atr": {"mean": 1.8, "std": 0.5, "min": 0.5, "max": 4.2, "count": 500},
                ...
            }
        """
        if not self.memory:
            return {}

        try:
            return self.memory.get_feature_aggregates(symbol=symbol, regime=regime)
        except Exception as e:
            logger.error("feature_get_error", extra={
                "symbol": symbol,
                "error": str(e),
            })
            return {}
