"""
Brain Intelligence API — endpoints สำหรับ dashboard.

Endpoints:
    GET /api/brain/sentiment      — sentiment ล่าสุดต่อ symbol
    GET /api/brain/ml-status      — สถานะ ML model
    GET /api/brain/patterns       — discovered patterns
    GET /api/brain/intelligence   — full intelligence summary
"""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/brain/sentiment")
async def get_sentiment(request: Request):
    """ดึง sentiment ล่าสุดทุก symbol."""
    aggregator = getattr(request.app.state, "sentiment_aggregator", None)
    memory = getattr(request.app.state, "memory", None)

    result = {"status": "disabled", "data": {}}

    if aggregator:
        result["status"] = "active"
        result["data"] = aggregator.get_all_sentiments()
    elif memory:
        # Fallback: get from DB
        result["status"] = "from_db"
        result["data"] = memory.get_latest_sentiment()

    return result


@router.get("/brain/ml-status")
async def get_ml_status(request: Request):
    """ดึง ML model status."""
    learner = getattr(request.app.state, "ml_pattern_learner", None)

    if not learner:
        return {"status": "disabled", "data": {}}

    return {
        "status": "active",
        "data": learner.get_status(),
    }


@router.get("/brain/patterns")
async def get_discovered_patterns(request: Request):
    """ดึง discovered patterns จาก brain.db."""
    memory = getattr(request.app.state, "memory", None)

    if not memory:
        return {"status": "no_memory", "data": []}

    patterns = memory.get_discovered_patterns(min_examples=3)
    return {
        "status": "ok",
        "count": len(patterns),
        "data": patterns,
    }


@router.get("/brain/intelligence")
async def get_intelligence_summary(request: Request):
    """Full intelligence summary: sentiment + ML + patterns."""
    aggregator = getattr(request.app.state, "sentiment_aggregator", None)
    learner = getattr(request.app.state, "ml_pattern_learner", None)
    memory = getattr(request.app.state, "memory", None)

    result = {
        "sentiment": {},
        "ml": {},
        "patterns": [],
        "status": "ok",
    }

    if aggregator:
        result["sentiment"] = aggregator.get_all_sentiments()

    if learner:
        result["ml"] = learner.get_status()

    if memory:
        result["patterns"] = memory.get_discovered_patterns(min_examples=3)

    return result


@router.get("/brain/patterns/performance")
async def get_pattern_performance(request: Request):
    """ดึง pattern performance — win rate ของแต่ละ candlestick pattern."""
    memory = getattr(request.app.state, "memory", None)

    if not memory:
        return {"status": "no_memory", "data": []}

    try:
        data = memory.get_all_pattern_performance()
        return {
            "status": "ok",
            "count": len(data),
            "data": data,
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "data": []}


@router.get("/brain/patterns/best")
async def get_best_patterns(request: Request, symbol: str = "", regime: str = ""):
    """ดึง Top patterns ที่กำไรดีที่สุด — ใช้สำหรับ dashboard."""
    memory = getattr(request.app.state, "memory", None)

    if not memory:
        return {"status": "no_memory", "data": []}

    try:
        data = memory.get_best_patterns(
            symbol=symbol or None,
            regime=regime or None,
            min_trades=5,
            top_n=10,
        )
        return {
            "status": "ok",
            "count": len(data),
            "data": data,
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "data": []}


@router.get("/brain/patterns/live")
async def get_live_patterns(request: Request):
    """ดึง patterns ที่ตรวจพบตอนนี้จาก PatternDetector (real-time)."""
    loop = getattr(request.app.state, "master_loop", None)

    if not loop:
        return {"status": "no_loop", "data": {}}

    pattern_signals = getattr(loop, "_pattern_signals", {})

    result = {}
    for symbol, signals in pattern_signals.items():
        result[symbol] = [
            {
                "name": s.name,
                "direction": s.direction,
                "strength": s.strength,
                "bar_index": s.bar_index,
            }
            for s in signals
        ]

    return {
        "status": "ok",
        "symbols": len(result),
        "data": result,
    }

