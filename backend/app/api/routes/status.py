"""
Status Route — เช็คสถานะบอทแบบเร็วใน call เดียว.

รวมข้อมูลจาก health, positions, decisions, master_loop ไว้ในที่เดียว:
    - bot alive / mode / uptime / kill-switch
    - account: balance, equity, floating P&L
    - services: MT5, SQLite, Brain (quick check)
    - open positions + total floating P&L
    - last decisions per symbol (ทำไมไม่เทรด?)
    - trend analysis per symbol (ตลาดกำลังไปทางไหน?)
    - active strategy per symbol (กลยุทธ์อะไรกำลังใช้?)
    - symbols tracked

Endpoints:
    GET /api/status      — Full bot status (ข้อมูลครบ)
    GET /api/status/live  — Lightweight real-time snapshot (เร็ว, ไม่ query DB)
"""

from datetime import datetime, timezone
import traceback as _traceback

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

_start_time = datetime.now(timezone.utc)


def _format_uptime(seconds: float) -> str:
    """แปลง seconds → human-readable uptime string."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"



@router.get("/status")
async def bot_status(request: Request):
    """
    Quick Bot Status — ดูสถานะบอทได้เร็วใน call เดียว.

    Response:
        - bot_alive: บอทยังทำงานอยู่ไหม
        - mode: LIVE / DRY_RUN
        - uptime: เวลาที่ทำงาน
        - kill_switch: ถูกกด kill-switch หรือยัง
        - account: balance, equity, floating P&L
        - services: สถานะ services ที่สำคัญ
        - positions: positions ที่เปิดอยู่
        - last_decisions: decision ล่าสุดของแต่ละ symbol
        - symbols_tracked: symbols ที่กำลังติดตาม
    """
    settings = get_settings()
    try:
        return await _bot_status_impl(request, settings)
    except Exception as e:
        # Return traceback as JSON for debugging
        tb = _traceback.format_exc()
        logger.error("bot_status_error", extra={"error": str(e), "traceback": tb})
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "traceback": tb.split("\n")},
        )


async def _bot_status_impl(request: Request, settings):
    uptime_sec = (datetime.now(timezone.utc) - _start_time).total_seconds()
    master_loop = getattr(request.app.state, "master_loop", None)
    loop_info = {}
    kill_switch = False
    symbols_tracked = []
    last_decisions = {}

    if master_loop:
        kill_switch = getattr(master_loop, "kill_switch", False)
        loop_info = {
            "cycle_count": getattr(master_loop, "cycle_count", 0),
            "running": getattr(master_loop, "running", False),
        }
        symbols_tracked = list(getattr(master_loop, "last_decisions", {}).keys())

        # Last decisions per symbol (ข้อมูลครบ: strategy, regime, session, SL/TP)
        for sym, dec in getattr(master_loop, "last_decisions", {}).items():
            if isinstance(dec, dict):
                last_decisions[sym] = {
                    "action": dec.get("action", "?"),
                    "confidence": dec.get("confidence", 0),
                    "reason": dec.get("reason", ""),
                    "result": dec.get("result", ""),
                    "strategy": dec.get("strategy", ""),
                    "regime": dec.get("regime", ""),
                    "session": dec.get("session", ""),
                    "sl": dec.get("sl"),
                    "tp": dec.get("tp"),
                    "cycle": dec.get("cycle", 0),
                    "strategies_tried": dec.get("strategies_tried", []),
                }
            else:
                # Decision object
                last_decisions[sym] = {
                    "action": getattr(dec, "action", "?"),
                    "confidence": getattr(dec, "confidence", 0),
                    "reason": getattr(dec, "reason", ""),
                    "result": getattr(dec, "result", ""),
                    "strategy": getattr(dec, "strategy_name", ""),
                    "regime": getattr(dec, "regime", ""),
                    "session": getattr(dec, "session", ""),
                    "sl": getattr(dec, "stop_loss", None),
                    "tp": getattr(dec, "take_profit", None),
                    "cycle": 0,
                    "strategies_tried": [],
                }
    
        # Regime Status
        regimes = {}
        for sym, ctx in getattr(master_loop, "regime_contexts", {}).items():
            try:
                safe_details = {}
                if ctx.details and isinstance(ctx.details, dict):
                    try:
                        safe_details = jsonable_encoder(ctx.details)
                    except Exception:
                        safe_details = {k: str(v) for k, v in ctx.details.items()}
                regimes[sym] = {
                    "regime": ctx.regime.value if hasattr(ctx.regime, "value") else str(ctx.regime),
                    "actionable": bool(ctx.actionable),
                    "score": float(ctx.score),
                    "details": safe_details,
                }
            except Exception:
                regimes[sym] = {"error": "serialization_failed"}
        
        # Risk Metrics (Phase D)
        risk_metrics = {}
        coach_report = getattr(master_loop, "latest_coach_report", None)
        if coach_report and "performance_summary" in coach_report:
            perf = coach_report["performance_summary"]
            risk_metrics = {
                "loss_streak": perf.get("current_loss_streak", 0),
                "win_streak": perf.get("current_win_streak", 0),
                "max_dd_pct": perf.get("max_drawdown_pct", 0.0),
            }

    # ─── MT5 Account (fast — single call) ───
    mt5_status = "disconnected"
    account_info = {}
    floating_pnl = 0.0
    positions_list = []

    try:
        import MetaTrader5 as mt5
        term = mt5.terminal_info()
        if term and term.connected:
            mt5_status = "connected"
            info = mt5.account_info()
            if info:
                floating_pnl = round(info.equity - info.balance, 2)
                account_info = {
                    "balance": info.balance,
                    "equity": info.equity,
                    "margin": info.margin,
                    "free_margin": info.margin_free,
                    "floating_pnl": floating_pnl,
                    "leverage": info.leverage,
                }

            # Open positions
            raw_positions = mt5.positions_get()
            if raw_positions:
                for p in raw_positions:
                    positions_list.append({
                        "ticket": p.ticket,
                        "symbol": p.symbol,
                        "type": "buy" if p.type == 0 else "sell",
                        "volume": p.volume,
                        "entry_price": p.price_open,
                        "current_price": p.price_current,
                        "sl": p.sl,
                        "tp": p.tp,
                        "profit": round(p.profit, 2),
                        "swap": round(p.swap, 2),
                    })
    except Exception as e:
        mt5_status = f"error: {str(e)[:50]}"

    total_floating = round(sum(p["profit"] for p in positions_list), 2) if positions_list else 0.0

    # ─── Services quick check (from app.state — no I/O) ───
    brain_status = "disconnected"
    memory = getattr(request.app.state, "memory", None)
    if memory and getattr(memory, "_conn", None):
        brain_status = "connected"

    sqlite_status = "connected"
    db = getattr(request.app.state, "db", None)
    if not db or not getattr(db, "_conn", None):
        sqlite_status = "disconnected"

    factory = getattr(request.app.state, "factory", None)
    try:
        strategies_count = len(factory._strategies) if factory else 0
    except Exception:
        strategies_count = 0

    # --- Tick snapshot (SQLite) ---
    ticks_latest = {}
    if db and getattr(db, "_conn", None):
        try:
            query_symbols = symbols_tracked or [s.strip() for s in settings.trading_symbols.split(",") if s.strip()]
            latest_by_symbol = db.get_latest_ticks(query_symbols)
            for sym in query_symbols:
                row = latest_by_symbol.get(sym)
                if row:
                    ticks_latest[sym] = {
                        "ts_latest": row.get("ts"),
                        "volume_tick": int(row.get("volume", 0) or 0),
                        "status": "ok",
                    }
                else:
                    ticks_latest[sym] = {"ts_latest": None, "volume_tick": None, "status": "no_data"}
        except Exception:
            pass

    # ─── Today's trade stats (from SQLite — fast query) ───
    today_stats = {}
    if db and getattr(db, "_conn", None):
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            cursor = db._conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE date(timestamp) = ? AND result = 'ok'",
                (today,)
            )
            row = cursor.fetchone()
            today_stats["signals_today"] = row[0] if row else 0

            cursor = db._conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE date(timestamp) = ? AND result = 'blocked'",
                (today,)
            )
            row = cursor.fetchone()
            today_stats["blocked_today"] = row[0] if row else 0
        except Exception:
            pass

    # ─── Trend Analysis per symbol (จาก regime_contexts + regime_intel) ───
    trend_analysis = {}
    if master_loop:
        try:
            for sym, ctx in getattr(master_loop, "regime_contexts", {}).items():
                try:
                    trend_info = {
                        "regime": ctx.regime.value if hasattr(ctx.regime, "value") else str(ctx.regime),
                        "actionable": bool(ctx.actionable),
                        "score": round(float(ctx.score), 3),
                        "reason": str(ctx.reason or ""),
                    }
                    # เพิ่ม indicator details จาก RegimeContext (safe serialize)
                    if ctx.details and isinstance(ctx.details, dict):
                        try:
                            trend_info["indicators"] = jsonable_encoder(ctx.details)
                        except Exception:
                            trend_info["indicators"] = {k: str(v) for k, v in ctx.details.items()}

                    # เพิ่ม intelligence จาก RegimeIntelligence (ถ้ามี)
                    intel_map = getattr(master_loop, "_regime_intel", {})
                    intel = intel_map.get(sym)
                    if intel:
                        direction_val = getattr(intel, "direction", "")
                        trend_info["direction"] = direction_val.value if hasattr(direction_val, "value") else str(direction_val)
                        trend_info["confidence"] = round(float(getattr(intel, "confidence", 0.5)), 3)
                        trend_info["recommended_strategy"] = str(getattr(intel, "recommended_strategy", ""))
                        trend_info["trade_allowed"] = bool(getattr(intel, "trade_allowed", True))
                        rp = getattr(intel, "risk_profile", None)
                        if rp and isinstance(rp, dict):
                            try:
                                trend_info["risk_profile"] = jsonable_encoder(rp)
                            except Exception:
                                trend_info["risk_profile"] = {k: str(v) for k, v in rp.items()}

                    trend_analysis[sym] = trend_info
                except Exception:
                    trend_analysis[sym] = {"error": "serialization_failed"}
        except Exception:
            pass

    # ─── Active Strategies per symbol ───
    active_strategies = {}
    if master_loop:
        try:
            for sym, dec in getattr(master_loop, "last_decisions", {}).items():
                strat_name = dec.get("strategy", "") if isinstance(dec, dict) else getattr(dec, "strategy_name", "")
                active_strategies[sym] = {
                    "strategy": str(strat_name),
                    "action": str(dec.get("action", "HOLD") if isinstance(dec, dict) else getattr(dec, "action", "HOLD")),
                    "confidence": round(float(dec.get("confidence", 0) if isinstance(dec, dict) else getattr(dec, "confidence", 0)), 3),
                    "candidates": list(dec.get("strategies_tried", []) if isinstance(dec, dict) else []),
                }
        except Exception:
            pass

    # ─── Tick Volume Signals per symbol ───
    tick_volume_data = {}
    if master_loop:
        try:
            for sym, sig in getattr(master_loop, "_tick_volume_signals", {}).items():
                try:
                    if sig and getattr(sig, "is_valid", False):
                        tick_volume_data[sym] = {
                            "score": round(float(sig.score), 3),
                            "volume_trend": str(sig.volume_trend),
                            "buying_pressure": round(float(sig.buying_pressure), 3),
                            "selling_pressure": round(float(sig.selling_pressure), 3),
                            "is_climax": bool(sig.is_climax),
                            "is_dryup": bool(sig.is_dryup),
                            "has_divergence": bool(sig.has_divergence),
                            "body_conviction": round(float(sig.body_conviction), 3),
                            "ad_line_trend": str(sig.ad_line_trend),
                        }
                except Exception:
                    pass
        except Exception:
            pass

    # ─── Candlestick Patterns per symbol ───
    patterns = {}
    if master_loop:
        try:
            for sym, sigs in getattr(master_loop, "_pattern_signals", {}).items():
                try:
                    if sigs:
                        patterns[sym] = [
                            {
                                "name": str(s.name),
                                "direction": str(s.direction),
                                "strength": round(float(s.strength), 3),
                            }
                            for s in sigs[:5]  # จำกัด 5 patterns ล่าสุด
                        ]
                except Exception:
                    pass
        except Exception:
            pass

    payload = {
        "bot_alive": True,
        "mode": settings.trading_mode,
        "uptime": _format_uptime(uptime_sec),
        "uptime_seconds": round(uptime_sec, 1),
        "kill_switch": kill_switch,

        "account": account_info,

        "services": {
            "mt5": mt5_status,
            "sqlite": sqlite_status,
            "brain": brain_status,
        },
        "strategies_registered": strategies_count,

        "positions": positions_list,
        "positions_count": len(positions_list),
        "total_floating_pnl": total_floating,

        "last_decisions": last_decisions,
        "regimes": regimes,
        "risk_metrics": risk_metrics,
        "symbols_tracked": symbols_tracked,
        "ticks_latest": ticks_latest,

        # ─── ข้อมูลใหม่: Trend + Strategy + Microstructure ───
        "trend_analysis": trend_analysis,
        "active_strategies": active_strategies,
        "tick_volume": tick_volume_data,
        "patterns": patterns,

        "today": today_stats,
        "master_loop": loop_info,

        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return JSONResponse(content=jsonable_encoder(payload))


# ====================================================================
# /status/live — Lightweight Real-time Snapshot (ไม่ query DB/MT5)
# ====================================================================

@router.get("/status/live")
async def bot_status_live(request: Request):
    """
    Lightweight Real-time Status — อ่านจาก memory อย่างเดียว (ไม่ query DB/MT5).

    ใช้สำหรับ frontend poll ทุก 2-3 วินาที.
    Response เบามาก — แค่ trend, strategy, regime per symbol.
    """
    settings = get_settings()
    master_loop = getattr(request.app.state, "master_loop", None)

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

        # เพิ่ม regime จาก regime_contexts
        ctx = getattr(master_loop, "regime_contexts", {}).get(sym)
        if ctx:
            sym_info["trend"] = ctx.regime.value
            sym_info["trend_score"] = round(ctx.score, 3)
            sym_info["actionable"] = ctx.actionable
        else:
            sym_info["trend"] = "UNKNOWN"
            sym_info["trend_score"] = 0.0
            sym_info["actionable"] = False

        # เพิ่ม tick volume summary
        tv_sig = getattr(master_loop, "_tick_volume_signals", {}).get(sym)
        if tv_sig and getattr(tv_sig, "is_valid", False):
            sym_info["pressure"] = "BUY" if tv_sig.buying_pressure > tv_sig.selling_pressure else "SELL"
            sym_info["pressure_score"] = round(tv_sig.score, 3)
        else:
            sym_info["pressure"] = "NEUTRAL"
            sym_info["pressure_score"] = 0.0

        symbols_data[sym] = sym_info

    return {
        "bot_alive": getattr(master_loop, "running", False),
        "mode": settings.trading_mode,
        "cycle": getattr(master_loop, "cycle_count", 0),
        "kill_switch": getattr(master_loop, "kill_switch", False),
        "symbols": symbols_data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/status/ticks")
async def latest_tick_volume(request: Request, symbols: str | None = None, limit: int = 10):
    """
    Latest tick snapshot from SQLite (includes tick volume).
    """
    settings = get_settings()
    db = getattr(request.app.state, "db", None)

    if symbols:
        wanted = [s.strip() for s in symbols.split(",") if s.strip()][:limit]
    else:
        wanted = [s.strip() for s in settings.trading_symbols.split(",") if s.strip()][:limit]

    try:
        if not db or not getattr(db, "_conn", None):
            return {"status": "error", "message": "SQLite not connected", "ticks": {}}

        latest_by_symbol = db.get_latest_ticks(wanted)
        ticks = {}
        for sym in wanted:
            row = latest_by_symbol.get(sym)
            if row:
                ticks[sym] = {
                    "ts_latest": row.get("ts"),
                    "bid": float(row.get("bid", 0.0) or 0.0),
                    "ask": float(row.get("ask", 0.0) or 0.0),
                    "last": float(row.get("last", 0.0) or 0.0),
                    "volume_tick": int(row.get("volume", 0) or 0),
                    "status": "ok",
                }
            else:
                ticks[sym] = {
                    "ts_latest": None, "bid": None, "ask": None,
                    "last": None, "volume_tick": None, "status": "no_data",
                }

        return {"status": "ok", "source": "sqlite", "count": len(ticks), "ticks": ticks}
    except Exception as e:
        logger.error("status_ticks_error", extra={"error": str(e)})
        return {"status": "error", "message": str(e), "ticks": {}}


from pydantic import BaseModel

class KillSwitchRequest(BaseModel):
    enabled: bool

@router.post("/status/kill-switch")
async def kill_switch_control(request: Request, body: KillSwitchRequest):
    """
    Emergency Kill Switch Control.
    
    Args:
        enabled (bool): True to STOP trading (Kill), False to RESUME.
    """
    master_loop = getattr(request.app.state, "master_loop", None)
    if not master_loop:
        return {"error": "MasterLoop not initialized"}
    
    master_loop.kill_switch = body.enabled
    
    logger.warning("kill_switch_toggled", extra={
        "enabled": body.enabled,
        "ip": request.client.host if request.client else "unknown"
    })
    
    return {
        "kill_switch": master_loop.kill_switch,
        "status": "STOPPED" if master_loop.kill_switch else "RUNNING",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ====================================================================
# Trade History — ประวัติออเดอร์ + วิเคราะห์ SL/TP
# ====================================================================

@router.get("/status/trades")
async def trade_history(
    request: Request,
    symbol: str | None = None,
    days: int = 7,
    limit: int = 100,
):
    """
    ประวัติเทรดจาก trade_journal (SQLite) — สำหรับวิเคราะห์ SL/TP.

    Query Params:
        symbol: กรองตาม symbol (เช่น EURUSDc)
        days: ย้อนหลังกี่วัน (default: 7)
        limit: จำนวนสูงสุด (default: 100)

    Response:
        - trades: รายการเทรดทั้งหมด
        - analysis: วิเคราะห์ SL/TP hit rate, avg R:R, win streaks ฯลฯ
        - per_symbol: สรุปแยกตาม symbol
    """
    db = getattr(request.app.state, "db", None)
    if not db or not getattr(db, "_conn", None):
        return {"trades": [], "analysis": {}, "error": "SQLite not connected"}

    try:
        cutoff = datetime.now(timezone.utc)
        from datetime import timedelta
        cutoff_str = (cutoff - timedelta(days=days)).isoformat()

        if symbol:
            rows = db._conn.execute(
                """SELECT * FROM trade_journal
                   WHERE symbol = ? AND entry_time >= ?
                   ORDER BY entry_time DESC LIMIT ?""",
                (symbol, cutoff_str, limit),
            ).fetchall()
        else:
            rows = db._conn.execute(
                """SELECT * FROM trade_journal
                   WHERE entry_time >= ?
                   ORDER BY entry_time DESC LIMIT ?""",
                (cutoff_str, limit),
            ).fetchall()

        trades = [dict(r) for r in rows]

        # ─── SL/TP Analysis ───
        analysis = _analyze_trades(trades)
        per_symbol = _analyze_per_symbol(trades)

        return {
            "trades": trades,
            "total": len(trades),
            "days": days,
            "analysis": analysis,
            "per_symbol": per_symbol,
        }

    except Exception as e:
        logger.error("trade_history_error", extra={"error": str(e)})
        return {"trades": [], "analysis": {}, "error": str(e)}


@router.get("/status/mt5-history")
async def mt5_deal_history(
    request: Request,
    days: int = 7,
):
    """
    ดึงประวัติ deals/orders จาก MT5 โดยตรง — ย้อนหลัง N วัน.

    ใช้เพื่อ cross-check กับ trade_journal ว่าข้อมูลตรงกัน.
    """
    try:
        import MetaTrader5 as mt5
        from datetime import timedelta

        term = mt5.terminal_info()
        if not term or not term.connected:
            return {"deals": [], "error": "MT5 not connected"}

        now = datetime.now(timezone.utc)
        from_date = now - timedelta(days=days)

        # ดึง deals (actual fills)
        deals = mt5.history_deals_get(from_date, now)
        deal_list = []
        if deals:
            for d in deals:
                deal_list.append({
                    "ticket": d.ticket,
                    "order": d.order,
                    "time": datetime.fromtimestamp(d.time, tz=timezone.utc).isoformat(),
                    "symbol": d.symbol,
                    "type": _deal_type_str(d.type),
                    "entry": _deal_entry_str(d.entry),
                    "volume": d.volume,
                    "price": d.price,
                    "profit": round(d.profit, 2),
                    "swap": round(d.swap, 2),
                    "commission": round(d.commission, 2),
                    "comment": d.comment,
                    "position_id": d.position_id,
                })

        return {
            "deals": deal_list,
            "total": len(deal_list),
            "days": days,
            "from": from_date.isoformat(),
            "to": now.isoformat(),
        }

    except Exception as e:
        logger.error("mt5_history_error", extra={"error": str(e)})
        return {"deals": [], "error": str(e)}


# ====================================================================
# Analysis Helpers
# ====================================================================

def _analyze_trades(trades: list[dict]) -> dict:
    """วิเคราะห์ trades: SL/TP hit rate, R:R, streaks."""
    if not trades:
        return _empty_analysis()

    closed = [t for t in trades if t.get("profit_usd") is not None]
    if not closed:
        return _empty_analysis()

    total = len(closed)
    wins = [t for t in closed if (t.get("profit_usd") or 0) > 0]
    losses = [t for t in closed if (t.get("profit_usd") or 0) < 0]
    breakevens = [t for t in closed if (t.get("profit_usd") or 0) == 0]

    # SL/TP hit analysis
    sl_hits = 0
    tp_hits = 0
    manual_close = 0

    for t in closed:
        exit_p = t.get("exit_price", 0) or 0
        sl = t.get("stop_loss", 0) or 0
        tp = t.get("take_profit", 0) or 0
        action = (t.get("action") or "").upper()

        if sl and exit_p:
            # Check if exit was near SL
            if action in ("BUY", "LONG"):
                if abs(exit_p - sl) < abs(exit_p) * 0.001:  # within 0.1%
                    sl_hits += 1
                    continue
            elif action in ("SELL", "SHORT"):
                if abs(exit_p - sl) < abs(exit_p) * 0.001:
                    sl_hits += 1
                    continue

        if tp and exit_p:
            # Check if exit was near TP
            if action in ("BUY", "LONG"):
                if abs(exit_p - tp) < abs(exit_p) * 0.001:
                    tp_hits += 1
                    continue
            elif action in ("SELL", "SHORT"):
                if abs(exit_p - tp) < abs(exit_p) * 0.001:
                    tp_hits += 1
                    continue

        manual_close += 1

    # Profit stats
    total_profit = sum(t.get("profit_usd", 0) for t in wins)
    total_loss = abs(sum(t.get("profit_usd", 0) for t in losses))
    net_pnl = total_profit - total_loss
    profit_factor = total_profit / total_loss if total_loss > 0 else 0
    avg_win = total_profit / len(wins) if wins else 0
    avg_loss = total_loss / len(losses) if losses else 0
    avg_rr = avg_win / avg_loss if avg_loss > 0 else 0

    # Win/Loss streaks
    current_streak = 0
    max_win_streak = 0
    max_loss_streak = 0
    streak_type = None

    for t in sorted(closed, key=lambda x: x.get("entry_time", "")):
        pnl = t.get("profit_usd", 0) or 0
        if pnl > 0:
            if streak_type == "win":
                current_streak += 1
            else:
                current_streak = 1
                streak_type = "win"
            max_win_streak = max(max_win_streak, current_streak)
        elif pnl < 0:
            if streak_type == "loss":
                current_streak += 1
            else:
                current_streak = 1
                streak_type = "loss"
            max_loss_streak = max(max_loss_streak, current_streak)

    return {
        "total_closed": total,
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(breakevens),
        "win_rate": round(len(wins) / total * 100, 1) if total > 0 else 0,
        "sl_hits": sl_hits,
        "tp_hits": tp_hits,
        "manual_close": manual_close,
        "sl_hit_rate": round(sl_hits / total * 100, 1) if total > 0 else 0,
        "tp_hit_rate": round(tp_hits / total * 100, 1) if total > 0 else 0,
        "net_pnl": round(net_pnl, 2),
        "total_profit": round(total_profit, 2),
        "total_loss": round(total_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_rr": round(avg_rr, 2),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
    }


def _analyze_per_symbol(trades: list[dict]) -> dict:
    """สรุป performance แยกตาม symbol."""
    by_symbol: dict[str, list[dict]] = {}
    for t in trades:
        sym = t.get("symbol", "?")
        by_symbol.setdefault(sym, []).append(t)

    result = {}
    for sym, sym_trades in by_symbol.items():
        closed = [t for t in sym_trades if t.get("profit_usd") is not None]
        if not closed:
            continue
        total = len(closed)
        wins = len([t for t in closed if (t.get("profit_usd") or 0) > 0])
        net = sum(t.get("profit_usd", 0) for t in closed)
        result[sym] = {
            "total": total,
            "wins": wins,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "net_pnl": round(net, 2),
            "strategies_used": list(set(
                t.get("strategy_name", "?") for t in closed if t.get("strategy_name")
            )),
        }

    return result


def _empty_analysis() -> dict:
    return {
        "total_closed": 0, "wins": 0, "losses": 0, "breakevens": 0,
        "win_rate": 0, "sl_hits": 0, "tp_hits": 0, "manual_close": 0,
        "sl_hit_rate": 0, "tp_hit_rate": 0,
        "net_pnl": 0, "total_profit": 0, "total_loss": 0,
        "profit_factor": 0, "avg_win": 0, "avg_loss": 0, "avg_rr": 0,
        "max_win_streak": 0, "max_loss_streak": 0,
    }


def _deal_type_str(deal_type: int) -> str:
    """แปลง MT5 deal type เป็น string."""
    types = {0: "buy", 1: "sell", 2: "balance", 3: "credit",
             4: "charge", 5: "correction", 6: "bonus"}
    return types.get(deal_type, f"unknown_{deal_type}")


def _deal_entry_str(entry: int) -> str:
    """แปลง MT5 deal entry เป็น string."""
    entries = {0: "in", 1: "out", 2: "inout", 3: "out_by"}
    return entries.get(entry, f"unknown_{entry}")
