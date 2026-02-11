"""
Analytics Route — ดึง performance metrics.

Metrics:
    win_rate, profit_factor, max_dd, expectancy, avg_R, Sharpe
    แยกตาม session / symbol / regime
"""

from fastapi import APIRouter, Query

from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/analytics/metrics")
async def get_metrics(
    symbol: str = Query(default=None, description="กรองตามสัญลักษณ์"),
):
    """ดึง performance metrics — PF, DD, Sharpe, win rate."""
    # TODO: คำนวณผ่าน DuckDB
    return {
        "symbol": symbol or "ALL",
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "expectancy": 0.0,
        "avg_r": 0.0,
        "sharpe_ratio": 0.0,
        "total_trades": 0,
    }
