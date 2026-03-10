"""
Analytics Route — performance metrics จริงจาก SQLite trade journal + AI Brain.

Endpoints:
    GET /api/analytics/metrics  — win_rate, PF, max_dd, expectancy (จาก trade journal)
    GET /api/analytics/daily    — สถิติประจำวัน (P&L + trade count)
    GET /api/analytics/brain    — AI Brain performance summary ต่อ strategy
"""

from fastapi import APIRouter, Query, Request

from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/analytics/metrics")
async def get_metrics(
    request: Request,
    symbol: str = Query(default=None, description="filter by symbol"),
):
    """
    ดึง performance metrics จาก trade journal (SQLite).

    Metrics:
        win_rate, profit_factor, max_drawdown, expectancy,
        avg_r, sharpe_ratio, total_trades
    """
    try:
        db = getattr(request.app.state, "db", None)
        if db:
            metrics = db.get_analytics(symbol=symbol)
            metrics["symbol"] = symbol or "ALL"
            return metrics
    except Exception as e:
        logger.error("analytics_error", extra={"error": str(e)})

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


@router.get("/analytics/daily")
async def get_daily_stats(request: Request):
    """
    ดึงสถิติประจำวัน — P&L + จำนวนเทรดวันนี้.
    """
    try:
        db = getattr(request.app.state, "db", None)
        if db:
            daily_pl = db.get_daily_pl()
            trade_count = db.get_trade_count_today()
            return {
                "daily_pl": round(daily_pl, 2),
                "trade_count": trade_count,
            }
    except Exception as e:
        logger.error("daily_analytics_error", extra={"error": str(e)})
    return {"daily_pl": 0.0, "trade_count": 0}


@router.get("/analytics/brain")
async def get_brain_performance(
    request: Request,
    symbol: str = Query(default="", description="filter by symbol (ว่าง = ทั้งหมด)"),
):
    """
    AI Brain — สรุป performance ทุก strategy จาก Brain Memory.

    แสดง:
        - strategy_name, symbol, regime, session
        - win_rate, profit_factor, avg_rr, total_trades
        - เรียงตาม score (PF × WR) จากมากไปน้อย

    ใช้สำหรับ Dashboard แสดงว่า Brain เรียนรู้อะไรมาบ้าง.
    """
    try:
        memory = getattr(request.app.state, "memory", None)
        if memory:
            summary = memory.get_performance_summary(symbol=symbol)
            return {
                "brain_status": "connected",
                "strategies": summary,
                "count": len(summary),
            }
    except Exception as e:
        logger.error("brain_analytics_error", extra={"error": str(e)})

    return {
        "brain_status": "disconnected",
        "strategies": [],
        "count": 0,
    }


@router.get("/analytics/symbol-tuner/latest")
async def get_symbol_tuner_latest(
    symbol: str = Query(default="", description="filter by symbol (e.g. XAUUSD)"),
):
    """
    ดึง snapshot ล่าสุดรายคู่จาก symbol_tuner_snapshots
    (win_rate + auto-tuned thresholds).
    """
    try:
        from backend.trader.data.mapper import mapper
        from backend.trader.storage.sqlite_db import db as trader_db

        sym = (symbol or "").strip()
        std_symbol = mapper.to_standard(sym) if sym else ""
        rows = trader_db.get_latest_symbol_tuner_snapshots(symbol=std_symbol or None)
        return {
            "count": len(rows),
            "symbol": std_symbol or "ALL",
            "snapshots": rows,
        }
    except Exception as e:
        logger.error("symbol_tuner_analytics_error", extra={"error": str(e)})
        return {
            "count": 0,
            "symbol": (symbol or "ALL"),
            "snapshots": [],
            "error": str(e),
        }
