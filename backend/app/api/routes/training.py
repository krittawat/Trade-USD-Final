"""
Training API Routes — endpoints สำหรับจัดการ self-training.

Endpoints:
    GET  /training/status       — สถานะปัจจุบัน (running/idle, last report)
    GET  /training/history      — ประวัติ training sessions
    POST /training/trigger      — สั่งฝึกซ้อมด้วยตนเอง
    GET  /training/params       — ดู evolved params ทั้งหมด
    GET  /training/patterns     — ดู pattern performance
    GET  /training/news-impact  — ดู news impact performance
"""

import asyncio

from fastapi import APIRouter, Request, HTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/training/status")
async def get_training_status(request: Request):
    """
    ดูสถานะ self-training system.

    Returns:
        running: กำลังฝึกอยู่หรือไม่
        last_report: ผลการฝึกล่าสุด (ถ้ามี)
        enabled: เปิดใช้งานหรือไม่
    """
    orchestrator = getattr(request.app.state, "training_orchestrator", None)
    settings = getattr(request.app.state, "settings", None)

    if not orchestrator:
        return {
            "status": "not_initialized",
            "running": False,
            "enabled": False,
            "last_report": None,
        }

    last = orchestrator.last_report
    return {
        "status": "running" if orchestrator.is_running else "idle",
        "running": orchestrator.is_running,
        "enabled": settings.training_enabled if settings else True,
        "last_report": last.model_dump() if last else None,
    }


@router.get("/training/history")
async def get_training_history(request: Request, limit: int = 10):
    """
    ดูประวัติ training sessions ล่าสุด.

    Query params:
        limit: จำนวน sessions ที่ดึง (default 10)
    """
    memory = getattr(request.app.state, "memory", None)
    if not memory:
        return {"sessions": []}

    sessions = memory.get_training_history(limit=min(limit, 50))
    return {"sessions": sessions}


@router.post("/training/trigger")
async def trigger_training(request: Request):
    """
    สั่งฝึกซ้อมทันที (manual trigger).

    ถ้ากำลังฝึกอยู่แล้ว → return error.
    """
    orchestrator = getattr(request.app.state, "training_orchestrator", None)
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Training system not initialized")

    if orchestrator.is_running:
        raise HTTPException(status_code=409, detail="Training already in progress")

    # Run training in background
    async def _run_training():
        try:
            report = await orchestrator.run_training_session()
            logger.info("manual_training_complete", extra={
                "session_id": report.session_id,
                "duration": report.duration_seconds,
            })
        except Exception as e:
            logger.error("manual_training_error", extra={"error": str(e)},
                         exc_info=True)

    asyncio.create_task(_run_training())

    return {
        "status": "started",
        "message": "Training session started in background",
    }


@router.get("/training/params")
async def get_evolved_params(request: Request):
    """
    ดู evolved parameters ทั้งหมด.

    Returns:
        params: list of {strategy_name, symbol, regime, params, score, generation}
    """
    memory = getattr(request.app.state, "memory", None)
    if not memory:
        return {"params": []}

    evolved = memory.get_all_evolved_params()
    return {"params": evolved}


@router.get("/training/patterns")
async def get_pattern_performance(request: Request):
    """
    ดู pattern performance — patterns ไหน win rate สูง/ต่ำ.

    Returns:
        patterns: list of {pattern_name, symbol, regime, win_rate, total_trades, ...}
    """
    memory = getattr(request.app.state, "memory", None)
    if not memory:
        return {"patterns": []}

    patterns = memory.get_all_pattern_performance()
    return {"patterns": patterns}


@router.get("/training/news-impact")
async def get_news_impact(request: Request, symbol: str | None = None):
    """
    ดู news impact performance — ข่าวแบบไหนมีผลต่อ win_rate.

    Query params:
        symbol: กรองตาม symbol (optional)

    Returns:
        news_performance: list of {news_impact, symbol, win_rate, total_trades, ...}
    """
    memory = getattr(request.app.state, "memory", None)
    if not memory:
        return {"news_performance": []}

    news_perf = memory.get_news_performance(symbol=symbol)
    return {"news_performance": news_perf}
