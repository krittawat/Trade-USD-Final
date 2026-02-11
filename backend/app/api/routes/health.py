"""
Health Route — ตรวจสุขภาพระบบทั้งหมด.

แสดง:
    - mode: LIVE / DRY_RUN / REPLAY / BACKTEST
    - สถานะ MT5, QuestDB, SQLite, DuckDB
    - uptime, version
"""

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/health")
async def health_check():
    """
    ตรวจสุขภาพระบบ.
    
    Dashboard ใช้ endpoint นี้แสดงสถานะของทุก component.
    """
    settings = get_settings()
    
    # TODO: ตรวจสอบ connection จริงของแต่ละ service
    return {
        "status": "ok",
        "mode": settings.trading_mode,
        "version": "0.1.0",
        "services": {
            "mt5": "stub",       # TODO: ตรวจ MT5 connection
            "questdb": "stub",   # TODO: ตรวจ QuestDB health
            "sqlite": "stub",    # TODO: ตรวจ SQLite
            "duckdb": "stub",    # TODO: ตรวจ DuckDB
        },
    }
