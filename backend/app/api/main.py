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
from app.api.routes import health, symbols, decisions, replay, analytics, positions, training, shadow, brain, status, coach, personality
from app.api.routes import learning as learning_route

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
    from app.db.duckdb import DuckDBStore
    from app.execution.replay import ReplayStreamer

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
    memory.set_trading_db(db)  # Cross-DB: shadow scoreboard queries
    trainer = Trainer(memory_store=memory, sqlite_store=db)
    logger.info("brain_connected")

    # ─── 2.1 DuckDB — Analytics & Replay Data ───
    analytics_db = DuckDBStore(settings)
    analytics_db.connect()
    logger.info("duckdb_connected")

    # ─── 2.5. Strategy Param Loader — โหลด params จาก DB ───
    from app.strategy.param_loader import StrategyParamLoader, set_param_loader
    param_loader = StrategyParamLoader(db)
    set_param_loader(param_loader)

    # ─── 3. Strategy Factory — ลงทะเบียนกลยุทธ์ทั้งหมด (Dynamic) ───
    # Note: ย้ายการโหลดหนักๆ ไปเป็น background task เพื่อไม่ให้ API ค้างตอนเริ่ม
    factory = StrategyFactory()
    
    # ─── 4. MT5 Client — เชื่อมต่อ MetaTrader 5 (graceful) ───
    mt5 = MT5Client(settings)

    # ─── Telegram: Notifier ───
    from app.services.telegram import TelegramNotifier
    telegram = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        enabled=settings.telegram_enabled,
    )

    # ─── Telegram: Command Handler ───
    from app.services.telegram_commands import TelegramCommandHandler
    tg_commands = TelegramCommandHandler(
        telegram_notifier=telegram,
        mt5_client=mt5,
        settings=settings,
    )
    tg_cmd_task = asyncio.create_task(tg_commands.start_polling())

    # ─── 5. Master Loop ───
    loop = MasterLoop(
        settings=settings,
        mt5_client=mt5,
        factory=factory,
        db=db,
        telegram=telegram,
    )
    loop.brain_memory = memory
    loop.trainer = trainer
    if loop._regime_engine:
        loop._regime_engine.memory = memory
    if loop._pattern_scorer:
        loop._pattern_scorer.memory_store = memory

    # ─── 5.1 Replay Streamer ───
    # Pipeline is inside the loop object, but we can access it
    replay_streamer = ReplayStreamer(db=db, pipeline=loop.pipeline, analytics_db=analytics_db)

    # Placeholders for brain components
    training_orch = None
    sentiment_agg = None
    ml_learner = None
    deep_learner = None
    recommender = None
    web_researcher = None
    online_learner = None
    registered_count = 0

    async def _async_full_startup():
        nonlocal training_orch, sentiment_agg, ml_learner, deep_learner, recommender, web_researcher, online_learner, registered_count
        try:
            logger.info("async_startup_begin", extra={"heavy_tasks": ["factory", "mt5", "ai_brain"]})
            
            # 1. Strategy Factory registration (IMPORT HEAVY)
            registered_count = await asyncio.to_thread(factory.auto_register, db=db)
            logger.info("factory_ready_async", extra={"strategies_registered": registered_count})
            
            # 2. MT5 connection
            try:
                success = await asyncio.to_thread(mt5.connect)
                if success:
                    account = mt5.get_account_state()
                    logger.info("mt5_connected_async", extra={
                        "balance": account.balance,
                        "equity": account.equity,
                    })
            except Exception as e:
                logger.warning("mt5_connection_failed_async", extra={"error": str(e)})

            # 3. Brain Modules
            if getattr(settings, 'training_enabled', True):
                from app.brain.training_orchestrator import TrainingOrchestrator
                training_orch = TrainingOrchestrator(factory=factory, memory_store=memory, mt5_client=mt5, settings=settings)
                loop.training_orchestrator = training_orch
            
            if getattr(settings, 'sentiment_enabled', True):
                from app.brain.sentiment_aggregator import SentimentAggregator
                sentiment_agg = SentimentAggregator(memory_store=memory, settings=settings)
                loop.sentiment_aggregator = sentiment_agg
                
            if getattr(settings, 'ml_pattern_enabled', True):
                from app.brain.ml_pattern_learner import MLPatternLearner
                ml_learner = MLPatternLearner(memory_store=memory, settings=settings)
                loop.ml_pattern_learner = ml_learner
                
            if getattr(settings, 'deep_learning_enabled', True):
                from app.brain.deep_learner import DeepLearner
                deep_learner = DeepLearner(settings=settings)
                loop.deep_learner = deep_learner
                
            from app.brain.recommender import Recommender
            recommender = Recommender(memory=memory)
            loop.recommender = recommender
            
            from app.brain.web_researcher import WebResearcher
            from app.brain.online_learner import OnlineLearner
            web_researcher = WebResearcher()
            online_learner = OnlineLearner()
            loop.web_researcher = web_researcher
            loop.online_learner = online_learner
            learning_route._researcher_instance = web_researcher
            learning_route._learner_instance = online_learner
            
            # Final sync & Update app.state
            loop.sync_submodule_deps()
            app.state.training_orchestrator = training_orch
            app.state.sentiment_aggregator = sentiment_agg
            app.state.ml_pattern_learner = ml_learner
            app.state.deep_learner = deep_learner
            app.state.recommender = recommender
            app.state.web_researcher = web_researcher
            app.state.online_learner = online_learner
            
            logger.info("async_heavy_startup_complete")
        except Exception as e:
            logger.critical("async_startup_failed", extra={"error": str(e)}, exc_info=True)

    # Start loop task
    loop_task = asyncio.create_task(loop.run())
    # Start background bootstrap
    asyncio.create_task(_async_full_startup())

    # --- เก็บ references ไว้ใน app.state ---
    app.state.db = db
    app.state.mt5 = mt5
    app.state.factory = factory
    app.state.memory = memory
    app.state.trainer = trainer
    app.state.master_loop = loop
    app.state.telegram = telegram
    app.state.tg_commands = tg_commands
    app.state.settings = settings
    app.state.analytics_db = analytics_db
    app.state.replay_streamer = replay_streamer
    # Initialize placeholders
    app.state.training_orchestrator = None
    app.state.sentiment_aggregator = None
    app.state.ml_pattern_learner = None
    app.state.deep_learner = None
    app.state.recommender = None
    app.state.web_researcher = None
    app.state.online_learner = None

    logger.info("startup_complete_deferred", extra={
        "mode": settings.trading_mode,
        "mt5": "pending_async",
    })

    # ─── Telegram: แจ้งเตือน Bot Started ───
    symbols_list = [s.strip() for s in settings.trading_symbols.split(",") if s.strip()]
    equity = 0.0
    currency = "USD"
    try:
        if mt5.is_connected():
            acct = mt5.get_account_state()
            equity = acct.real_equity
            currency = acct.currency
    except Exception:
        pass
    asyncio.create_task(telegram.notify_bot_start(
        mode=settings.trading_mode,
        symbols=symbols_list,
        strategies_count=registered_count,
        equity=equity,
        currency=currency,
    ))

    # ─── Web Knowledge Auto-Refresh Loop (every 30 min) ───
    async def _web_refresh_loop():
        await asyncio.sleep(180)  # Wait 3 min for system stabilize and brain loading
        while True:
            try:
                if web_researcher:
                    for sym in symbols_list:
                        await web_researcher.search_market_knowledge(sym)
                        await asyncio.sleep(3)
                    logger.info("web_knowledge_refreshed", extra={"symbols": len(symbols_list)})
            except Exception as e:
                logger.debug("web_refresh_error", extra={"error": str(e)})
            await asyncio.sleep(1800)

    web_refresh_task = asyncio.create_task(_web_refresh_loop())

    yield  # ← จุดนี้คือช่วง API ทำงาน (รับ requests)

    # ─── Shutdown — ปิดทุกอย่าง ───
    logger.info("api_shutdown")
    loop.running = False
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass

    # ปิด Telegram command polling
    await tg_commands.close()
    tg_cmd_task.cancel()
    try:
        await tg_cmd_task
    except asyncio.CancelledError:
        pass

    # ปิด connections (ลำดับกลับจาก startup)
    await telegram.close()
    # questdb removed — no disconnect needed
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

# ─── CORS — อนุญาต frontend (Nuxt) + WebSocket เชื่อมต่อ ───
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Dev mode: allow all origins (WebSocket + API test)
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Mount Routes — เชื่อม API endpoints ───
from app.api.routes import health, decisions, symbols, replay, coach, analytics, positions, training, shadow, brain, status, personality
from app.api.routes import ws_status

app.include_router(health.router, prefix="/api/health", tags=["Health"])
app.include_router(decisions.router, prefix="/api/decisions", tags=["Decisions"])
app.include_router(symbols.router, prefix="/api/symbols", tags=["Symbols"])
app.include_router(replay.router, prefix="/api/replay", tags=["Replay"])
app.include_router(coach.router, prefix="/api/coach", tags=["Coach"])
app.include_router(analytics.router, prefix="/api", tags=["Analytics"])
app.include_router(positions.router, prefix="/api", tags=["Positions"])
app.include_router(training.router, prefix="/api", tags=["Training"])
app.include_router(shadow.router, prefix="/api", tags=["Shadow"])
app.include_router(brain.router, prefix="/api", tags=["Brain Intelligence"])
app.include_router(status.router, prefix="/api", tags=["Status"])
app.include_router(coach.router, prefix="/api", tags=["Auto Coach"])
app.include_router(personality.router, prefix="/api/personality", tags=["Personality"])
app.include_router(ws_status.router, tags=["WebSocket Status"])
app.include_router(learning_route.router, prefix="/api/learning", tags=["Online Learning"])


# ====================================================================
# Standalone Bot Runner (no API server) — for --no-api mode
# ====================================================================

async def _run_bot_standalone():
    """
    รัน MasterLoop โดยไม่ต้องเปิด API server.
    ใช้กับ: python run_bot.py --mode LIVE --no-api
    เหมาะสำหรับรันคู่กับ uvicorn แยกต่างหาก.
    """
    import asyncio
    from app.db.sqlite import SQLiteStore
    from app.brain.memory_store import MemoryStore
    from app.brain.trainer import Trainer
    from app.strategy.factory import StrategyFactory
    from app.mt5.client import MT5Client
    from app.master_loop import MasterLoop

    settings = get_settings()
    logger.info("standalone_bot_startup", extra={"mode": settings.trading_mode})

    # 1. SQLite
    db = SQLiteStore(settings)
    db.connect()
    db.set_state("mode", settings.trading_mode)

    # 2. Brain
    memory = MemoryStore()
    memory.connect()
    memory.set_trading_db(db)
    trainer = Trainer(memory_store=memory, sqlite_store=db)

    # 2.5. Param Loader
    from app.strategy.param_loader import StrategyParamLoader, set_param_loader
    param_loader = StrategyParamLoader(db)
    set_param_loader(param_loader)

    # 3. Factory
    factory = StrategyFactory()
    factory.auto_register(db=db)

    # 4. MT5
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
        logger.warning("mt5_connection_failed", extra={"error": str(e)})

    # 4.6 Telegram
    from app.services.telegram import TelegramNotifier
    telegram = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        enabled=settings.telegram_enabled,
    )

    # 5. Master Loop
    loop = MasterLoop(
        settings=settings,
        mt5_client=mt5,
        factory=factory,
        db=db,
        telegram=telegram,
    )
    loop.brain_memory = memory
    loop.trainer = trainer
    if loop._regime_engine:
        loop._regime_engine.memory = memory
    if loop._pattern_scorer:
        loop._pattern_scorer.memory_store = memory

    # 6-8. Brain Intelligence
    if getattr(settings, 'ml_pattern_enabled', True):
        from app.brain.ml_pattern_learner import MLPatternLearner
        ml_learner = MLPatternLearner(memory_store=memory, settings=settings)
        loop.ml_pattern_learner = ml_learner

    if getattr(settings, 'deep_learning_enabled', True):
        from app.brain.deep_learner import DeepLearner
        deep_learner = DeepLearner(settings=settings)
        loop.deep_learner = deep_learner

    from app.brain.recommender import Recommender
    recommender = Recommender(memory=memory)
    loop.recommender = recommender
    if loop._regime_engine:
        loop._regime_engine.recommender = recommender

    loop.sync_submodule_deps()
    logger.info("standalone_bot_ready", extra={"mode": settings.trading_mode})

    # Run
    try:
        await loop.run()
    finally:
        memory.disconnect()
        db.disconnect()
        if mt5.is_connected():
            mt5.disconnect()
        logger.info("standalone_bot_shutdown")

