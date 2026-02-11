"""
FastAPI Application — API หลักของระบบเทรด.

Endpoints:
    /api/health     — ตรวจสุขภาพระบบ (MT5, QuestDB, SQLite, DuckDB)
    /api/symbols    — รายการสัญลักษณ์ที่เทรดได้
    /api/decisions  — ประวัติการตัดสินใจ
    /api/replay     — เริ่ม/หยุด replay session
    /api/analytics  — ดึง metrics (PF, DD, Sharpe)

CORS:
    อนุญาตให้ frontend (localhost:3000) เชื่อมต่อ.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import get_logger
from app.api.routes import health, symbols, decisions, replay, analytics

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / Shutdown lifecycle.
    
    Startup:
        - เชื่อมต่อ DB ทั้งหมด
        - ตรวจสอบ QuestDB health
    
    Shutdown:
        - ปิดการเชื่อมต่อทั้งหมด
    """
    settings = get_settings()
    logger.info("api_startup", extra={"port": settings.api_port})
    
    # TODO: เชื่อมต่อ DB ทั้งหมด
    yield
    
    logger.info("api_shutdown")
    # TODO: ปิดการเชื่อมต่อทั้งหมด


# --- สร้าง FastAPI app ---
app = FastAPI(
    title="Antigravity AI Trading System",
    description="Production-grade AI trading API for MT5 (Exness USD)",
    version="0.1.0",
    lifespan=lifespan,
)

# --- CORS — อนุญาต frontend ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # Nuxt dev server
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Mount routes ---
app.include_router(health.router, prefix="/api", tags=["Health"])
app.include_router(symbols.router, prefix="/api", tags=["Symbols"])
app.include_router(decisions.router, prefix="/api", tags=["Decisions"])
app.include_router(replay.router, prefix="/api", tags=["Replay"])
app.include_router(analytics.router, prefix="/api", tags=["Analytics"])
