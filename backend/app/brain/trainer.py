"""
Trainer — สรุปและเรียนรู้จากข้อมูลใหม่เป็นระยะ.

หน้าที่:
    - Periodic: ทุก N ชั่วโมง สรุปผลเทรดใหม่เข้า memory
    - อัปเดต regime statistics
    - อัปเดต strategy performance tables
    - Compact old data (ลบ raw, เก็บเฉพาะ aggregates)

กฎ RAM:
    - ทำงานแบบ batch/streaming — ไม่ load ทั้งหมดเข้า RAM
    - ใช้ DuckDB สำหรับ aggregation แล้วเก็บผลใน SQLite
"""

from app.core.logging import get_logger

logger = get_logger(__name__)


class Trainer:
    """
    AI Trainer — สรุปผลเทรดและอัปเดต memory เป็นระยะ.
    
    ทำงานเป็น background task — ไม่กระทบ main trading loop.
    """

    def __init__(self, memory_store=None, duckdb_store=None) -> None:
        self.memory = memory_store
        self.analytics = duckdb_store

    async def run_training_cycle(self) -> None:
        """
        รอบการเรียนรู้:
            1. ดึงเทรดใหม่จาก trade journal
            2. คำนวณ performance ต่อ strategy/regime/session
            3. อัปเดต memory store
            4. Compact ข้อมูลเก่า
        """
        logger.info("training_cycle_start")

        # TODO: Implement
        # 1. trades = query_new_trades_since_last_training()
        # 2. stats = compute_strategy_stats(trades)
        # 3. memory.update_performance(stats)
        # 4. memory.compact_old_data()

        logger.info("training_cycle_complete")

    async def update_regime_stats(self, symbol: str) -> None:
        """อัปเดต regime statistics สำหรับสัญลักษณ์."""
        # TODO: ดึง candles จาก QuestDB → classify regime → update stats
        logger.debug("update_regime_stats", extra={"symbol": symbol})
