"""
Health Route — ตรวจสุขภาพระบบทั้งหมดจริง.

แสดง:
    - mode: LIVE / DRY_RUN / REPLAY / BACKTEST
    - สถานะ MT5, QuestDB, SQLite, DuckDB (real checks)
    - account info, uptime, version
    - brain status + strategies registered

Endpoints:
    GET  /api/health        — ตรวจสุขภาพระบบ
    POST /api/kill-switch   — Emergency kill-switch
"""

from datetime import datetime, timezone

import httpx

from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

_start_time = datetime.now(timezone.utc)


@router.get("/health")
async def health_check(request: Request):
    """
    ตรวจสุขภาพระบบจริง — Dashboard ใช้ endpoint นี้.

    ตรวจ:
        - MT5: connected / disconnected (+ account info)
        - QuestDB: HTTP ping
        - SQLite: file exists + SELECT 1
        - DuckDB: SELECT 1
        - Brain: connected / disconnected
        - Factory: จำนวน strategies ที่ลงทะเบียน
    """
    settings = get_settings()

    # ─── MT5 check ───
    mt5_status = "disconnected"
    account_info = {}
    try:
        import MetaTrader5 as mt5
        term = mt5.terminal_info()
        if term and term.connected:
            mt5_status = "connected"
            info = mt5.account_info()
            if info:
                account_info = {
                    "login": info.login,
                    "server": info.server,
                    "balance": info.balance,
                    "equity": info.equity,
                    "margin": info.margin,
                    "free_margin": info.margin_free,
                    "open_positions": len(mt5.positions_get() or []),
                }
    except Exception:
        mt5_status = "error"

    # ─── QuestDB check ───
    qdb_status = "disconnected"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"http://{settings.questdb_host}:{settings.questdb_http_port}")
            qdb_status = "connected" if r.status_code == 200 else "error"
    except Exception:
        qdb_status = "disconnected"

    # ─── SQLite check ───
    sqlite_status = "connected"
    try:
        import sqlite3
        import os
        db_path = settings.sqlite_db_path
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT 1")
            conn.close()
        else:
            sqlite_status = "no_file"
    except Exception:
        sqlite_status = "error"

    # ─── DuckDB check ───
    duckdb_status = "connected"
    try:
        import duckdb
        conn = duckdb.connect(settings.duckdb_db_path)
        conn.execute("SELECT 1")
        conn.close()
    except Exception:
        duckdb_status = "error"

    # ─── Brain status ───
    brain_status = "disconnected"
    memory = getattr(request.app.state, "memory", None)
    if memory and memory._conn:
        brain_status = "connected"

    # ─── Factory info ───
    strategies_registered = 0
    factory = getattr(request.app.state, "factory", None)
    if factory:
        strategies_registered = len(factory._strategies)

    # ─── Master loop info ───
    master_loop = getattr(request.app.state, "master_loop", None)
    loop_info = {}
    if master_loop:
        loop_info = {
            "cycle_count": master_loop.cycle_count,
            "running": master_loop.running,
            "kill_switch": master_loop.kill_switch,
            "symbols_tracked": len(master_loop.last_decisions),
        }

    # ─── Uptime ───
    uptime = (datetime.now(timezone.utc) - _start_time).total_seconds()

    return {
        "status": "ok" if mt5_status == "connected" else "degraded",
        "mode": settings.trading_mode,
        "version": "0.3.0",
        "uptime_seconds": round(uptime, 1),
        "services": {
            "mt5": mt5_status,
            "questdb": qdb_status,
            "sqlite": sqlite_status,
            "duckdb": duckdb_status,
            "brain": brain_status,
        },
        "strategies_registered": strategies_registered,
        "master_loop": loop_info,
        "account": account_info,
    }


@router.post("/kill-switch")
async def activate_kill_switch(request: Request):
    """
    Emergency Kill Switch — หยุดส่งออเดอร์ทันที.

    เรียก master_loop.activate_kill_switch() ผ่าน app.state.
    """
    master_loop = getattr(request.app.state, "master_loop", None)
    if master_loop:
        master_loop.activate_kill_switch()
        logger.critical("kill_switch_activated_via_api")
        return {"status": "activated", "message": "Kill switch activated — no more orders"}

    return {"status": "error", "message": "Master loop not available"}
