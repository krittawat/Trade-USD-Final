import asyncio
from fastapi import APIRouter, Request, BackgroundTasks, HTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

@router.post("/start")
async def start_replay(
    request: Request,
    background_tasks: BackgroundTasks,
    symbol: str = "XAUUSD",
    timeframe: str = "M5",
    start_date: str = "",
    end_date: str = "",
    speed: float = 0.1,  # Default 100ms between bars
):
    """เริ่ม replay session ใน background."""
    replay_streamer = request.app.state.replay_streamer
    
    if replay_streamer.is_running:
        raise HTTPException(status_code=400, detail="Replay already running")

    logger.info("replay_api_start_request", extra={"symbol": symbol, "timeframe": timeframe})
    
    # Run in background to not block the API response
    background_tasks.add_task(
        replay_streamer.replay,
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        speed=speed
    )
    
    return {
        "status": "started",
        "symbol": symbol,
        "timeframe": timeframe,
        "mode": "REPLAY"
    }

@router.post("/stop")
async def stop_replay(request: Request):
    """หยุด replay session ที่กำลังรันอยู่."""
    replay_streamer = request.app.state.replay_streamer
    replay_streamer.stop()
    return {"status": "stop_requested"}

@router.get("/status")
async def get_replay_status(request: Request):
    """เช็คสถานะและความคืบหน้าของ replay."""
    replay_streamer = request.app.state.replay_streamer
    return {
        "is_running": replay_streamer.is_running,
        "current_symbol": replay_streamer.current_symbol,
        "progress": round(replay_streamer.progress, 2)
    }
