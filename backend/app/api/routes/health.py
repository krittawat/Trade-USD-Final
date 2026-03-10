"""
Health Route — ตรวจสุขภาพระบบทั้งหมดจริง.

แสดง:
    - mode: LIVE / DRY_RUN / REPLAY / BACKTEST
    - สถานะ MT5, SQLite, DuckDB (real checks)
    - account info, uptime, version
    - brain status + strategies registered

Endpoints:
    GET  /api/health        — ตรวจสุขภาพระบบ
    POST /api/kill-switch   — Emergency kill-switch
"""

from datetime import datetime, timezone
import asyncio

# httpx removed — no more QuestDB HTTP ping needed

from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

_start_time = datetime.now(timezone.utc)


@router.get("")
async def health_check(request: Request):
    """
    ตรวจสุขภาพระบบจริง — Dashboard ใช้ endpoint นี้.

    ตรวจ:
        - MT5: connected / disconnected (+ account info)
        - Ticks: via SQLite (ไม่ต้อง QuestDB แล้ว)
        - SQLite: file exists + SELECT 1
        - DuckDB: SELECT 1
        - Brain: connected / disconnected
        - Factory: จำนวน strategies ที่ลงทะเบียน
    """
    settings = get_settings()

    # ─── MT5 check ───
    # ─── MT5 check ───
    mt5_status = "disconnected"
    account_info = {}
    master_loop = getattr(request.app.state, "master_loop", None)
    
    try:
        # Check MT5 underlying connection via client if possible, or just use adapter state
        mt5 = getattr(request.app.state, "mt5", None)
        if mt5 and await asyncio.to_thread(mt5.is_connected):
            mt5_status = "connected"
        
        # Get Account Info from Adapter (Uniform for Live/Dry)
        if master_loop and hasattr(master_loop, "adapter") and master_loop.adapter:
            # If adapter allows basic account check
             acct = await asyncio.to_thread(master_loop.adapter.get_account_state)
             account_info = {
                 "balance": acct.balance,
                 "equity": acct.equity,
                 "margin": acct.margin,
                 "free_margin": acct.free_margin,
                 "open_positions": acct.open_positions,
                 "mode": settings.trading_mode
             }
        elif mt5 and await asyncio.to_thread(mt5.is_connected):
             # Fallback
             try:
                 info = await asyncio.to_thread(mt5.account_info)
                 if info:
                    positions = await asyncio.to_thread(mt5.positions_get)
                    account_info = {
                        "login": info.login,
                        "server": info.server,
                        "balance": info.balance,
                        "equity": info.equity,
                        "margin": info.margin,
                        "free_margin": info.margin_free,
                        "open_positions": len(positions or []),
                    }
             except Exception:
                 pass

    except Exception:
        mt5_status = "error"

    # ─── QuestDB removed — tick storage via SQLite ───

    # ─── SQLite check ───
    sqlite_status = "connected"
    try:
        import sqlite3
        import os
        db_path = settings.sqlite_db_path
        if os.path.exists(db_path):
            conn = await asyncio.to_thread(sqlite3.connect, db_path, check_same_thread=False)
            await asyncio.to_thread(conn.execute, "SELECT 1")
            await asyncio.to_thread(conn.close)
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
