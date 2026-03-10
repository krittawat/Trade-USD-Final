# -*- coding: utf-8 -*-
"""
OPUS API — FastAPI Bridge for MT5 Live Trading
===============================================
Endpoints:
  GET  /health      — System health check
  GET  /account     — Live account state + OPUS status
  GET  /positions   — Open positions
  POST /order       — Execute trade (requires signal validation)

Run: uvicorn backend.trader.api.opus_api:app --port 8000
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger("opus_api")

app = FastAPI(
    title="ANTIGRAVITY-OPUS Trading API",
    description="Institutional Execution Intelligence — MT5 Bridge",
    version="1.0.0",
)

# ═══════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    status: str
    mt5_connected: bool
    timestamp: str
    mode: str = "LIVE"

class AccountResponse(BaseModel):
    equity: float
    balance: float
    margin_level: float
    margin_free: float
    daily_pnl: float
    consecutive_losses: int
    floating_dd_pct: float
    # OPUS Status
    risk_level: str
    trade_allowed: bool
    block_reason: str
    defensive_mode: bool
    lot_multiplier: float
    regime: str
    discipline_ok: bool

class PositionItem(BaseModel):
    ticket: int
    symbol: str
    side: str
    volume: float
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float
    magic: int

class PositionsResponse(BaseModel):
    count: int
    positions: list[PositionItem]
    total_profit: float

class OrderRequest(BaseModel):
    symbol: str
    side: str  # BUY or SELL
    lot_size: Optional[float] = None  # auto-compute if None
    sl: float
    tp: float
    comment: str = "OPUS_API"

class OrderResponse(BaseModel):
    trade_allowed: bool
    regime: str
    defensive_mode: bool
    recommended_symbol: str
    lot_size: float
    risk_status: str
    next_valid_setup: str
    order_result: Optional[str] = None


# ═══════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════

@app.get("/health", response_model=HealthResponse)
def health_check():
    """System health check — MT5, DB, Governor."""
    try:
        import MetaTrader5 as mt5
        connected = mt5.initialize()
        if connected:
            info = mt5.account_info()
            mt5_ok = info is not None
        else:
            mt5_ok = False
    except Exception:
        mt5_ok = False

    return HealthResponse(
        status="OK" if mt5_ok else "DEGRADED",
        mt5_connected=mt5_ok,
        timestamp=datetime.utcnow().isoformat(),
    )


@app.get("/account", response_model=AccountResponse)
def get_account():
    """Live account state with OPUS Governor analysis."""
    try:
        import MetaTrader5 as mt5
        from backend.trader.main import get_real_account_state
        from backend.trader.risk.opus_governor import governor

        if not mt5.initialize():
            raise HTTPException(status_code=503, detail="MT5 not connected")

        account_state = get_real_account_state(mt5)
        opus_status = governor.compute_status(account_state)

        return AccountResponse(
            equity=account_state["equity"],
            balance=account_state["balance"],
            margin_level=account_state.get("margin_level", 0),
            margin_free=account_state.get("margin_free", 0),
            daily_pnl=account_state["daily_pnl"],
            consecutive_losses=account_state["consecutive_losses"],
            floating_dd_pct=opus_status.floating_dd_pct,
            risk_level=opus_status.risk_level,
            trade_allowed=opus_status.trade_allowed,
            block_reason=opus_status.block_reason,
            defensive_mode=opus_status.defensive_mode,
            lot_multiplier=opus_status.lot_multiplier,
            regime=opus_status.regime,
            discipline_ok=opus_status.discipline_ok,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/positions", response_model=PositionsResponse)
def get_positions():
    """Get all open positions."""
    try:
        import MetaTrader5 as mt5

        if not mt5.initialize():
            raise HTTPException(status_code=503, detail="MT5 not connected")

        positions = mt5.positions_get()
        if not positions:
            return PositionsResponse(count=0, positions=[], total_profit=0)

        items = []
        total = 0.0
        for p in positions:
            side = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
            items.append(PositionItem(
                ticket=p.ticket,
                symbol=p.symbol,
                side=side,
                volume=p.volume,
                price_open=p.price_open,
                price_current=p.price_current,
                sl=p.sl,
                tp=p.tp,
                profit=p.profit,
                magic=p.magic,
            ))
            total += p.profit

        return PositionsResponse(count=len(items), positions=items, total_profit=total)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/order", response_model=OrderResponse)
def place_order(req: OrderRequest):
    """
    Execute trade through OPUS protocol.
    Validates all risk checks before execution.
    """
    try:
        import MetaTrader5 as mt5
        from backend.trader.main import get_real_account_state, get_real_market_state, compute_lot_size
        from backend.trader.risk.opus_governor import governor
        from backend.trader.risk.gate import risk_engine
        from backend.trader.data.mapper import mapper

        if not mt5.initialize():
            raise HTTPException(status_code=503, detail="MT5 not connected")

        # 1. Get live state
        account_state = get_real_account_state(mt5)
        broker_symbol = mapper.to_broker(req.symbol)
        market_state = get_real_market_state(mt5, broker_symbol)

        # 2. OPUS Governor status
        opus_status = governor.compute_status(account_state, market_state)

        # Build signal dict for risk gate
        signal = {
            "symbol": req.symbol,
            "side": req.side,
            "sl": req.sl,
            "tp1": req.tp,
            "tp2": req.tp,
            "tp3": req.tp,
            "entry_price": mt5.symbol_info_tick(broker_symbol).ask if req.side == "BUY"
                          else mt5.symbol_info_tick(broker_symbol).bid,
            "model": "OPUS_API",
            "confidence": 1.0,
        }

        # 3. Risk gate check
        gate_res = risk_engine.risk_gate(signal, account_state, market_state, opus_status=opus_status)

        # 4. Compute lot
        if req.lot_size:
            lot = req.lot_size
        else:
            sl_dist = abs(signal["entry_price"] - req.sl)
            risk_pct = governor.get_risk_pct(opus_status.defensive_mode)
            raw_lot = compute_lot_size(account_state["equity"], risk_pct, sl_dist)
            lot = max(0.01, round(raw_lot * opus_status.lot_multiplier, 2))
            lot = min(lot, 0.02)

        response = OrderResponse(
            trade_allowed=gate_res["allowed"],
            regime=opus_status.regime,
            defensive_mode=opus_status.defensive_mode,
            recommended_symbol=req.symbol,
            lot_size=lot,
            risk_status=opus_status.risk_level,
            next_valid_setup="Waiting for signal..." if not gate_res["allowed"]
                            else f"{req.side} {req.symbol} @ {signal['entry_price']:.2f}",
        )

        # 5. Execute if allowed
        if gate_res["allowed"]:
            from backend.trader.execution.mt5_order import Executor
            executor = Executor(mode="live")
            result = executor.place_order(signal, lot_size=lot)
            response.order_result = result.get("status", "unknown")
        else:
            response.order_result = f"BLOCKED: {gate_res['reasons']}"

        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
