"""
Replay Route — เริ่ม/หยุด replay session.

Replay = สตรีมข้อมูลย้อนหลังผ่าน execution pipeline.
ผลลัพธ์แสดงบน frontend แบบ real-time ผ่าน WebSocket.
"""

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.post("/replay/start")
async def start_replay(
    symbol: str = "XAUUSD",
    timeframe: str = "M5",
    start_date: str = "",
    end_date: str = "",
):
    """เริ่ม replay session — สตรีม historical data ผ่าน pipeline."""
    logger.info("replay_api_start", extra={"symbol": symbol})
    # TODO: เริ่ม ReplayStreamer ใน background task
    return {"status": "started", "symbol": symbol}


@router.post("/replay/stop")
async def stop_replay():
    """หยุด replay session."""
    # TODO: หยุด ReplayStreamer
    return {"status": "stopped"}
