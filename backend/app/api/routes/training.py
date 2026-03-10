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



# ====================================================================
# Deep Learning Endpoints
# ====================================================================

@router.get("/training/deep/status")
async def get_deep_learning_status(request: Request):
    """Get Deep Learning model status."""
    dl = getattr(request.app.state, "deep_learner", None)
    if not dl:
        return {"enabled": False, "status": "not_initialized"}
    return dl.get_status()


@router.post("/training/deep/trigger")
async def trigger_deep_learning_training(
    request: Request,
    symbol: str = "XAUUSDm",
    timeframe: str = "M5",
    limit: int = 5000
):
    """
    Trigger Deep Learning (LSTM) training manually.
    
    Args:
        symbol: Symbol to train on (e.g., XAUUSDm) - currently trains on single symbol for demo
        timeframe: Timeframe (e.g., M5)
        limit: Max candles to load
    """
    dl = getattr(request.app.state, "deep_learner", None)
    if not dl:
        raise HTTPException(status_code=503, detail="Deep Learning module not initialized")
        
    if not dl._enabled:
         raise HTTPException(status_code=400, detail="Deep Learning is disabled or Torch is missing")

    async def _run_dl_training():
        try:
            from app.brain.data_loader import DLDataLoader
            loader = DLDataLoader()
            
            logger.info("dl_loading_data", extra={"symbol": symbol, "limit": limit})
            # Run CPU-bound work in thread pool to avoid blocking the event loop
            X, y = await asyncio.to_thread(loader.load_training_data, symbol, timeframe, limit)
            
            if len(X) == 0:
                logger.warning("dl_no_data", extra={"symbol": symbol})
                return
                
            report = await asyncio.to_thread(dl.train_session, X, y)
            logger.info("dl_training_complete", extra=report)
            
        except Exception as e:
            logger.error("dl_training_error", extra={"error": str(e)}, exc_info=True)

    asyncio.create_task(_run_dl_training())

    return {
        "status": "started",
        "message": f"Deep Learning training started for {symbol} {timeframe}",
        "data_limit": limit
    }


# ====================================================================
# Tournament Backtest Endpoints
# ====================================================================

@router.post("/training/tournament")
async def trigger_tournament(
    request: Request,
    symbols: str | None = None,
    days: int = 60,
    timeframe: str = "M5",
):
    """
    Trigger tournament backtest in background.

    Query params:
        symbols: comma-separated symbols (default: all configured)
        days: number of days (default: 60)
        timeframe: M1/M5/M15/M30/H1 (default: M5)
    """
    async def _run_tournament():
        import subprocess
        import sys

        cmd = [sys.executable, "scripts/pro_tournament.py",
               "--days", str(days), "--timeframe", timeframe]
        if symbols:
            cmd.extend(["--symbols"] + symbols.split(","))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=str(Path(__file__).resolve().parent.parent.parent.parent),
                timeout=600,
            )
            logger.info("tournament_complete", extra={
                "returncode": result.returncode,
                "stdout_tail": result.stdout[-500:] if result.stdout else "",
            })
        except Exception as e:
            logger.error("tournament_error", extra={"error": str(e)}, exc_info=True)

    from pathlib import Path
    asyncio.create_task(_run_tournament())

    return {
        "status": "started",
        "message": f"Tournament started (days={days}, tf={timeframe})",
        "symbols": symbols or "all",
    }


@router.get("/training/tournament-results")
async def get_tournament_results(
    request: Request,
    symbol: str | None = None,
    limit: int = 20,
):
    """
    Get latest tournament backtest results.

    Query params:
        symbol: filter by symbol (optional)
        limit: max results per symbol (default: 20)
    """
    import sqlite3
    import json

    db_path = "data/sqlite/trading.db"
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        query = """
            SELECT symbol, strategy_name, total_trades, win_rate,
                   profit_factor, total_profit_usd, max_drawdown_pct,
                   composite_score, live_recommended, rank,
                   created_at, per_regime
            FROM tournament_results
            WHERE created_at = (SELECT MAX(created_at) FROM tournament_results)
        """
        params = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)

        query += " ORDER BY symbol, rank ASC LIMIT ?"
        params.append(limit * 10)

        rows = conn.execute(query, params).fetchall()
        conn.close()

        results = []
        for row in rows:
            entry = dict(row)
            try:
                entry["per_regime"] = json.loads(entry.get("per_regime", "{}"))
            except (json.JSONDecodeError, TypeError):
                entry["per_regime"] = {}
            results.append(entry)

        # Group by symbol
        by_symbol = {}
        for r in results:
            sym = r["symbol"]
            if sym not in by_symbol:
                by_symbol[sym] = []
            if len(by_symbol[sym]) < limit:
                by_symbol[sym].append(r)

        return {
            "total_results": len(results),
            "by_symbol": by_symbol,
        }

    except sqlite3.OperationalError as e:
        return {
            "total_results": 0,
            "by_symbol": {},
            "error": f"No tournament data yet: {e}",
        }
