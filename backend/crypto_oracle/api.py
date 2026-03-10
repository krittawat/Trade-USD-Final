from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import asyncio
from typing import Dict, Any

from app.core.logging import get_logger
from .ws_client import BinanceWSClient
from .models import LiquidityIntelligenceEngine, SignalResult

logger = get_logger("crypto_oracle.api")

app = FastAPI(title="BTC Liquidity Intelligence Oracle", version="1.0.0")

class OracleState:
    def __init__(self):
        self.ws_client = BinanceWSClient()
        self.engine = LiquidityIntelligenceEngine()
        # Bind the websocket feed to the engine
        self.ws_client.add_callback(self.engine.update_from_stream)

oracle_state = OracleState()

@app.on_event("startup")
async def startup_event():
    # Start WS Client in background task
    asyncio.create_task(oracle_state.ws_client.start())
    logger.info("Crypto Oracle Started.")

@app.on_event("shutdown")
async def shutdown_event():
    oracle_state.ws_client.stop()
    logger.info("Crypto Oracle Shutdown.")


# --- Response Models ---
class SignalResponse(BaseModel):
    signal: str
    entry_price: float
    stop_loss: float
    target_liquidity_zone: float
    confidence_score: int
    reason: str
    mark_price: float
    funding_rate: float
    cvd: float

class StatusResponse(BaseModel):
    status: str
    symbol: str
    mark_price: float
    bids_levels: int
    asks_levels: int

@app.get("/api/v1/health", response_model=StatusResponse)
async def get_health():
    state = oracle_state.engine.state
    return StatusResponse(
        status="active",
        symbol=state.symbol,
        mark_price=state.mark_price,
        bids_levels=len(state.bids),
        asks_levels=len(state.asks)
    )

@app.get("/api/v1/signal/btc", response_model=SignalResponse)
async def get_btc_signal():
    """
    Called by the main MT5 Antigravity Backend (Squeeze Strategy) to fetch external signal
    """
    state = oracle_state.engine.state
    if state.mark_price == 0:
        raise HTTPException(status_code=503, detail="Oracle has no market data yet. Waiting for WebSocket pool.")
        
    signal: SignalResult = oracle_state.engine.detect_squeeze()
    
    return SignalResponse(
        signal=signal.signal,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        target_liquidity_zone=signal.target_liquidity_zone,
        confidence_score=signal.confidence_score,
        reason=signal.reason,
        mark_price=state.mark_price,
        funding_rate=state.funding_rate,
        cvd=state.cvd
    )

@app.get("/api/v1/heatmap/btc")
async def get_btc_heatmap():
    """
    Returns the estimated top Liquidation clusters (like Coinglass).
    Used for frontend Dashboard Visualization.
    """
    if oracle_state.engine.state.mark_price == 0:
         raise HTTPException(status_code=503, detail="No data yet.")
         
    clusters = oracle_state.engine.estimate_liquidation_clusters()
    return {
        "mark_price": oracle_state.engine.state.mark_price,
        "short_liquidations_above": clusters["short_liqs"],
        "long_liquidations_below": clusters["long_liqs"]
    }
