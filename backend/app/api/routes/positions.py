"""
Positions API — จัดการ positions ที่เปิดอยู่ (SL/TP/Trailing/Close).

Endpoints:
    GET  /api/positions              — ดึง positions ทั้งหมด
    POST /api/positions/{ticket}/sl  — แก้ SL
    POST /api/positions/{ticket}/tp  — แก้ TP
    POST /api/positions/{ticket}/trailing   — ตั้ง trailing stop
    POST /api/positions/{ticket}/profit-lock — ตั้ง profit lock
    POST /api/positions/{ticket}/close       — ปิด position
"""

from fastapi import APIRouter, HTTPException, Request

from app.core.logging import get_logger
from app.api.schemas import (
    ModifySLRequest, ModifyTPRequest, TrailingStopRequest,
    ProfitLockRequest, GhostGuardRequest, PositionResponse,
)
from app.risk.trade_manager import TrailingConfig, ProfitLockConfig, ProfitLockTier

logger = get_logger(__name__)
router = APIRouter()


# ─── GET /api/positions — ดึง positions ทั้งหมด ───

@router.get("/positions", response_model=list[PositionResponse])
async def list_positions(request: Request):
    """ดึง positions ทั้งหมดจาก MT5 พร้อม trailing/profit-lock status."""
    mt5 = getattr(request.app.state, "mt5", None)
    if not mt5 or not mt5.is_connected():
        raise HTTPException(status_code=503, detail="MT5 not connected")

    positions = mt5.get_positions()
    loop = getattr(request.app.state, "master_loop", None)

    result = []
    for pos in positions:
        ticket = pos["ticket"]

        # ─── Trailing status ───
        trailing_status = {"active": False, "mode": "off"}
        profit_lock_status = {"active": False, "current_tier": -1}

        # ─── Ghost Guard status ───
        ghost_status = {"active": False, "virtual_sl": 0.0, "virtual_tp": 0.0}

        if loop:
            if hasattr(loop, "trailing_manager"):
                trailing_status = loop.trailing_manager.get_status(ticket)
            if hasattr(loop, "profit_lock_manager"):
                profit_lock_status = loop.profit_lock_manager.get_status(ticket)
            if hasattr(loop, "ghost_guard"):
                ghost_status = loop.ghost_guard.get_status(ticket)

        result.append(PositionResponse(
            ticket=ticket,
            symbol=pos["symbol"],
            type=pos["type"],
            volume=pos["volume"],
            entry_price=pos["price_open"],
            current_price=pos["price_current"],
            sl=pos["sl"],
            tp=pos["tp"],
            profit=pos["profit"],
            magic=pos.get("magic", -1),
            trailing_active=trailing_status.get("active", False),
            trailing_mode=trailing_status.get("mode", "off"),
            profit_lock_tier=profit_lock_status.get("current_tier", -1),
            ghost_active=ghost_status.get("active", False),
            ghost_sl=ghost_status.get("virtual_sl", 0.0),
            ghost_tp=ghost_status.get("virtual_tp", 0.0),
        ))

    return result


# ─── POST /api/positions/{ticket}/sl — แก้ SL ───

@router.post("/positions/{ticket}/sl")
async def modify_sl(ticket: int, body: ModifySLRequest, request: Request):
    """แก้ Stop Loss ของ position."""
    mt5 = getattr(request.app.state, "mt5", None)
    if not mt5 or not mt5.is_connected():
        raise HTTPException(status_code=503, detail="MT5 not connected")

    success = mt5.modify_sl(ticket, body.sl)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to modify SL for ticket {ticket}")

    logger.info("api_modify_sl", extra={
        "ticket": ticket, "new_sl": body.sl, "stage": "api", "result": "ok",
    })
    return {"ticket": ticket, "sl": body.sl, "status": "ok"}


# ─── POST /api/positions/{ticket}/tp — แก้ TP ───

@router.post("/positions/{ticket}/tp")
async def modify_tp(ticket: int, body: ModifyTPRequest, request: Request):
    """แก้ Take Profit ของ position."""
    mt5 = getattr(request.app.state, "mt5", None)
    if not mt5 or not mt5.is_connected():
        raise HTTPException(status_code=503, detail="MT5 not connected")

    # modify_sl รองรับ new_tp parameter อยู่แล้ว
    # ดึง SL ปัจจุบันก่อน แล้วส่งพร้อม TP ใหม่
    positions = mt5.get_positions()
    current_sl = 0.0
    for pos in positions:
        if pos["ticket"] == ticket:
            current_sl = pos["sl"]
            break
    else:
        raise HTTPException(status_code=404, detail=f"Position {ticket} not found")

    success = mt5.modify_sl(ticket, current_sl, body.tp)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to modify TP for ticket {ticket}")

    logger.info("api_modify_tp", extra={
        "ticket": ticket, "new_tp": body.tp, "stage": "api", "result": "ok",
    })
    return {"ticket": ticket, "tp": body.tp, "status": "ok"}


# ─── POST /api/positions/{ticket}/trailing — ตั้ง trailing stop ───

@router.post("/positions/{ticket}/trailing")
async def set_trailing(ticket: int, body: TrailingStopRequest, request: Request):
    """ตั้ง/ปิด trailing stop สำหรับ position."""
    loop = getattr(request.app.state, "master_loop", None)
    if not loop or not hasattr(loop, "trailing_manager"):
        raise HTTPException(status_code=503, detail="Trading system not ready")

    if body.mode == "off":
        loop.trailing_manager.remove_trailing(ticket)
        return {"ticket": ticket, "trailing": "off", "status": "ok"}

    config = TrailingConfig(
        mode=body.mode,
        atr_multiplier=body.atr_multiplier,
        fixed_points=body.fixed_points,
        activation_r=body.activation_r,
    )
    loop.trailing_manager.set_trailing(ticket, config)

    logger.info("api_set_trailing", extra={
        "ticket": ticket, "mode": body.mode, "stage": "api", "result": "ok",
    })
    return {"ticket": ticket, "trailing": body.mode, "status": "ok"}


# ─── POST /api/positions/{ticket}/profit-lock — ตั้ง profit lock ───

@router.post("/positions/{ticket}/profit-lock")
async def set_profit_lock(ticket: int, body: ProfitLockRequest, request: Request):
    """ตั้ง profit lock tiers สำหรับ position."""
    loop = getattr(request.app.state, "master_loop", None)
    if not loop or not hasattr(loop, "profit_lock_manager"):
        raise HTTPException(status_code=503, detail="Trading system not ready")

    tiers = [
        ProfitLockTier(r_multiple=t.r, lock_pct=t.lock_pct)
        for t in body.tiers
    ]
    config = ProfitLockConfig(tiers=tiers)
    loop.profit_lock_manager.set_profit_lock(ticket, config)

    logger.info("api_set_profit_lock", extra={
        "ticket": ticket, "tiers": len(tiers), "stage": "api", "result": "ok",
    })
    return {"ticket": ticket, "tiers": len(tiers), "status": "ok"}


# ─── POST /api/positions/{ticket}/close — ปิด position ───

@router.post("/positions/{ticket}/close")
async def close_position(ticket: int, request: Request):
    """ปิด position ด้วย ticket number."""
    mt5 = getattr(request.app.state, "mt5", None)
    if not mt5 or not mt5.is_connected():
        raise HTTPException(status_code=503, detail="MT5 not connected")

    success = mt5.close_position(ticket, reason="API_CLOSE")
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to close position {ticket}")

    # ─── Cleanup trailing/profit-lock configs ───
    loop = getattr(request.app.state, "master_loop", None)
    if loop:
        if hasattr(loop, "trailing_manager"):
            loop.trailing_manager.remove_trailing(ticket)
        if hasattr(loop, "profit_lock_manager"):
            loop.profit_lock_manager.remove_profit_lock(ticket)

    logger.info("api_close_position", extra={
        "ticket": ticket, "stage": "api", "result": "ok",
    })
    return {"ticket": ticket, "status": "closed"}


# ─── POST /api/positions/{ticket}/ghost — ตั้ง Ghost Guard (Virtual SL/TP) ───

@router.post("/positions/{ticket}/ghost")
async def set_ghost_guard(ticket: int, body: GhostGuardRequest, request: Request):
    """
    ตั้ง Ghost Guard — Virtual SL/TP ที่ซ่อนจากโบรกเกอร์.

    Bot จะเก็บ SL/TP ไว้ใน memory แทน → โบรกเกอร์ไม่เห็น
    เมื่อราคาถึง → ปิดด้วย market order ทันที

    แนะนำ: ตั้ง server SL ไว้กว้างเป็น safety net ด้วย
    """
    loop = getattr(request.app.state, "master_loop", None)
    if not loop or not hasattr(loop, "ghost_guard"):
        raise HTTPException(status_code=503, detail="Trading system not ready")

    if body.virtual_sl <= 0 and body.virtual_tp <= 0:
        loop.ghost_guard.remove_ghost(ticket)
        return {"ticket": ticket, "ghost": "off", "status": "ok"}

    loop.ghost_guard.set_ghost(ticket, body.virtual_sl, body.virtual_tp)

    logger.info("api_set_ghost", extra={
        "ticket": ticket,
        "virtual_sl": body.virtual_sl,
        "virtual_tp": body.virtual_tp,
        "stage": "api",
        "result": "ok",
    })
    return {
        "ticket": ticket,
        "ghost": "on",
        "virtual_sl": body.virtual_sl,
        "virtual_tp": body.virtual_tp,
        "status": "ok",
    }
