"""
DuckDB Analytics Store — เครื่องมือวิเคราะห์และ backtest.

หน้าที่:
    - คำนวณ metrics: win_rate, profit_factor, max_dd, expectancy, Sharpe
    - อ่าน CSV/Parquet สำหรับ backtest (streaming execution)
    - วิเคราะห์ per-session / per-symbol
    - Discipline metrics (blocked trades ต้อง = 0 ในช่วง news)

เหตุผลที่ใช้ DuckDB:
    - อ่านข้อมูลแบบ columnar ได้เร็ว
    - ไม่ต้อง load ทั้งหมดเข้า RAM (streaming)
    - ทำงานฝังตัว (embedded) เหมือน SQLite
"""

from pathlib import Path

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class DuckDBStore:
    """
    DuckDB wrapper — analytics engine สำหรับ backtest และ metrics.
    
    ใช้ streaming execution เพื่อไม่ให้ใช้ RAM เยอะ.
    """

    def __init__(self, settings: Settings) -> None:
        self.db_path = Path(settings.duckdb_db_path)
        self._conn = None  # duckdb.Connection

    def connect(self) -> None:
        """เปิดการเชื่อมต่อ DuckDB — สร้างไฟล์ถ้ายังไม่มี."""
        import duckdb

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self.db_path))
        logger.info("duckdb_connected", extra={"path": str(self.db_path)})

    def compute_metrics(self, symbol: str | None = None) -> dict:
        """
        คำนวณ performance metrics.
        
        Returns:
            dict: {
                "win_rate": float,
                "profit_factor": float,
                "max_drawdown": float,
                "expectancy": float,
                "avg_r": float,
                "sharpe_ratio": float,
                "total_trades": int,
            }
        """
        # TODO: implement query จาก trade journal
        logger.info("compute_metrics", extra={"symbol": symbol or "ALL"})
        return {
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "expectancy": 0.0,
            "avg_r": 0.0,
            "sharpe_ratio": 0.0,
            "total_trades": 0,
        }

    def health_check(self) -> bool:
        """ตรวจสอบว่า DuckDB ยังทำงานได้."""
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
            logger.info("duckdb_disconnected")
