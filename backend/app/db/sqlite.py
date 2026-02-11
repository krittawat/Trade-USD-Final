"""
SQLite Store — ฐานข้อมูลฝังตัว (embedded) สำหรับข้อมูลสถานะ.

หน้าที่:
    - Trade journal (บันทึกเทรดทุกรายการ)
    - Runtime state (สถานะระบบ, kill switch, mode)
    - Decision traces (ร่องรอยการตัดสินใจ)
    - QC results (ผลการทดสอบ)
    - AI Brain metadata (ดัชนีหน่วยความจำ)

เหตุผลที่ใช้ SQLite:
    - ใช้ RAM น้อยมาก (เหมาะกับเครื่อง 8GB)
    - ไม่ต้องติดตั้ง service เพิ่ม
    - Embedded — ทำงานในตัว
"""

import sqlite3
from pathlib import Path

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SQLiteStore:
    """
    SQLite wrapper — จัดการ trade journal และ runtime state.
    
    ทำ migration อัตโนมัติเมื่อเริ่มระบบ.
    """

    def __init__(self, settings: Settings) -> None:
        self.db_path = Path(settings.sqlite_db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """เปิดการเชื่อมต่อ SQLite — สร้างไฟล์ถ้ายังไม่มี."""
        # สร้างโฟลเดอร์ parent ถ้ายังไม่มี
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row  # ให้ได้ dict-like results
        self._conn.execute("PRAGMA journal_mode=WAL")  # WAL mode เพื่อ performance
        logger.info("sqlite_connected", extra={"path": str(self.db_path)})
        self._run_migrations()

    def _run_migrations(self) -> None:
        """สร้างตารางที่จำเป็น (idempotent — รันซ้ำได้)."""
        assert self._conn is not None, "ต้อง connect() ก่อนใช้งาน"
        
        # ตาราง trade journal — บันทึกเทรดทุกรายการ
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                lot_size REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                stop_loss REAL NOT NULL,
                take_profit REAL,
                risk_usd REAL NOT NULL,
                profit_usd REAL,
                strategy_name TEXT,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                regime TEXT,
                session TEXT,
                tags TEXT,
                notes TEXT
            )
        """)

        # ตาราง decision traces — ร่องรอยการตัดสินใจ
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                stage TEXT NOT NULL,
                result TEXT NOT NULL,
                reason TEXT,
                details TEXT
            )
        """)

        # ตาราง runtime state — สถานะระบบ
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS runtime_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        self._conn.commit()
        logger.info("sqlite_migrations_complete")

    def health_check(self) -> bool:
        """ตรวจสอบว่า SQLite ยังทำงานได้."""
        try:
            if self._conn:
                self._conn.execute("SELECT 1")
                return True
        except Exception:
            pass
        return False

    def disconnect(self) -> None:
        """ปิดการเชื่อมต่อ."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("sqlite_disconnected")
