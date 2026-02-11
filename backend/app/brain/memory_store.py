"""
Memory Store — ที่เก็บหน่วยความจำ AI (SQLite-backed).

หน้าที่:
    - เก็บ learned features แบบ compact (ไม่เก็บ raw tick ใน RAM)
    - เก็บ regime statistics (ความผันผวน, strength, session behavior)
    - เก็บ strategy performance ต่อ symbol/timeframe/regime
    - เก็บ win/loss patterns

กฎ RAM:
    - ข้อมูลถูก summarize ก่อนเก็บ (feature aggregates, ไม่ใช่ raw data)
    - ใช้ SQLite — ไม่กิน RAM เยอะ
    - Periodic cleanup เพื่อลบข้อมูลเก่าที่ไม่ใช้
"""

import sqlite3
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class MemoryStore:
    """
    AI Memory — เก็บความรู้ที่เรียนรู้จากตลาด.
    
    ออกแบบเป็น compact storage:
        - ไม่เก็บ raw tick/candle (นั่นอยู่ใน QuestDB)
        - เก็บเฉพาะ aggregated features, statistics, performance tables
    """

    def __init__(self, db_path: str = "backend/data/sqlite/brain.db") -> None:
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """เปิดการเชื่อมต่อ — สร้างตารางที่จำเป็น."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info("brain_memory_connected", extra={"path": str(self.db_path)})

    def _create_tables(self) -> None:
        """สร้างตาราง memory (idempotent)."""
        assert self._conn is not None
        
        # ตาราง regime statistics — สถิติต่อสภาวะตลาด
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS regime_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                regime TEXT NOT NULL,
                session TEXT,
                timeframe TEXT,
                avg_volatility REAL,
                avg_range REAL,
                trend_strength REAL,
                sample_count INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)

        # ตาราง strategy performance — ผลงาน strategy ต่อสภาวะ
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                regime TEXT,
                session TEXT,
                win_rate REAL DEFAULT 0,
                profit_factor REAL DEFAULT 0,
                avg_rr REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)

        # ตาราง feature aggregates — ค่าเฉลี่ย indicator ต่อสภาวะ
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS feature_aggregates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                regime TEXT,
                mean_value REAL,
                std_value REAL,
                min_value REAL,
                max_value REAL,
                sample_count INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)

        self._conn.commit()

    def get_best_strategy(
        self,
        symbol: str,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
    ) -> Optional[str]:
        """
        ถาม AI Brain: strategy ไหนดีที่สุดสำหรับสภาวะนี้?

        Query:
            "อะไรที่เคยใช้ได้ผลดีสำหรับ {symbol} ใน session {session}, regime {regime}?"
        
        Returns:
            ชื่อ strategy ที่ profit_factor สูงสุด (ถ้ามีข้อมูลพอ)
            None ถ้ายังไม่มีข้อมูล
        """
        # TODO: query จาก strategy_performance table
        logger.debug("brain_query", extra={
            "symbol": symbol,
            "regime": regime,
            "session": session,
        })
        return None

    def disconnect(self) -> None:
        """ปิดการเชื่อมต่อ."""
        if self._conn:
            self._conn.close()
            self._conn = None
