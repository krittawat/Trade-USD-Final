"""
Decisions Route — ดูประวัติการตัดสินใจ.

แสดง decision trace timeline — ทุก decision ทั้ง ok และ blocked.
"ไม่เทรด" ต้องแสดงเหตุผลเสมอ — ห้ามบล็อกเงียบ.
"""

from fastapi import APIRouter, Query

from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/decisions")
async def list_decisions(
    symbol: str = Query(default=None, description="กรองตามสัญลักษณ์"),
    limit: int = Query(default=50, ge=1, le=500, description="จำนวนสูงสุด"),
):
    """ดูประวัติ decisions — ทั้ง ok และ blocked."""
    # TODO: query จาก SQLite decision_traces
    return {"decisions": [], "count": 0}


@router.get("/decisions/latest")
async def get_latest_decisions():
    """ดู decisions ล่าสุดของทุกสัญลักษณ์."""
    # TODO: query decisions ล่าสุด per symbol
    return {"decisions": {}}
