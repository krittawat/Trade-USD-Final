"""
WebSocket Status — Real-time bot status push ทุก 3 วินาที.

ไม่ต้อง poll — server push ข้อมูลมาให้ automatically:
    - bot alive / mode / cycle
    - per-symbol: trend, strategy, action, confidence, pressure
    - positions count + floating P&L

Endpoint:
    WS /ws/status — WebSocket real-time status stream

การใช้งาน:
    const ws = new WebSocket("ws://localhost:8000/ws/status");
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        console.log(data);
    };
"""

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Push interval (seconds)
WS_PUSH_INTERVAL = 3.0


def _build_live_snapshot(master_loop, settings) -> dict:
    """
    สร้าง snapshot เบาๆ จาก memory (ไม่ query DB/MT5).

    ใช้ร่วมกับ WebSocket push และ /status/live.
    """
    if not master_loop:
        return {
            "bot_alive": False,
            "mode": settings.trading_mode,
            "symbols": {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    symbols_data = {}
    for sym, dec in getattr(master_loop, "last_decisions", {}).items():
        if isinstance(dec, dict):
            sym_info = {
                "action": dec.get("action", "HOLD"),
                "confidence": round(dec.get("confidence", 0), 3),
                "strategy": dec.get("strategy", ""),
                "regime": dec.get("regime", ""),
                "session": dec.get("session", ""),
                "result": dec.get("result", ""),
                "reason": dec.get("reason", ""),
            }
        else:
            sym_info = {
                "action": getattr(dec, "action", "HOLD"),
                "confidence": round(getattr(dec, "confidence", 0), 3),
                "strategy": getattr(dec, "strategy_name", ""),
                "regime": "",
                "session": "",
                "result": "",
                "reason": getattr(dec, "reason", ""),
            }

        # Trend จาก regime_contexts
        ctx = getattr(master_loop, "regime_contexts", {}).get(sym)
        if ctx:
            sym_info["trend"] = ctx.regime.value
            sym_info["trend_score"] = round(ctx.score, 3)
            sym_info["actionable"] = ctx.actionable
        else:
            sym_info["trend"] = "UNKNOWN"
            sym_info["trend_score"] = 0.0
            sym_info["actionable"] = False

        # Tick volume pressure summary
        tv_sig = getattr(master_loop, "_tick_volume_signals", {}).get(sym)
        if tv_sig and getattr(tv_sig, "is_valid", False):
            sym_info["pressure"] = "BUY" if tv_sig.buying_pressure > tv_sig.selling_pressure else "SELL"
            sym_info["pressure_score"] = round(tv_sig.score, 3)
        else:
            sym_info["pressure"] = "NEUTRAL"
            sym_info["pressure_score"] = 0.0

        symbols_data[sym] = sym_info

    # Positions count จาก MT5 (ผ่าน adapter ถ้ามี)
    positions_count = 0
    floating_pnl = 0.0
    try:
        if hasattr(master_loop, "adapter") and master_loop.adapter:
            acct = master_loop.adapter.get_account_state()
            positions_count = acct.open_positions
            floating_pnl = round(acct.equity - acct.balance, 2)
    except Exception:
        pass

    return {
        "bot_alive": getattr(master_loop, "running", False),
        "mode": settings.trading_mode,
        "cycle": getattr(master_loop, "cycle_count", 0),
        "kill_switch": getattr(master_loop, "kill_switch", False),
        "positions_count": positions_count,
        "floating_pnl": floating_pnl,
        "symbols": symbols_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    """
    WebSocket Real-time Status Stream.

    เชื่อมต่อแล้ว push สถานะทุก 3 วินาทีจนกว่า client จะ disconnect.
    ข้อมูลเหมือนกับ GET /status/live แต่ push มาเอง ไม่ต้อง poll.
    """
    await websocket.accept()
    settings = get_settings()
    logger.info("ws_status_connected", extra={
        "client": websocket.client.host if websocket.client else "unknown",
    })

    try:
        while True:
            master_loop = getattr(websocket.app.state, "master_loop", None)
            snapshot = _build_live_snapshot(master_loop, settings)

            # ส่ง JSON snapshot
            await websocket.send_text(json.dumps(
                jsonable_encoder(snapshot),
                ensure_ascii=False,
            ))

            await asyncio.sleep(WS_PUSH_INTERVAL)

    except WebSocketDisconnect:
        logger.info("ws_status_disconnected", extra={
            "client": websocket.client.host if websocket.client else "unknown",
        })
    except Exception as e:
        logger.debug("ws_status_error", extra={"error": str(e)})
        try:
            await websocket.close()
        except Exception:
            pass
