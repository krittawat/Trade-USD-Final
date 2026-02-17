"""
FastAPI Application — API หลักของระบบเทรด Antigravity.

Endpoints:
    /api/health     — ตรวจสุขภาพระบบ (MT5, QuestDB, SQLite, DuckDB)
    /api/symbols    — รายการสัญลักษณ์ที่เทรดได้
    /api/decisions  — ประวัติการตัดสินใจ (decision trace)
    /api/replay     — เริ่ม/หยุด replay session (เล่นซ้ำข้อมูลเก่า)
    /api/analytics  — ดึง metrics (PF, DD, Sharpe)

CORS:
    อนุญาตให้ frontend (localhost:3000) เชื่อมต่อ.

Lifecycle (lifespan):
    Startup:
        1. SQLite connect  → trade journal, decision trace
        2. Brain connect   → AI memory (MemoryStore)
        3. Factory         → auto-register strategies ทั้งหมด
        4. MT5 connect     → เชื่อมต่อ MetaTrader 5
        5. MasterLoop      → spawn background task

    Shutdown:
        - หยุด MasterLoop
        - ปิด DB connections ทั้งหมด
        - ปิด MT5 connection
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import get_logger
from app.api.routes import health, symbols, decisions, replay, analytics, positions, training, shadow

logger = get_logger(__name__)


# ====================================================================
# Lifespan — วงจรชีวิตของ API (Startup + Shutdown)
# ====================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup / Shutdown lifecycle ของ FastAPI app.

    Startup (ลำดับสำคัญ — ต้องเรียงตามนี้):
        1. SQLite connect  — ฐานข้อมูล trade journal, decisions, state
        2. Brain MemoryStore connect — AI learning memory (SQLite แยกไฟล์)
        3. Strategy Factory auto-register — โหลดทุก strategy จาก templates/
        4. MT5 Client connect — เชื่อมต่อ MetaTrader 5 (อาจ retry ใน loop)
        5. MasterLoop spawn — background task ที่วนลูปเทรด

    Shutdown (cleanup ลำดับกลับ):
        - หยุด MasterLoop → ปิด Brain → ปิด SQLite → ปิด MT5

    References:
        - ทุก component ถูกเก็บใน app.state เพื่อให้ API routes เข้าถึงได้
    """
    import asyncio
    from app.db.sqlite import SQLiteStore
    from app.brain.memory_store import MemoryStore
    from app.brain.trainer import Trainer
    from app.strategy.factory import StrategyFactory
    from app.mt5.client import MT5Client
    from app.master_loop import MasterLoop

    settings = get_settings()
    logger.info("api_startup", extra={"port": settings.api_port, "mode": settings.trading_mode})

    # ─── 1. SQLite — ฐานข้อมูลเทรด + state ───
    db = SQLiteStore(settings)
    db.connect()
    db.set_state("mode", settings.trading_mode)
    logger.info("sqlite_connected")

    # ─── 2. Brain — หน่วยความจำ AI ───
    memory = MemoryStore()
    memory.connect()
    trainer = Trainer(memory_store=memory, sqlite_store=db)
    logger.info("brain_connected")

    # ─── 3. Factory — สแกน + ลงทะเบียน strategies ทั้งหมด ───
    factory = StrategyFactory()
    registered = factory.auto_register()
    logger.info("factory_ready", extra={"strategies_registered": registered})

    # ─── 4. MT5 Client — เชื่อมต่อ MetaTrader 5 (graceful) ───
    mt5 = MT5Client(settings)
    try:
        mt5.connect()
        account = mt5.get_account_state()
        logger.info("mt5_connected", extra={
            "balance": account.balance,
            "equity": account.equity,
            "positions": account.open_positions,
        })
    except Exception as e:
        # ⚠️ เชื่อมไม่ได้ → จะ auto-retry ในลูป (ไม่หยุดระบบ)
        logger.warning("mt5_connection_failed", extra={
            "error": str(e),
            "detail": "will auto-retry in master loop every 30 cycles",
        })

    # ─── 4.5 Telegram Notifier — แจ้งเตือนเปิด/ปิดออเดอร์ ───
    from app.services.telegram import TelegramNotifier
    telegram = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        enabled=settings.telegram_enabled,
    )

    # ─── 5. Master Loop — background task วนลูปเทรด ───
    loop = MasterLoop(
        settings=settings,
        mt5_client=mt5,
        factory=factory,
        db=db,
        telegram=telegram,
    )
    loop.brain_memory = memory
    loop.trainer = trainer

    # ─── 6. Training Orchestrator — Self-Training System ───
    training_orch = None
    if getattr(settings, 'training_enabled', True):
        from app.brain.training_orchestrator import TrainingOrchestrator
        training_orch = TrainingOrchestrator(
            factory=factory,
            memory_store=memory,
            mt5_client=mt5,
            settings=settings,
        )
        loop.training_orchestrator = training_orch
        logger.info("training_orchestrator_ready", extra={
            "interval_hours": getattr(settings, 'training_interval_hours', 6.0),
        })

    loop_task = asyncio.create_task(loop.run())

    # --- เก็บ references ไว้ใน app.state เพื่อให้ API routes เข้าถึง ---
    app.state.db = db
    app.state.mt5 = mt5
    app.state.factory = factory
    app.state.memory = memory
    app.state.trainer = trainer
    app.state.master_loop = loop
    app.state.training_orchestrator = training_orch
    app.state.telegram = telegram
    app.state.settings = settings

    logger.info("startup_complete", extra={
        "mode": settings.trading_mode,
        "strategies": registered,
        "mt5": "connected" if mt5.is_connected() else "pending",
    })

    yield  # ← จุดนี้คือช่วง API ทำงาน (รับ requests)

    # ─── Shutdown — ปิดทุกอย่าง ───
    logger.info("api_shutdown")
    loop.running = False
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass

    # ปิด connections (ลำดับกลับจาก startup)
    await telegram.close()
    memory.disconnect()
    db.disconnect()
    if mt5.is_connected():
        mt5.disconnect()
    logger.info("api_shutdown_complete")


# ====================================================================
# FastAPI App — สร้าง app instance
# ====================================================================

app = FastAPI(
    title="Antigravity AI Trading System",
    description="Production-grade AI trading API for MT5 (Exness USD)",
    version="0.1.0",
    lifespan=lifespan,  # ใช้ lifespan ที่กำหนดข้างบน
)

# ─── CORS — อนุญาต frontend (Nuxt) เชื่อมต่อ ───
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

# ─── Mount Routes — เชื่อม API endpoints ───
app.include_router(health.router, prefix="/api", tags=["Health"])
app.include_router(symbols.router, prefix="/api", tags=["Symbols"])
app.include_router(decisions.router, prefix="/api", tags=["Decisions"])
app.include_router(replay.router, prefix="/api", tags=["Replay"])
app.include_router(analytics.router, prefix="/api", tags=["Analytics"])
app.include_router(positions.router, prefix="/api", tags=["Positions"])
app.include_router(training.router, prefix="/api", tags=["Training"])
app.include_router(shadow.router, prefix="/api", tags=["Shadow"])
