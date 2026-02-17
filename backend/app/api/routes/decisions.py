"""
Decisions Route — ข้อมูล decision traces จริงจาก SQLite.

แสดง decision trace timeline — ทุก decision ทั้ง ok และ blocked.
"ไม่เทรด" ต้องแสดงเหตุผลเสมอ — ห้ามบล็อกเงียบ.

Endpoints:
    GET /api/decisions         — ประวัติ decisions (filter by symbol, limit)
    GET /api/decisions/latest  — decisions ล่าสุดของทุก symbol (live + recent)
"""

from fastapi import APIRouter, Query, Request

from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/decisions")
async def list_decisions(
    request: Request,
    symbol: str = Query(default=None, description="filter by symbol"),
    limit: int = Query(default=50, ge=1, le=500, description="max results"),
):
    """
    ดูประวัติ decisions — ทั้ง ok และ blocked (จาก SQLite).

    ใช้ request.app.state.db ที่ถูก inject มาจาก lifespan.
    """
    try:
        db = request.app.state.db
        if db:
            decisions = db.get_decisions(symbol=symbol, limit=limit)
            return {"decisions": decisions, "count": len(decisions)}
    except Exception as e:
        logger.error("decisions_error", extra={"error": str(e)})
    return {"decisions": [], "count": 0}


@router.get("/decisions/latest")
async def get_latest_decisions(request: Request):
    """
    ดู decisions ล่าสุดของทุกสัญลักษณ์.

    แหล่งข้อมูล:
        - live: จาก master_loop.last_decisions (real-time state)
        - recent: จาก SQLite (persisted decisions ล่าสุด 20 รายการ)
    """
    try:
        result = {}

        # --- Live state จาก master_loop ---
        master_loop = getattr(request.app.state, "master_loop", None)
        if master_loop and master_loop.last_decisions:
            result["live"] = master_loop.last_decisions

        # --- Recent จาก SQLite ---
        db = getattr(request.app.state, "db", None)
        if db:
            result["recent"] = db.get_latest_decisions(limit=20)

        return {"decisions": result}
    except Exception as e:
        logger.error("latest_decisions_error", extra={"error": str(e)})
        return {"decisions": {}}
