"""
Shadow API — ดู shadow trades + เปรียบเทียบ LIVE vs Shadow.

Endpoints:
    GET /api/shadow/trades   — shadow trades ล่าสุด
    GET /api/shadow/compare  — เปรียบเทียบ signal count / confidence
"""

from fastapi import APIRouter, Request, Query

router = APIRouter(prefix="/shadow", tags=["Shadow"])


@router.get("/trades")
async def get_shadow_trades(
    request: Request,
    symbol: str | None = Query(None, description="Filter by symbol"),
    strategy: str | None = Query(None, description="Filter by strategy name"),
    limit: int = Query(50, ge=1, le=500),
):
    """ดึง shadow trades ล่าสุด."""
    db = request.app.state.db
    if not db:
        return {"trades": [], "total": 0}

    trades = db.get_shadow_trades(symbol=symbol, strategy=strategy, limit=limit)
    return {"trades": trades, "total": len(trades)}


@router.get("/compare")
async def compare_shadow_vs_live(
    request: Request,
    symbol: str | None = Query(None, description="Filter by symbol"),
    hours: int = Query(24, ge=1, le=168, description="Look back N hours"),
):
    """
    เปรียบเทียบ LIVE strategy vs Shadow strategies.

    Returns:
        per-strategy: signal count, avg confidence, action distribution
    """
    db = request.app.state.db
    if not db:
        return {"live": {}, "shadow": [], "hours": hours}

    shadow_stats = db.get_shadow_analytics(symbol=symbol, hours=hours)

    # LIVE stats จาก decision_traces
    live_stats = db.get_live_signal_stats(symbol=symbol, hours=hours)

    return {
        "live": live_stats,
        "shadow": shadow_stats,
        "hours": hours,
        "symbol": symbol,
    }
