"""
Learning API — Online Learning & Web Knowledge endpoints.

Endpoints:
    GET  /api/learning/status           — สถานะระบบเรียนรู้
    GET  /api/learning/insights         — Insights ที่เรียนรู้ได้
    GET  /api/learning/performance      — Performance summary
    GET  /api/learning/knowledge        — ข้อมูลตลาดจาก internet
    GET  /api/learning/sentiment        — Sentiment overview
    GET  /api/learning/adaptive-params  — Adaptive parameters
    GET  /api/learning/best-hours       — ช่วงเวลาเทรดดีที่สุด
    POST /api/learning/refresh          — Force refresh web knowledge
"""

from fastapi import APIRouter, Query
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/status")
async def get_learning_status():
    """Get status of all learning systems."""
    from app.brain.online_learner import OnlineLearner
    from app.brain.web_researcher import WebResearcher
    # Lazy-import singletons from app state or create fresh
    learner = _get_learner()
    researcher = _get_researcher()
    return {
        "learner": learner.get_status(),
        "researcher": researcher.get_status(),
        "status": "active",
    }


@router.get("/insights")
async def get_learning_insights():
    """Get latest learning insights (what the bot learned)."""
    learner = _get_learner()
    return {
        "insights": learner.get_learning_insights(),
        "count": len(learner.insights),
    }


@router.get("/performance")
async def get_learning_performance():
    """Get overall learning performance summary."""
    learner = _get_learner()
    return learner.get_performance_summary()


@router.get("/knowledge")
async def get_market_knowledge(symbol: Optional[str] = Query(None)):
    """Get cached market knowledge from internet."""
    researcher = _get_researcher()
    try:
        if symbol:
            knowledge = await researcher.search_market_knowledge(symbol)
            return knowledge.to_dict()
        else:
            return researcher.get_knowledge_summary()
    except Exception as e:
        logger.warning("knowledge_fetch_error", extra={"error": str(e)})
        return {"error": str(e), "articles": [], "overall_sentiment": 0}


@router.get("/sentiment")
async def get_sentiment_overview():
    """Get global market sentiment from internet sources."""
    researcher = _get_researcher()
    return researcher.get_knowledge_summary()


@router.get("/adaptive-params")
async def get_adaptive_params(symbol: Optional[str] = Query(None)):
    """Get adaptive trading parameters learned from trades."""
    learner = _get_learner()
    if symbol:
        params = learner.get_adaptive_params(symbol)
        return {"symbol": symbol, "params": params}
    else:
        return {"all_params": learner.adaptive_params}


@router.get("/best-hours")
async def get_best_hours(symbol: Optional[str] = Query("ALL")):
    """Get best trading hours based on learned performance."""
    learner = _get_learner()
    return {
        "symbol": symbol,
        "best_hours": learner.get_best_trading_hours(symbol or "ALL"),
    }


@router.get("/strategy-recommendation")
async def get_strategy_recommendation(symbol: str = Query(...)):
    """Get strategy change recommendation if needed."""
    learner = _get_learner()
    recommendation = learner.should_adjust_strategy(symbol)
    return {
        "symbol": symbol,
        "has_recommendation": recommendation is not None,
        "recommendation": recommendation,
    }


@router.post("/refresh")
async def refresh_knowledge(symbol: Optional[str] = Query(None)):
    """Force refresh internet knowledge for a symbol."""
    researcher = _get_researcher()
    target = symbol or "XAUUSD"
    knowledge = await researcher.search_market_knowledge(target)
    return {
        "status": "refreshed",
        "symbol": target,
        "articles": len(knowledge.articles),
        "sentiment": knowledge.overall_sentiment,
        "direction": knowledge.consensus_direction,
    }


# ─── Module-level singletons (lazy init) ───
_learner_instance = None
_researcher_instance = None


def _get_learner():
    global _learner_instance
    if _learner_instance is None:
        from app.brain.online_learner import OnlineLearner
        _learner_instance = OnlineLearner()
    return _learner_instance


def _get_researcher():
    global _researcher_instance
    if _researcher_instance is None:
        from app.brain.web_researcher import WebResearcher
        _researcher_instance = WebResearcher()
    return _researcher_instance
