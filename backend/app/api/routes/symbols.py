"""
Symbols Route — ดูรายการสัญลักษณ์ที่เทรดได้.

แสดง:
    - profile version, session allowed, news safe, spread ปัจจุบัน
    - จำนวน open positions / max
    - last decision + เหตุผลที่ BLOCKED
"""

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/symbols")
async def list_symbols():
    """ดูรายการสัญลักษณ์ทั้งหมดจาก QuestDB profiles."""
    # TODO: query จาก QuestDB
    return {"symbols": [], "count": 0}


@router.get("/symbols/{symbol}")
async def get_symbol_detail(symbol: str):
    """ดูรายละเอียดสัญลักษณ์เฉพาะ — profile, positions, last decision."""
    # TODO: query จาก QuestDB + runtime state
    return {
        "symbol": symbol,
        "profile": None,
        "open_positions": 0,
        "max_positions": 2,
        "last_decision": None,
        "session_allowed": True,
        "news_safe": True,
    }
