"""
Antigravity AI Trading System — Master Loop (FULL PERSISTENCE).

Main trading loop — runs asynchronously at all times:
    1. Load trading symbols from .env (TRADING_SYMBOLS)
    2. Per symbol: analyze market -> select strategy -> Risk Gate -> Execute
    3. Manage position health (SL check, Break-Even, DD guard)
    4. Persist every decision to SQLite (no silent blocks)
    5. Multi-timeframe: M1/M5/M15/H1 per registered strategy needs

Cycle:
    DRY_RUN  = every 5 seconds
    LIVE     = every 3 seconds

Brain Integration:
    - Every cycle: ask Brain for best strategy -> give priority
    - Every 120 cycles (~10 min): run training cycle to learn from new trades

Performance (8GB RAM):
    - Candle dedup: skip re-analysis if latest candle unchanged
    - Multi-timeframe: fetch candles only for needed timeframes
"""

import asyncio
import inspect
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.mode import TradingMode
from app.db.sqlite import SQLiteStore
from app.domain.enums import Action, RegimeType
from app.domain.models import RegimeContext, Decision
from app.mt5.client import MT5Client
from app.mt5.market_data import fetch_candles, reset_terminal_cache
from app.strategy.backtest_router import BacktestRouter
from app.strategy.invoker import analyze_with_fallback, normalize_strategy_params
from app.brain.regime import classify_regime
from app.brain.regime_intelligence import RegimeIntelligenceEngine
from app.brain.pattern_detector import PatternDetector
from app.brain.pattern_scorer import PatternScorer
from app.brain.personality import PersonalityEngine  # NEW
from app.analysis.tick_volume_analyzer import TickVolumeAnalyzer
from app.brain.order_flow import OrderFlowAnalyzer
from app.strategy.factory import StrategyFactory
from app.risk.gate import PreTradeGate
from app.risk.postfill import PostFillGuard
from app.risk.breakeven import should_move_to_breakeven, mark_be_moved, cleanup_old_be_records
from app.risk.trade_manager import (
    TrailingStopManager, TrailingConfig,
    ProfitLockManager, ManualTradeProtector, GhostGuard,
    TakeProfitManager, TPConfig, TPTier,
)
from app.risk.trailing import TrailingManager # New import
from app.risk.position_guardian import PositionGuardian  # Unified Position Guardian
from app.risk.hedging import HedgeManager # New import
from app.risk.smart_hedge import SmartHedge  # Smart Hedge (defensive)
from app.brain.history_learner import TradeHistoryLearner  # Learn from trade history
from app.execution.pipeline import ExecutionPipeline
from app.execution.shadow_runner import ShadowRunner
from app.services.session import get_current_session
from app.services.news_filter import NewsFilter
from app.services.telegram import TelegramNotifier

# ─── Super-Human Intelligence Modules ───
from app.brain.mtf_confluence import MTFConfluenceEngine
from app.brain.entry_optimizer import EntryOptimizer
from app.brain.outcome_analyzer import OutcomeAnalyzer

# ─── Extracted Sub-modules (refactored from MasterLoop) ───
from app.execution.symbol_processor import SymbolProcessor
from app.execution.position_health import PositionHealthChecker
from app.execution.training_scheduler import TrainingScheduler

# ─── Smart Bot V2 Phase 1 ───
from app.brain.trade_coach import TradeCoach
from app.risk.mtf_gate import MTFGate

# ─── Overtrading Protection Modules ───
from app.risk.regime_filter import RegimeFilter
from app.risk.cooldown_manager import CooldownManager
from app.risk.risk_dampener import RiskDampener
from app.risk.session_guard import SessionGuard

logger = get_logger(__name__)

# ─── Trading symbols ───
# Load from .env: TRADING_SYMBOLS=XAUUSDc,XAGUSDc,EURUSDc,USDJPYc,BTCUSDc
# If not set -> fallback to XAUUSDc (Exness USC/Cent account)
DEFAULT_SYMBOLS = ["XAUUSDc"]

# ─── Cycle interval (seconds) ───
CYCLE_INTERVAL = 5.0       # DRY_RUN mode — 5 seconds/cycle
LIVE_CYCLE_INTERVAL = 3.0  # LIVE mode — 3 seconds/cycle (faster)

# ─── Multi-timeframe map ───
# strategy timeframe -> (mt5_timeframe, number of candles to fetch)
# Covers all timeframes used by registered strategies
TIMEFRAME_CANDLE_MAP = {
    "M1":  ("M1", 250),    # 250 M1 candles (~4 hours)
    "M2":  ("M2", 500),    # 500 M2 candles (~16 hours)
    "M3":  ("M3", 500),    # 500 M3 candles (~1 day)
    "M5":  ("M5", 500),    # 500 M5 candles (~1.7 days)
    "M12": ("M12", 500),   # 500 M12 candles (~4 days)
    "M15": ("M15", 250),   # 250 M15 candles (~2.5 days)
    "M30": ("M30", 250),   # 250 M30 candles (~5 days)
    "H1":  ("H1", 300),    # 300 H1 candles (~12 days)
    "H4":  ("H4", 100),    # 100 H4 candles (~16 days)
}


# ====================================================================
# MasterLoop — Main Trading Loop
# ====================================================================

class MasterLoop:
    """
    Main trading loop — controls execution pipeline for all symbols.

    Flow:
        run() -> _run_cycle() -> _process_symbol() x N symbols
                              -> _check_positions() (LIVE only)
                              -> training (every 120 cycles)
                              -> health_check (every 60 cycles)
    """

    def __init__(
        self,
        settings: Settings,
        mt5_client: MT5Client | None = None,
        factory: StrategyFactory | None = None,
        db: SQLiteStore | None = None,
        telegram: TelegramNotifier | None = None,
        # questdb removed — ticks now stored via SQLite (self.db)
    ) -> None:
        """Initialize MasterLoop with all dependencies."""
        self.settings = settings
        self.mode = TradingMode(settings.trading_mode)
        self.mt5 = mt5_client
        self.factory = factory or StrategyFactory()
        self.db = db
        self.telegram = telegram
        self._market_narrative: dict = {}

        # ─── Overtrading Protection ───
        self.cooldown_mgr = CooldownManager(
            cooldown_minutes=getattr(settings, 'cooldown_minutes_after_loss', 5),
            max_consecutive_losses=getattr(settings, 'max_consecutive_losses_session', 2),
        )
        self.session_guard = SessionGuard(
            max_trades_per_session=getattr(settings, 'max_trades_per_session', 3),
        )
        self.regime_filter = RegimeFilter(
            enabled=getattr(settings, 'regime_filter_enabled', True),
            adx_min=getattr(settings, 'regime_adx_min', 18.0),
            ema_compression_pct=getattr(settings, 'regime_ema_compression_pct', 0.1),
            range_threshold_pct=getattr(settings, 'regime_range_threshold_pct', 0.5),
        )
        self.risk_dampener = RiskDampener(
            enabled=getattr(settings, 'risk_dampening_enabled', True),
            loss1_mult=getattr(settings, 'risk_dampener_loss1_mult', 0.7),
            loss2_mult=getattr(settings, 'risk_dampener_loss2_mult', 0.5),
            loss3_mult=getattr(settings, 'risk_dampener_loss3_mult', 0.3),
            win_recovery=getattr(settings, 'risk_dampener_win_recovery', 0.1),
        )

        # ─── Risk Engine components ───
        self.gate = PreTradeGate(
            settings,
            cooldown_mgr=self.cooldown_mgr,
            session_guard=self.session_guard,
            regime_filter=self.regime_filter,
        )
        self.news_filter = NewsFilter(settings.news_block_minutes)  # News filter
        # ─── Execution Adapter ───
        from app.execution.adapter import Mt5LiveAdapter
        from app.execution.paper.adapter import DryRunAdapter
        from app.execution.paper.broker import PaperBroker
        
        if self.mode == TradingMode.LIVE:
            self.adapter = Mt5LiveAdapter(mt5_client)
        else:
            # DRY_RUN / REPLAY / BACKTEST use Paper Adapter
            # Note: PaperBroker needs persistence path
            self.paper_broker = PaperBroker(settings)
            self.adapter = DryRunAdapter(self.paper_broker, mt5_client)

        self.pipeline = ExecutionPipeline(                # Full execution pipeline
            settings, 
            adapter=self.adapter, 
            mt5_client=mt5_client, 
            gate=self.gate, 
            db=db, 
            news_filter=self.news_filter,
            telegram=telegram,
            risk_dampener=self.risk_dampener,
            session_guard=self.session_guard,
        )
        self.postfill = PostFillGuard(mt5_client)        # Post-Fill: verify SL after order opens

        # ─── Trade Management ───
        from app.risk.trailing import TrailingManager
        self.trailing_manager = TrailingManager(mt5_client, settings)  # Legacy trailing (kept for compatibility)
        self.hedge_manager = HedgeManager(mt5_client, settings)    # Hedge Manager
        self._smart_hedge = SmartHedge(mt5_client, settings)       # Smart Hedge (defensive)
        self._history_learner = TradeHistoryLearner(db=db)             # Learn from trade history
        self.ghost_guard = GhostGuard(mt5_client)                  # Ghost Guard (virtual SL/TP)
        self._protect_manual = getattr(settings, 'protect_manual_trades', True)

        # ─── Unified Position Guardian (replaces separate trailing/lock/TP/manual managers) ───
        self.guardian = PositionGuardian(mt5_client, settings)
        # Keep references for backward compatibility
        self.profit_lock_manager = self.guardian.profit_lock_mgr
        self.tp_manager = self.guardian.tp_mgr
        self.manual_protector = self.guardian.manual_protector

        # ─── Brain Integration (injected from lifespan) ───
        self.brain_memory = None  # MemoryStore — set from main.py lifespan
        self.trainer = None       # Trainer — set from main.py lifespan
        self.training_orchestrator = None  # TrainingOrchestrator — self-training system
        self._last_training_time = 0.0     # monotonic timestamp of last self-training

        # ─── AI Brain Intelligence (injected from lifespan) ───
        self.sentiment_aggregator = None   # SentimentAggregator
        self.ml_pattern_learner = None     # MLPatternLearner
        self.recommender = None            # Recommender
        self.online_learner = None         # OnlineLearner
        self.personality_engine = PersonalityEngine()  # Behavioral Analysis Engine
        self._regime_engine = RegimeIntelligenceEngine()  # Regime Intelligence v2
        self._regime_intel: dict[str, object] = {}  # symbol → RegimeIntelligence

        # ─── Candlestick Pattern Intelligence ───
        self._pattern_detector = PatternDetector()
        self._pattern_scorer = PatternScorer()  # memory_store wired from lifespan
        self._pattern_signals: dict[str, list] = {}  # symbol → [PatternSignal]

        # ─── Tick Volume + OHLC Microstructure Analyzer ───
        self._tick_volume_analyzer = TickVolumeAnalyzer()
        self._tick_volume_signals: dict[str, object] = {}  # symbol → TickVolumeSignal
        
        # ─── Order Flow & Volume Profile ───
        self._order_flow_analyzer = OrderFlowAnalyzer(num_bins=50, value_area_pct=0.70)
        self._vp_signals: dict[str, dict] = {}
        
        # ─── Super-Human Intelligence ───
        self._mtf_engine = MTFConfluenceEngine()      # Multi-Timeframe Confluence
        self._entry_optimizer = EntryOptimizer()       # Adaptive Entry Timing
        self._outcome_analyzer = OutcomeAnalyzer(db=db)  # Trade Outcome Learning

        # ─── Auto Coach (Psychologist) ───
        from app.brain.auto_coach import AutoCoach
        self.auto_coach = AutoCoach()
        self.latest_coach_report = None
        self._last_coach_time = 0.0

        # ─── Loop State ───
        self.running = False          # Is the loop running?
        self.kill_switch = False      # Kill-switch: stop sending orders immediately
        self.cycle_count = 0          # Cycle counter
        self.last_decisions: dict[str, dict] = {}  # Last decision per symbol (for API)
        self.regime_contexts: dict[str, RegimeContext] = {}  # Latest regime stats (for API/Dashboard)

        # ─── Performance: candle dedup ───
        # Skip re-analysis if latest candle is the same
        self._last_candle_hash: dict[str, str] = {}  # symbol -> "time:close"

        # ─── Session Transition Tracking ───
        self._last_session: str = ""  # Track session changes to reset cooldown + session guard
        self._daily_lock_date: date | None = None
        self._daily_lock_reason: str = ""
        self._daily_lock_trigger_pl: float = 0.0

        # ─── Peak Equity Tracking (High-Water Mark) ───
        self._peak_equity: float = 0.0


        # ─── Position Tracker: detect closed positions ───
        # Store set of ticket -> {ticket: {symbol, type, volume, entry_price, profit}}
        self._tracked_positions: dict[int, dict] = {}

        # ─── Tick Ingestion Tracker ───
        # symbol -> last_ingested_time_ms (int)
        self._last_tick_time: dict[str, int] = {}

        # ─── Shadow Runner: test strategies in parallel ───
        self.shadow_runner = None
        if getattr(settings, 'shadow_enabled', True):
            self.shadow_runner = ShadowRunner(factory=self.factory, db=db)

        # ─── Shadow candle cache for evaluator (per symbol M5 candles) ───
        self._shadow_candle_cache: dict[str, pd.DataFrame] = {}

        # ─── Backtest Router: data-driven strategy selection ───
        self.backtest_router = None
        try:
            db_path = str(Path(settings.sqlite_db_path))
            self.backtest_router = BacktestRouter(db_path=db_path)
            logger.info("backtest_router_initialized", extra={
                "routes": self.backtest_router.get_routing_table(),
            })
        except Exception as e:
            logger.warning("backtest_router_init_error", extra={"error": str(e)})

        # ─── Performance: pre-compute needed timeframes ───
        # Compute once at init (no need to recalculate per symbol)
        self._needed_tfs: set[str] = {"M5", "H1"}  # M5 always needed + H1 for MTF
        for strat in self.factory._strategies.values():
            tf = getattr(strat, "timeframe", "M5")
            self._needed_tfs.add(tf)

        # ─── Extracted Sub-Modules (delegated from _run_cycle) ───
        self._symbol_processor = SymbolProcessor()
        self._symbol_processor.set_dependencies(
            mt5=self.mt5, factory=self.factory, db=self.db,
            pipeline=self.pipeline, settings=self.settings,
            brain_memory=self.brain_memory, trainer=self.trainer,
            recommender=self.recommender,
            ml_pattern_learner=self.ml_pattern_learner,
            sentiment_aggregator=self.sentiment_aggregator,
            personality_engine=self.personality_engine,
            _regime_engine=self._regime_engine,
            _pattern_detector=self._pattern_detector,
            _pattern_scorer=self._pattern_scorer,
            _tick_volume_analyzer=self._tick_volume_analyzer,
            _order_flow_analyzer=self._order_flow_analyzer,
            _mtf_engine=self._mtf_engine,
            _entry_optimizer=self._entry_optimizer,
            _outcome_analyzer=self._outcome_analyzer,
            backtest_router=self.backtest_router,
            shadow_runner=self.shadow_runner,
            _needed_tfs=self._needed_tfs,
            trade_coach=TradeCoach(db=self.db),
            mtf_gate=MTFGate(),
        )
        self._position_checker = PositionHealthChecker()
        self._position_checker.set_dependencies(
            mt5=self.mt5, settings=self.settings, mode=self.mode,
            telegram=self.telegram, db=self.db,
            hedge_manager=self.hedge_manager, postfill=self.postfill,
            ghost_guard=self.ghost_guard, guardian=self.guardian,
            trailing_manager=self.trailing_manager,
            tp_manager=self.tp_manager,
            cooldown_mgr=self.cooldown_mgr,
            risk_dampener=self.risk_dampener,
            brain_memory=self.brain_memory,
            _outcome_analyzer=self._outcome_analyzer,
        )
        self._training_scheduler = TrainingScheduler()
        self._training_scheduler.set_dependencies(
            training_orchestrator=self.training_orchestrator,
            trainer=self.trainer,
        )

    async def _load_market_narrative(self) -> None:
        """Load AI market narrative from JSON file."""
        narrative_path = Path("backend/data/market_narrative.json")
        if not narrative_path.exists():
            return
            
        try:
            content = await asyncio.to_thread(narrative_path.read_text, encoding="utf-8")
            data = json.loads(content)
            self._market_narrative = data
            logger.info("market_narrative_loaded", extra={
                "symbols_count": len(data),
                "symbols": list(data.keys())
            })
        except Exception as e:
            logger.debug("market_narrative_load_error", extra={"error": str(e)})


    # ────────────────────────────────────────────────────────────────
    # sync_submodule_deps() — Propagate late-injected brain deps
    # ────────────────────────────────────────────────────────────────

    def sync_submodule_deps(self) -> None:
        """
        Propagate late-injected dependencies (brain_memory, trainer, etc.)
        to extracted sub-modules. Call after main.py finishes wiring brain.
        """
        self._symbol_processor.set_dependencies(
            brain_memory=self.brain_memory,
            trainer=self.trainer,
            recommender=self.recommender,
            ml_pattern_learner=self.ml_pattern_learner,
            sentiment_aggregator=self.sentiment_aggregator,
            online_learner=self.online_learner,
            deep_learner=getattr(self, 'deep_learner', None),
        )
        self._position_checker.set_dependencies(
            brain_memory=self.brain_memory,
        )
        self._training_scheduler.set_dependencies(
            training_orchestrator=self.training_orchestrator,
            trainer=self.trainer,
        )
        logger.info("submodule_deps_synced", extra={
            "brain_memory": self.brain_memory is not None,
            "trainer": self.trainer is not None,
            "recommender": self.recommender is not None,
        })

    # ────────────────────────────────────────────────────────────────
    # run() — Start Main Loop
    # ────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Start main loop — runs until stopped or kill-switch activates.

        interval:
            LIVE     -> 3 seconds/cycle
            DRY_RUN  -> 5 seconds/cycle
        """
        self.running = True
        # Parse actual configured symbols
        raw_sym = self.settings.trading_symbols
        configured_symbols = [s.strip() for s in raw_sym.split(",") if s.strip()] or DEFAULT_SYMBOLS
        logger.info("master_loop_started", extra={
            "mode": self.mode.value,
            "symbols": configured_symbols,
            "symbol_count": len(configured_symbols),
        })

        interval = LIVE_CYCLE_INTERVAL if self.mode == TradingMode.LIVE else CYCLE_INTERVAL

        try:
            while self.running and not self.kill_switch:
                await self._run_cycle()
                await asyncio.sleep(interval)
            # ─── Loop exited normally — log reason ───
            logger.warning("master_loop_exited_normally", extra={
                "running": self.running,
                "kill_switch": self.kill_switch,
                "cycles": self.cycle_count,
            })
        except asyncio.CancelledError:
            logger.info("master_loop_cancelled")  # Cancelled by lifespan shutdown
        except Exception as e:
            # ❌ Unexpected error — log full traceback
            logger.critical("master_loop_crash", extra={
                "error": str(e),
                "type": type(e).__name__,
                "cycles": self.cycle_count,
            }, exc_info=True)
        finally:
            await self._shutdown()

    # ────────────────────────────────────────────────────────────────
    # _run_cycle() — One cycle
    # ────────────────────────────────────────────────────────────────

    async def _run_coach_analysis(self) -> None:
        """Run Auto Coach analysis periodically."""
        try:
            now = time.monotonic()
            # Run every 15 minutes (900s)
            if now - self._last_coach_time < 900:
                return

            logger.info("coach_analysis_start")
            
            # Gather session data from DB
            trades = []
            if self.db:
                 trades = await asyncio.to_thread(self.db.get_recent_trades, limit=50)

            using_shadow = False
            # Fallback to Shadow Trades if insufficient real trades
            if len(trades) < 5 and self.db:
                shadow_trades = await asyncio.to_thread(self.db.get_shadow_trades, limit=50)
                # Filter specific to evaluated shadows
                evaluated_shadows = [t for t in shadow_trades if t.get("outcome") != "PENDING"]
                if len(evaluated_shadows) > len(trades):
                    trades = evaluated_shadows
                    using_shadow = True
                    logger.info("coach_using_shadow_trades", extra={"count": len(trades)})

            if not trades:
                logger.debug("coach_skip_no_trades", extra={"reason": "No recent trades (real or shadow) to analyze"})
                self._last_coach_time = now
                return

            # Map trades to Coach format (normalize fields)
            mapped_trades = []
            for t in trades:
                # Determine if real or shadow
                is_shadow = "outcome_pnl" in t
                
                pnl = t.get("outcome_pnl") if is_shadow else t.get("profit_usd")
                entry = t.get("entry_price")
                
                # If values are None, default to 0.0
                pnl = float(pnl) if pnl is not None else 0.0
                entry = float(entry) if entry is not None else 0.0
                
                mapped_trades.append({
                    "id": t.get("id"),
                    "symbol": t.get("symbol"),
                    "action": t.get("action"),
                    "pnl": pnl,
                    "entry": entry,
                    "exit": t.get("take_profit") if is_shadow else t.get("exit_price"), # Approx for shadow
                    "entry_time": t.get("timestamp") if is_shadow else t.get("entry_time"),
                    "exit_time": t.get("evaluated_at") if is_shadow else t.get("exit_time"),
                    "bars": 0, # TODO: calculate duration in bars
                    "reason": t.get("reason", "UNKNOWN"),
                    "regime": t.get("regime", "UNKNOWN"),
                    "rr": 0.0, # TODO: calculate realized RR
                    "lot_size": t.get("lot_size", 0.0),
                })

            from app.brain.auto_coach import SessionData
            # Construct SessionData
            session_data = SessionData(
                trades=mapped_trades,
                symbol=DEFAULT_SYMBOLS[0], 
                starting_balance=getattr(self.settings, 'initial_balance', 10000),
                ending_balance=getattr(self.settings, 'initial_balance', 10000) + sum(t["pnl"] for t in mapped_trades),
            )

            report = await asyncio.to_thread(self.auto_coach.analyze, session_data)
            self.latest_coach_report = report.to_dict()
            self._last_coach_time = now
            
            logger.info("coach_analysis_done", extra={
                "score": report.performance_summary.get("session_score"),
                "rating": report.performance_summary.get("score_rating")
            })

            # Save report to logs/coach_report.md (absolute path)
            report_text = self.auto_coach.format_report_text(report)
            report_path = Path(__file__).resolve().parents[2] / "logs" / "coach_report.md"
            
            def _write_report():
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(report_text, encoding="utf-8")
                
            await asyncio.to_thread(_write_report)

        except Exception as e:
            logger.error("coach_analysis_error", extra={"error": str(e)})

    async def _run_cycle(self) -> None:
        """
        One execution cycle:
        - Fetch market data
        - Analyze + decide (all symbols)
        - Check position health
        - Learn (Training)
        - Auto Coach
        """
        self.cycle_count += 1
        cycle_start = time.monotonic()  # Use monotonic clock for timing
        self.pipeline.set_cycle(self.cycle_count)

        # ─── Read symbols from .env ───
        raw_trading_symbols = self.settings.trading_symbols
        raw_shadow_symbols = getattr(self.settings, 'shadow_symbols', "")
        
        # ─── Symbol Discovery (ALL) ───
        if raw_trading_symbols.strip().upper() == "ALL" and self.mt5 and self.mt5.is_connected():
            if not hasattr(self, "_discovered_symbols") or self.cycle_count % 120 == 1:
                self._discovered_symbols = self.mt5.get_available_symbols(forex_only=True)
                logger.info("symbol_discovery_refresh", extra={"count": len(self._discovered_symbols)})
            trading_symbols = self._discovered_symbols
        else:
            trading_symbols = [s.strip() for s in raw_trading_symbols.split(",") if s.strip()] or DEFAULT_SYMBOLS

        shadow_only_symbols = [s.strip() for s in raw_shadow_symbols.split(",") if s.strip() and s.strip() not in trading_symbols]
        all_symbols = trading_symbols + shadow_only_symbols
        self._shadow_only_symbols = set(shadow_only_symbols)

        # ─── Throttling: Trade Rate Limiter ───
        # Ensure we don't open too many trades globally per hour
        # Need persistent tracker? In-memory is fine for run lifecycle
        if not hasattr(self, "_trade_throttle"):
             self._trade_throttle = {
                 "hourly_count": 0,
                 "last_hour_reset": time.monotonic(),
                 "symbol_last_trade": {} # symbol -> monotonic time
             }
        
        # Reset hourly counter
        if time.monotonic() - self._trade_throttle["last_hour_reset"] > 3600:
             self._trade_throttle["hourly_count"] = 0
             self._trade_throttle["last_hour_reset"] = time.monotonic()

        MAX_HOURLY_TRADES = 6
        MIN_MINUTES_BETWEEN_TRADES_PER_SYMBOL = 30


        # ─── Per-cycle cache: session + account (called once) ───
        session = get_current_session()
        session_value = session.value

        # ─── Session Transition: reset cooldown + session guard on session change ───
        if self._last_session and session_value != self._last_session:
            logger.info("session_transition", extra={
                "from": self._last_session,
                "to": session_value,
                "cycle": self.cycle_count,
            })
            self.cooldown_mgr.reset_session()
            self.session_guard.reset_session(self._last_session)
            self.risk_dampener.reset_session()
        self._last_session = session_value

        # ─── MT5 Auto-reconnect: every 30 cycles if disconnected ───
        if self.mt5 and not self.mt5.is_connected() and self.cycle_count % 30 == 1:
            try:
                logger.info("mt5_auto_reconnect_attempt", extra={"cycle": self.cycle_count})
                await asyncio.to_thread(self.mt5.connect)
                logger.info("mt5_auto_reconnected", extra={"cycle": self.cycle_count})
            except Exception as e:
                logger.debug("mt5_auto_reconnect_failed", extra={
                    "error": str(e), "next_retry_cycle": self.cycle_count + 30,
                })

        account = None
        if self.mt5 and self.mt5.is_connected():
            try:
                account = await asyncio.to_thread(self.mt5.get_account_state)
            except Exception as e:
                logger.warning("account_info_failed", extra={"error": str(e)})

        daily_pl = await self._get_realized_daily_pl()
        if account is not None:
            account.daily_pl = daily_pl
            
            # Update Peak Equity
            if account.equity > self._peak_equity:
                self._peak_equity = account.equity
            account.peak_equity = self._peak_equity
            
        daily_locked, daily_lock_reason = self._evaluate_daily_trade_lock(daily_pl=daily_pl)

        # ─── Performance: reset terminal cache (1 MT5 check/cycle) ───
        reset_terminal_cache(self.cycle_count)

        # ─── Batch MT5 state: fetch spread, positions, market_open once ───
        mt5_states: dict[str, dict] = {}
        if self.mt5 and self.mt5.is_connected():
            mt5_states = await asyncio.to_thread(self.mt5.get_batch_mt5_state, all_symbols)

        # ─── Sentiment Refresh: every 60 cycles (~5 min) ───
        if self.sentiment_aggregator and self.cycle_count % 60 == 1:
            try:
                await self.sentiment_aggregator.refresh(all_symbols)
            except Exception as e:
                logger.debug("sentiment_refresh_error", extra={"error": str(e)})

        # ─── Process each symbol ───
        for symbol in all_symbols:
            # yield control to event loop for HTTP requests
            await asyncio.sleep(0)

            if self.kill_switch:
                logger.warning("kill_switch_active", extra={"symbol": symbol})
                break
            
            is_shadow_only = symbol in self._shadow_only_symbols
            if daily_locked:
                self.last_decisions[symbol] = {
                    "stage": "daily_lock",
                    "result": "blocked",
                    "reason": daily_lock_reason,
                    "action": "HOLD",
                    "confidence": 0.0,
                    "strategy": "daily_guard",
                    "cycle": self.cycle_count,
                }
                continue

            # ─── History Learner: skip bad hours ───
            should_trade, hour_reason = self._history_learner.should_trade_now(symbol)
            if not should_trade:
                self.last_decisions[symbol] = {
                    "stage": "history_filter",
                    "result": "blocked",
                    "reason": f"History filter: {hour_reason}",
                    "action": "HOLD",
                    "confidence": 0.0,
                    "strategy": "history_learner",
                    "cycle": self.cycle_count,
                }
                continue

            try:
                # Trade Coach: analyze recent trades (Throttled to once per hour to avoid spam)
                if self._symbol_processor.trade_coach:
                    if self.cycle_count % 1200 == 1:  # Run every ~1 hour (1200 cycles * 3s = 3600s)
                        await asyncio.to_thread(self._symbol_processor.trade_coach.analyze)

                await self._symbol_processor.process(
                    symbol=symbol, account=account,
                    session_value=session_value,
                    mt5_state=mt5_states.get(symbol),
                    cycle_count=self.cycle_count,
                    candle_map=TIMEFRAME_CANDLE_MAP,
                    regime_contexts=self.regime_contexts,
                    last_decisions=self.last_decisions,
                    tracked_positions=self._tracked_positions,
                    latest_coach_report=self.latest_coach_report,
                    mode=self.mode,
                    is_shadow_only=is_shadow_only,
                )
            except Exception as e:
                logger.error(
                    "symbol_processing_error",
                    extra={
                        "symbol": symbol,
                        "error": str(e),
                        "type": type(e).__name__,
                        "cycle": self.cycle_count,
                    },
                    exc_info=True,
                )

        # ─── Flush buffered traces (batch commit) ───
        if self.db:
            await asyncio.to_thread(self.db.flush_traces)

        # ─── Flush shadow trades ───
        if self.shadow_runner:
            await asyncio.to_thread(self.shadow_runner.flush)

        # ─── Position health checks (LIVE mode only) ───
        if self.mode.can_send_orders and self.mt5:
            await self._position_checker.check(
                account=account,
                tracked_positions=self._tracked_positions,
                last_decisions=self.last_decisions,
                cycle_count=self.cycle_count,
            )

            # ─── Smart Hedge: defensive hedging for losing positions ───
            if hasattr(self, '_smart_hedge') and self._smart_hedge:
                try:
                    await self._smart_hedge.check_and_hedge(
                        positions=list(self._tracked_positions.values()),
                    )
                except Exception as e:
                    logger.debug("smart_hedge_error", extra={"error": str(e)})

        # ─── History Learner: analyze every 120 cycles (~10 min) ───
        if self.cycle_count % 120 == 0 and self.cycle_count > 0:
            try:
                if self._history_learner.should_analyze():
                    import asyncio as _aio
                    await _aio.to_thread(self._history_learner.analyze, 5)
            except Exception as e:
                logger.debug("history_learner_error", extra={"error": str(e)})

        # ─── Brain Training: every 120 cycles (~10 min @ 5 sec/cycle) ───
        if self.trainer and self.cycle_count % 120 == 0 and self.cycle_count > 0:
            try:
                result = await self.trainer.run_training_cycle()
                logger.info("brain_training_done", extra=result)
            except Exception as e:
                logger.error("brain_training_error", extra={"error": str(e)})

        # ─── ML Retrain: alongside brain training ───
        if (
            self.ml_pattern_learner
            and self.cycle_count % 120 == 0
            and self.cycle_count > 0
            and getattr(self.settings, 'ml_retrain_with_training', True)
        ):
            try:
                ml_result = await self.ml_pattern_learner.train()
                logger.info("ml_retrain_done", extra=ml_result)
            except Exception as e:
                logger.debug("ml_retrain_error", extra={"error": str(e)})

        # ─── Auto Coach: every 15 min (check frequency inside method) ───
        if self.auto_coach:
            await self._run_coach_analysis()

        # ─── Self-Training: every N hours (configured in .env) ───
        if (
            self.training_orchestrator
            and getattr(self.settings, 'training_enabled', True)
            and not self.training_orchestrator.is_running
        ):
            training_interval_s = getattr(
                self.settings, 'training_interval_hours', 6.0
            ) * 3600
            now_mono = time.monotonic()
            if now_mono - self._last_training_time >= training_interval_s:
                self._last_training_time = now_mono
                asyncio.create_task(self._training_scheduler.run_self_training(self.cycle_count))

        # ─── Prune old traces: every 1,000 cycles (prevent DB bloat) ───
        if self.db and self.cycle_count % 1000 == 0:
            await asyncio.to_thread(self.db.prune_old_traces, keep_days=7)

        # ─── Cleanup BE records: every 500 cycles ───
        if self.cycle_count % 500 == 0:
            await asyncio.to_thread(cleanup_old_be_records)

        # ─── Prune shadow trades: every 1,000 cycles ───
        if self.db and self.cycle_count % 1000 == 0:
            await asyncio.to_thread(self.db.prune_old_shadow_trades, keep_days=7)

        # ─── Shadow Evaluation Training: every 60 cycles (~5 min) ───
        if self.db and self.trainer and self.cycle_count % 60 == 0 and self.cycle_count > 0:
            try:
                self._training_scheduler._shadow_candle_cache = self._symbol_processor._shadow_candle_cache
                asyncio.create_task(self._training_scheduler.run_shadow_evaluation())
            except Exception as e:
                logger.debug("shadow_eval_schedule_error", extra={"error": str(e)})

        # ─── Health emission + cycle timing: every 60 cycles ───
        if self.cycle_count % 60 == 0:
            cycle_ms = (time.monotonic() - cycle_start) * 1000
            # Gather intelligence status
            intel_status = {}
            if self.sentiment_aggregator:
                intel_status["sentiment_sources"] = sum(
                    1 for s in self.sentiment_aggregator._sentiments.values()
                    if s.sources_available > 0
                )
            if self.ml_pattern_learner:
                intel_status["ml_ready"] = self.ml_pattern_learner._model_ready
                intel_status["ml_accuracy"] = self.ml_pattern_learner._accuracy
            logger.info(
                "health_check",
                extra={
                    "cycle": self.cycle_count,
                    "symbols_count": len(all_symbols),
                    "cycle_ms": round(cycle_ms, 1),
                    "mode": self.mode.value,
                    "equity": account.equity if account else 0,
                    "open_positions": account.open_positions if account else 0,
                    "daily_pl": round(daily_pl, 2),
                    "daily_locked": daily_locked,
                    "session": session_value,
                    **intel_status,
                },
            )

        # ─── Data Retention Cleanup: Every ~30 minutes ───
        # LIVE: 600 cycles * 3s = 1800s = 30 min
        # DRY_RUN: 600 cycles * 5s = 3000s = 50 min (Close enough)
        if self.db and self.cycle_count % 600 == 1:
             # run cleanup in background
             asyncio.create_task(self._run_tick_cleanup())

    async def _get_realized_daily_pl(self) -> float:
        """Get today's realized P/L from SQLite journal (account currency baseline in system)."""
        if not self.db:
            return 0.0
        try:
            return float(await asyncio.to_thread(self.db.get_daily_pl))
        except Exception as e:
            logger.debug("daily_pl_read_error", extra={"error": str(e)})
            return 0.0

    def _evaluate_daily_trade_lock(self, daily_pl: float) -> tuple[bool, str]:
        """
        Lock new entries for the rest of the day after hitting daily target/stop.
        Existing positions are still managed by _check_positions().
        """
        today = date.today()

        if self._daily_lock_date and self._daily_lock_date != today:
            logger.info(
                "daily_trade_lock_reset",
                extra={
                    "previous_day": self._daily_lock_date.isoformat(),
                    "today": today.isoformat(),
                },
            )
            self._daily_lock_date = None
            self._daily_lock_reason = ""
            self._daily_lock_trigger_pl = 0.0

        if self._daily_lock_date == today:
            return True, self._daily_lock_reason

        target_amount = max(0.0, float(getattr(self.settings, "daily_target_amount", 0.0)))
        stop_amount = max(0.0, float(getattr(self.settings, "daily_stop_amount", 0.0)))
        if target_amount <= 0.0 and stop_amount <= 0.0:
            return False, ""

        reason = ""
        if target_amount > 0.0 and daily_pl >= target_amount:
            reason = f"DAILY_TARGET_REACHED ({daily_pl:.2f}/{target_amount:.2f})"
        elif stop_amount > 0.0 and daily_pl <= -stop_amount:
            reason = f"DAILY_STOP_REACHED ({daily_pl:.2f}/-{stop_amount:.2f})"

        if not reason:
            return False, ""

        self._daily_lock_date = today
        self._daily_lock_reason = reason
        self._daily_lock_trigger_pl = daily_pl

        logger.warning(
            "daily_trade_lock_activated",
            extra={
                "day": today.isoformat(),
                "reason": reason,
                "daily_pl": round(daily_pl, 2),
                "target_amount": target_amount,
                "stop_amount": stop_amount,
            },
        )

        if self.telegram:
            asyncio.create_task(
                self.telegram.notify_error(
                    title="DAILY TRADE LOCK",
                    details=(
                        f"{reason}\n"
                        f"day={today.isoformat()} "
                        f"daily_pl={daily_pl:.2f}"
                    ),
                )
            )
        return True, reason

    async def _run_tick_cleanup(self) -> None:
        """Tick Housekeeping: delete old ticks beyond retention (3 days)."""
        try:
            retention = getattr(self.settings, 'tick_retention_days', 3)
            if self.db:
                await asyncio.to_thread(self.db.cleanup_old_ticks, retention)
        except Exception as e:
            logger.error("tick_cleanup_error", extra={"error": str(e)})

    # ────────────────────────────────────────────────────────────────
    # _ingest_market_data() — Store Tick Data to SQLite
    # ────────────────────────────────────────────────────────────────
    async def _ingest_market_data(self, symbol: str) -> None:
        """Fetch and ingest new ticks to SQLite."""
        try:
            last_time = self._last_tick_time.get(symbol)
            if last_time is None:
                 self._last_tick_time[symbol] = int(time.time() * 1000) - 10000
                 last_time = self._last_tick_time[symbol]

            from app.mt5.market_data import fetch_ticks_since

            # Resolve Broker Symbol (e.g. XAUUSD -> XAUUSDc)
            broker_symbol = symbol
            if self.mt5 and hasattr(self.mt5, 'adapter'):
                broker_symbol = self.mt5.adapter.map_symbol(symbol)

            ticks = await asyncio.to_thread(fetch_ticks_since, broker_symbol, last_time)

            if not ticks:
                return

            # Ingest to SQLite (Store as INTERNAL symbol)
            if self.db:
                await asyncio.to_thread(self.db.ingest_ticks, symbol, ticks)

            # Update high-water mark
            max_ts = max(t['time'] for t in ticks)
            max_ts_ms = int(max_ts * 1000)
            if max_ts_ms > last_time:
                self._last_tick_time[symbol] = max_ts_ms

        except Exception as e:
            logger.debug("tick_ingest_error", extra={"symbol": symbol, "error": str(e)})

    # ────────────────────────────────────────────────────────────────
    # _process_symbol() — Process 1 symbol
    # ────────────────────────────────────────────────────────────────

    async def _process_symbol(
        self, symbol: str, account=None, session_value: str = "",
        mt5_state: dict | None = None,
    ) -> None:
        """
        Run execution pipeline for 1 symbol.

        Performance:
            - session_value: cached per cycle (no redundant get_current_session() calls)
            - Candle dedup: skip if same candle
            - Brain fast path: if Brain recommend + signal -> use immediately
        """
        # ─── 1. Profile — symbol info ───
        profile = None
        if self.mt5 and self.mt5.is_connected():
            profile = await asyncio.to_thread(self.mt5.get_symbol_info, symbol)

        if profile is None:
            from app.domain.models import SymbolProfile
            profile = SymbolProfile(symbol=symbol)

        # ─── 1.5 Tick Ingestion (SQLite) ───
        if self.db and self.mt5 and self.mt5.is_connected():
            await self._ingest_market_data(symbol)

        # ─── 2. Multi-timeframe candles (use pre-computed _needed_tfs) ───
        def _fetch_all_candles():
            # Resolve Broker Symbol (e.g. XAUUSD -> XAUUSDc)
            broker_symbol = symbol
            if self.mt5 and hasattr(self.mt5, 'adapter'):
                broker_symbol = self.mt5.adapter.map_symbol(symbol)

            result = {}
            for tf in self._needed_tfs:
                tf_key, cnt = TIMEFRAME_CANDLE_MAP.get(tf, ("M5", 250))
                # Pass BROKER symbol to MT5, but result maps to INTERNAL symbol logic
                c = fetch_candles(broker_symbol, timeframe=tf_key, count=cnt, cycle=self.cycle_count)
                if c is not None and len(c) >= 30:
                    result[tf] = c
            return result

        candles_by_tf = await asyncio.to_thread(_fetch_all_candles)

        m5_candles = candles_by_tf.get("M5")
        if m5_candles is None or len(m5_candles) < 30:
            logger.warning("symbol_skipped_no_candles", extra={
                "symbol": symbol,
                "reason": "insufficient_candles",
                "candle_count": len(m5_candles) if m5_candles is not None else 0,
                "timeframes_available": list(candles_by_tf.keys()),
                "cycle": self.cycle_count,
            })
            self.last_decisions[symbol] = {
                "result": "blocked",
                "reason": "insufficient_candles",
            }
            return

        # ─── 2.5 Populate Shadow Cache ───
        self._shadow_candle_cache[symbol] = m5_candles


        # ─── 3. Performance: Candle dedup ───
        last_row = m5_candles.iloc[-1]
        # Fix: 'time' is the DataFrame index, so we access it via .name
        candle_hash = f"{last_row.name}:{last_row['close']}"
        if candle_hash == self._last_candle_hash.get(symbol):
            # Candle unchanged — skip re-analysis (normal dedup, log only every 20 cycles)
            if self.cycle_count % 20 == 0:
                logger.debug("candle_dedup_skip", extra={"symbol": symbol, "cycle": self.cycle_count})
            return
        self._last_candle_hash[symbol] = candle_hash

        # ─── 4. Regime — Market condition analysis (Intelligence Core v2) ───
        regime_recommended: str | None = None
        regime_lot_multiplier = 1.0
        regime_param_overrides: dict[str, object] = {}

        if hasattr(self, '_regime_engine') and self._regime_engine:
            regime_intel = self._regime_engine.analyze(
                symbol=symbol, candles=m5_candles,
                session=session_value or "CLOSED",
                profile=getattr(self, '_personality_cache', {}).get(symbol),
            )
            regime_ctx = self._regime_engine.get_regime_context(regime_intel)
            regime_recommended = getattr(regime_intel, "recommended_strategy", "") or None
            rp = getattr(regime_intel, "risk_profile", {}) or {}
            try:
                regime_lot_multiplier = float(rp.get("lot_multiplier", 1.0))
            except (TypeError, ValueError):
                regime_lot_multiplier = 1.0
            regime_param_overrides = normalize_strategy_params({
                "sl_atr": rp.get("sl_atr"),
                "tp_rr": rp.get("tp_rr"),
            })
            # Store intelligence for later use (risk sizing, learning)
            if not hasattr(self, '_regime_intel'):
                self._regime_intel = {}
            self._regime_intel[symbol] = regime_intel
        else:
            regime_ctx = classify_regime(m5_candles)
        self.regime_contexts[symbol] = regime_ctx
        regime = regime_ctx.regime

        # ─── 4.5 Pattern Detection — Candlestick patterns (Candlestick Intelligence) ───
        try:
            pattern_signals = self._pattern_detector.detect_latest(m5_candles, lookback=3)
            self._pattern_signals[symbol] = pattern_signals
            if pattern_signals:
                summary = self._pattern_detector.summarize(pattern_signals)
                logger.debug("pattern_detected", extra={
                    "symbol": symbol,
                    "total": summary["total"],
                    "bullish": summary["bullish"],
                    "bearish": summary["bearish"],
                    "strongest": summary["strongest"],
                })
        except Exception as e:
            pattern_signals = []
            self._pattern_signals[symbol] = []
            logger.debug("pattern_detect_error", extra={"symbol": symbol, "error": str(e)})

        # ─── 4.6 Tick Volume + OHLC Microstructure Analysis ───
        tick_vol_signal = None
        try:
            tick_vol_signal = self._tick_volume_analyzer.analyze(m5_candles)
            self._tick_volume_signals[symbol] = tick_vol_signal
            if tick_vol_signal.is_valid and tick_vol_signal.reasons:
                logger.info("tick_volume_analysis", extra={
                    "symbol": symbol,
                    "tick_volume": tick_vol_signal.current_volume,
                    "score": tick_vol_signal.score,
                    "volume_trend": tick_vol_signal.volume_trend,
                    "is_climax": tick_vol_signal.is_climax,
                    "is_dryup": tick_vol_signal.is_dryup,
                    "buying_pressure": tick_vol_signal.buying_pressure,
                    "has_divergence": tick_vol_signal.has_divergence,
                })
        except Exception as e:
            logger.debug("tick_volume_analysis_error", extra={"symbol": symbol, "error": str(e)})

        # ─── 5. Session — use cached value from _run_cycle() ───
        if not session_value:
            session_value = get_current_session().value

        # ─── 6. Performance & Throttling Check ───
        # Check if we should skip strategy analysis to save CPU or prevent overtrading
        skip_analysis = False
        throttle_reason = ""
        
        # Global Hourly Limit
        if self._trade_throttle["hourly_count"] >= 6: # MAX_HOURLY_TRADES
             skip_analysis = True
             throttle_reason = "hourly_limit_reached"
             
        # Per-Symbol Cooldown
        last_trade_time = self._trade_throttle["symbol_last_trade"].get(symbol, 0)
        if time.monotonic() - last_trade_time < 1800: # 30 mins
             skip_analysis = True
             throttle_reason = "symbol_cooldown"

        # ─── 7. Strategy Selection & Analysis ───
        best_decision = None
        candidates = []
        
        if not skip_analysis:
            # Personality Analysis (only if analyzing)
            personality = self.personality_engine.analyze_symbol(symbol, m5_candles)

            # Smart Strategy Selection → Backtest Router + Brain + Regime
            brain_rec = None
            evolved_params_cache: dict[str, dict] = {}  # strategy_name → params
            if self.brain_memory:
                brain_rec = self.brain_memory.get_best_strategy(
                    symbol=symbol,
                    regime=regime.value,
                    session=session_value,
                )

            # Backtest Router
            router_pick = None
            if self.backtest_router:
                router_pick = self.backtest_router.get_best_strategy(symbol, regime.value)

            # Build candidate list
            added = set()
            is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
            is_silver = "XAG" in symbol.upper() or "SILVER" in symbol.upper()

            def _add_candidate(name: str) -> None:
                if not name or name in added:
                    return
                strategy_obj = self.factory._strategies.get(name)
                if strategy_obj is None:
                    return
                candidates.append((name, strategy_obj))
                added.add(name)

            # ─── DB-Driven Strategy Selection (no hardcoded cascades) ───
            # Factory handles: brain_rec → backtest_routing → registry → fallback
            selected = self.factory.select_strategy(
                symbol=symbol,
                regime=regime,
                session=session_value,
                brain_recommendation=brain_rec,
            )

            # Also try regime intelligence recommendation
            if regime_recommended and regime_recommended in self.factory._strategies:
                _add_candidate(regime_recommended)

            # Also try backtest router pick (if factory didn't already)
            if router_pick and router_pick in self.factory._strategies:
                _add_candidate(router_pick)

            # Add factory-selected strategy as top priority
            if selected:
                _add_candidate(selected.name)

            # Add regime-matched strategies from registered ones
            for strat_name, strategy in self.factory._strategies.items():
                if strat_name in added:
                    continue
                suitable_regimes = getattr(strategy, 'suitable_regimes', None) or getattr(strategy, 'regimes', None)
                if suitable_regimes and regime in suitable_regimes:
                    _add_candidate(strat_name)

            # Fallback: first few registered strategies
            if not candidates:
                for name in list(self.factory._strategies.keys())[:3]:
                    _add_candidate(name)

            # Keep compute cost bounded per cycle
            if len(candidates) > 10:
                candidates = candidates[:10]

            # ML & Sentiment
            ml_win_prob = 0.5
            dl_win_prob = 0.5
            sentiment_score = 0.0
            
            # ─── Deep Learning (LSTM) ───
            if getattr(self.pipeline, 'deep_learner', None):
                 # access via pipeline or master_loop attribute? 
                 # In main.py: loop.deep_learner = deep_learner
                 # But loop is self. So self.deep_learner should exist if injected.
                 pass

            if getattr(self, 'deep_learner', None):
                try:
                    dl_win_prob = self.deep_learner.predict_from_candles(m5_candles)
                except Exception: pass

            if self.ml_pattern_learner:
                try:
                    ml_win_prob = self.ml_pattern_learner.predict(m5_candles, regime=regime.value, session=session_value)
                except Exception: pass
            if self.sentiment_aggregator:
                try:
                    sentiment_score = self.sentiment_aggregator.get_sentiment(symbol).composite_score
                except Exception: pass

            confidence_boost = 0.0
            brain_pattern_direction = ""  # "bullish" / "bearish" / "" from brain's best patterns
            brain_pattern_wr = 0.0
            if self.recommender:
                try:
                    intel = self.recommender.recommend_with_intelligence(
                        symbol=symbol, regime=regime.value, session=session_value,
                        ml_win_prob=ml_win_prob, sentiment_score=sentiment_score,
                    )
                    confidence_boost = intel.get("confidence_boost", 0.0)
                    pers_rec = self.recommender.recommend_with_personality(
                        symbol=symbol, regime=regime.value, session=session_value, personality=personality,
                    )
                    confidence_boost += pers_rec.get("personality_boost", 0.0)

                    # ── Brain Pattern Direction — detect bearish/bullish from top pattern ──
                    best_patterns = intel.get("best_patterns") or []
                    if best_patterns:
                        _BEARISH_PATTERNS = {
                            "gravestone_doji", "shooting_star", "evening_star",
                            "bearish_engulfing", "dark_cloud_cover", "hanging_man",
                            "three_black_crows", "bearish_harami",
                        }
                        _BULLISH_PATTERNS = {
                            "hammer", "morning_star", "bullish_engulfing",
                            "dragonfly_doji", "piercing_line", "inverted_hammer",
                            "three_white_soldiers", "bullish_harami",
                        }
                        top = best_patterns[0]
                        top_name = top.get("pattern_name", "").lower()
                        brain_pattern_wr = top.get("win_rate", 0.0)
                        if top_name in _BEARISH_PATTERNS:
                            brain_pattern_direction = "bearish"
                        elif top_name in _BULLISH_PATTERNS:
                            brain_pattern_direction = "bullish"
                except Exception: pass

            # Run Analysis
            best_confidence = -1.0
            h1_candles = candles_by_tf.get("H1")

            for strat_name, strategy in candidates:
                tf = getattr(strategy, 'timeframe', 'M5')
                candles = candles_by_tf.get(tf, m5_candles)

                # ─── Load Evolved Params from DB (cached per cycle) ───
                try:
                    sig = inspect.signature(strategy.analyze)
                    accepts_var_kwargs = any(
                        p.kind == inspect.Parameter.VAR_KEYWORD
                        for p in sig.parameters.values()
                    )
                except (TypeError, ValueError):
                    accepts_var_kwargs = False

                strategy_kwargs = dict(regime_param_overrides) if accepts_var_kwargs else {}
                if self.brain_memory and strat_name not in evolved_params_cache:
                    for r in [regime.value, "ALL"]:
                        ep = await asyncio.to_thread(
                            self.brain_memory.get_evolved_params,
                            strategy_name=strat_name, symbol=symbol, regime=r,
                        )
                        if ep:
                            evolved_params_cache[strat_name] = ep
                            break
                    else:
                        evolved_params_cache[strat_name] = {}

                ep = evolved_params_cache.get(strat_name, {})
                if ep and accepts_var_kwargs:
                    strategy_kwargs.update(normalize_strategy_params(ep))

                if strategy_kwargs:
                    logger.debug("strategy_param_overrides", extra={
                        "strategy": strat_name,
                        "symbol": symbol,
                        "params": strategy_kwargs,
                    })

                try:
                    analysis_kwargs = {
                        "session": session_value,
                        "h1_candles": h1_candles,
                        "regime_context": regime_ctx,
                    }
                    # ─── Pipe buy/sell pressure data to strategy ───
                    if tick_vol_signal and tick_vol_signal.is_valid:
                        analysis_kwargs["pressure"] = {
                            "buying_pressure": tick_vol_signal.buying_pressure,
                            "selling_pressure": tick_vol_signal.selling_pressure,
                            "score": tick_vol_signal.score,
                            "is_climax": tick_vol_signal.is_climax,
                            "is_dryup": tick_vol_signal.is_dryup,
                            "ad_line_trend": tick_vol_signal.ad_line_trend,
                            "volume_trend": tick_vol_signal.volume_trend,
                            "body_conviction": tick_vol_signal.body_conviction,
                            "has_divergence": tick_vol_signal.has_divergence,
                        }
                    if strategy_kwargs:
                        analysis_kwargs.update(strategy_kwargs)

                    decision = analyze_with_fallback(
                        strategy=strategy,
                        candles=candles,
                        profile=profile,
                        regime=regime,
                        extra_kwargs=analysis_kwargs,
                    )
                    if decision.action != Action.HOLD:
                        # ── Pattern Confidence Boost ──
                        pattern_score = self._pattern_scorer.score(
                            signals=pattern_signals,
                            strategy_direction=decision.action.value,
                            symbol=symbol,
                            regime=regime.value,
                        )
                        # ── Tick Volume Microstructure Boost/Block ──
                        tv_boost = 0.0
                        tv_block = False
                        if tick_vol_signal and tick_vol_signal.is_valid:
                            tv_boost = tick_vol_signal.score * 0.005  # ±0.1 max
                            # Block on climax (exhaustion risk)
                            if tick_vol_signal.is_climax:
                                tv_block = True
                                decision.action = Action.HOLD
                                decision.reason = (decision.reason or "") + " | TickVol CLIMAX (exhaustion)"
                                logger.info("tick_volume_climax_block", extra={
                                    "symbol": symbol, "strategy": strat_name,
                                    "reasons": tick_vol_signal.reasons,
                                })
                                continue
                            # Block on dry-up (no participation)
                            if tick_vol_signal.is_dryup:
                                tv_block = True
                                decision.action = Action.HOLD
                                decision.reason = (decision.reason or "") + " | TickVol DRY-UP (no participation)"
                                logger.info("tick_volume_dryup_block", extra={
                                    "symbol": symbol, "strategy": strat_name,
                                })
                                continue

                        # ── MTF Confluence Boost ──
                        mtf_boost = 0.0
                        mtf_score_val = 0.0
                        try:
                            mtf_result = self._mtf_engine.score(
                                candles_by_tf=candles_by_tf,
                                direction=decision.action.value,
                            )
                            mtf_boost = mtf_result.confidence_boost
                            mtf_score_val = mtf_result.total
                            if mtf_result.should_block:
                                decision.action = Action.HOLD
                                decision.reason = (decision.reason or "") + f" | MTF CONFLICT (score={mtf_result.total:.0f})"
                                logger.info("mtf_confluence_block", extra={
                                    "symbol": symbol, "strategy": strat_name,
                                    "mtf_score": mtf_result.total,
                                    "reason": mtf_result.reason,
                                })
                                continue
                        except Exception as me:
                            logger.debug("mtf_score_error", extra={"symbol": symbol, "error": str(me)})

                        # ── Entry Quality Boost ──
                        entry_boost = 0.0
                        entry_grade = "B"
                        try:
                            entry_quality = self._entry_optimizer.evaluate(
                                candles=candles,
                                direction=decision.action.value,
                            )
                            entry_boost = entry_quality.confidence_boost
                            entry_grade = entry_quality.grade
                        except Exception as ee:
                            logger.debug("entry_quality_error", extra={"symbol": symbol, "error": str(ee)})

                        # ── Outcome Edge Boost ──
                        edge_boost = 0.0
                        try:
                            edge_boost = self._outcome_analyzer.get_edge_boost(
                                strategy_name=strat_name,
                                symbol=symbol,
                                regime=regime.value,
                                session=session_value,
                            )
                        except Exception:
                            pass

                        # ── Web Knowledge Filter (Online Learning) ──
                        web_boost = 0.0
                        web_reason = ""
                        try:
                            if hasattr(self, 'web_researcher') and self.web_researcher:
                                web_mod, web_reason = self.web_researcher.get_web_confidence_modifier(
                                    symbol, decision.action.value,
                                )
                                web_boost = web_mod - 1.0  # e.g. 1.05 → +0.05
                        except Exception:
                            pass
                        # ── Brain Pattern-Direction Conflict Penalty ──
                        brain_conflict_penalty = 0.0
                        if brain_pattern_direction:
                            signal_dir = "bullish" if decision.action == Action.BUY else "bearish"
                            if brain_pattern_direction != signal_dir:
                                # Bearish brain pattern + BUY signal (or vice versa) = conflict
                                brain_conflict_penalty = -0.25 if brain_pattern_wr >= 0.70 else -0.15
                                logger.info("brain_pattern_conflict", extra={
                                    "symbol": symbol,
                                    "strategy": strat_name,
                                    "signal": decision.action.value,
                                    "brain_direction": brain_pattern_direction,
                                    "brain_pattern_wr": round(brain_pattern_wr, 2),
                                    "penalty": brain_conflict_penalty,
                                })

                        total_boost = confidence_boost + pattern_score.confidence_boost + tv_boost + mtf_boost + entry_boost + edge_boost + web_boost + brain_conflict_penalty
                        boosted_confidence = max(0.0, min(1.0, decision.confidence + total_boost))

                        # Pattern conflict → skip
                        if pattern_score.should_skip:
                            decision.action = Action.HOLD
                            decision.reason = (decision.reason or "") + f" | Pattern SKIP: {pattern_score.reason}"
                            logger.info("pattern_skip_trade", extra={
                                "symbol": symbol,
                                "strategy": strat_name,
                                "conflicting": pattern_score.conflicting_patterns,
                            })
                            continue

                        if boosted_confidence > best_confidence:
                            best_confidence = boosted_confidence
                            decision.confidence = boosted_confidence
                            # Tag patterns in decision
                            if not hasattr(decision, 'tags') or decision.tags is None:
                                decision.tags = {}
                            decision.tags["patterns_aligned"] = pattern_score.aligned_patterns
                            decision.tags["patterns_conflicting"] = pattern_score.conflicting_patterns
                            decision.tags["pattern_boost"] = pattern_score.confidence_boost
                            decision.tags["pattern_reason"] = pattern_score.reason
                            decision.tags["pattern_strongest"] = pattern_score.strongest_pattern
                            # Tag tick volume microstructure
                            if tick_vol_signal and tick_vol_signal.is_valid:
                                decision.tags["tv_score"] = tick_vol_signal.score
                                decision.tags["tv_trend"] = tick_vol_signal.volume_trend
                                decision.tags["tv_buy_pressure"] = tick_vol_signal.buying_pressure
                                decision.tags["tv_sell_pressure"] = tick_vol_signal.selling_pressure
                                decision.tags["tv_divergence"] = tick_vol_signal.has_divergence
                                decision.tags["tv_body_conviction"] = tick_vol_signal.body_conviction
                                decision.tags["tv_ad_trend"] = tick_vol_signal.ad_line_trend
                            # Tag super-human intelligence
                            decision.tags["mtf_score"] = round(mtf_score_val, 1)
                            decision.tags["mtf_boost"] = round(mtf_boost, 3)
                            decision.tags["entry_grade"] = entry_grade
                            decision.tags["entry_boost"] = round(entry_boost, 3)

                            decision.tags["edge_boost"] = round(edge_boost, 3)
                            decision.tags["web_boost"] = round(web_boost, 3)
                            decision.tags["web_reason"] = web_reason
                            decision.tags["ml_prob"] = round(ml_win_prob, 2)
                            decision.tags["dl_prob"] = round(dl_win_prob, 2)
                            best_decision = decision
                            if brain_rec and strat_name == brain_rec:
                                break
                except Exception as e:
                    logger.debug("strategy_error", extra={"strategy": strat_name, "symbol": symbol, "error": str(e)})

        # If skipped or no decision
        if best_decision is None:
            if skip_analysis:
                 best_decision = Decision(
                     action=Action.HOLD,
                     symbol=symbol,
                     strategy_name="Throttler",
                     reason=f"Throttled: {throttle_reason}",
                     confidence=0.0
                 )
            else:
                best_decision = self.factory.get_decision(
                    candles=m5_candles, profile=profile, regime=regime, session=session_value,
                    brain_recommendation=brain_rec if 'brain_rec' in locals() else None,
                    personality=personality if 'personality' in locals() else None,
                )

        # ─── 8. Pipeline Execution ───
        if account is None:
            from app.domain.models import AccountState
            account = AccountState(balance=100.0, equity=100.0)

        performance_metrics = {}
        if self.latest_coach_report:
            performance_metrics = dict(
                self.latest_coach_report.get("performance_summary") or {}
            )
        performance_metrics["regime_lot_multiplier"] = regime_lot_multiplier
        # Pass MTF score and edge to sizing for volatility-adaptive sizing
        if best_decision and hasattr(best_decision, 'tags') and isinstance(best_decision.tags, dict):
            performance_metrics["mtf_score"] = best_decision.tags.get("mtf_score", 50)
            performance_metrics["strategy_edge"] = best_decision.tags.get("edge_boost", 0)

        pipeline_result = await self.pipeline.execute(
            decision=best_decision,
            profile=profile,
            account=account,
            regime=regime.value,
            session=session_value,
            mt5_state=mt5_state,
            candles=m5_candles,
            personality=personality if 'personality' in locals() else None,
            performance_metrics=performance_metrics,
            regime_context=regime_ctx,
        )

        # ─── 9. Throttling & Tracking Update ───
        # If trade executed successfully:
        # 1. Update throttling counters
        # 2. Eagerly track position for exit detection (fast trades)
        if pipeline_result["result"] == "ok" and pipeline_result.get("ticket"):
             ticket = pipeline_result["ticket"]
             entry_price = pipeline_result.get("entry_price", 0.0)
             
             # ─── Setup Ghost Guard (Stealth SL/TP) ───
             if pipeline_result.get("order_plan") and pipeline_result["order_plan"].ghost_protocol:
                 self.ghost_guard.set_ghost(
                     ticket=ticket,
                     virtual_sl=pipeline_result["order_plan"].stop_loss or 0.0,
                     virtual_tp=pipeline_result["order_plan"].take_profit or 0.0,
                 )
                 logger.info("ghost_guard_enrolled", extra={
                     "ticket": ticket,
                     "symbol": symbol,
                     "virtual_sl": pipeline_result["order_plan"].stop_loss,
                     "virtual_tp": pipeline_result["order_plan"].take_profit,
                 })

             self._trade_throttle["hourly_count"] += 1
             self._trade_throttle["symbol_last_trade"][symbol] = time.monotonic()
             
             # Eagerly track!
             self._tracked_positions[ticket] = {
                 "symbol": symbol,
                 "type": best_decision.action.value,  # BUY/SELL
                 "volume": pipeline_result.get("broker_volume", best_decision.lot_size if hasattr(best_decision, 'lot_size') else 0.01),
                 "price_open": entry_price,
                 "profit": 0.0,
                 "sl": best_decision.stop_loss,
                 "strategy": best_decision.strategy_name,
                 "regime": regime.value,
                 "session": session_value,
                 "patterns": [s.name for s in pattern_signals] if pattern_signals else [],
             }

             # ─── Outcome Analyzer: record trade context at open ───
             try:
                 from app.brain.outcome_analyzer import TradeContext
                 tags = best_decision.tags or {}
                 self._outcome_analyzer.record_trade_open(TradeContext(
                     ticket=ticket,
                     symbol=symbol,
                     strategy_name=best_decision.strategy_name,
                     regime=regime.value,
                     session=session_value,
                     confidence=best_decision.confidence,
                     mtf_score=tags.get("mtf_score", 0),
                     entry_quality_grade=tags.get("entry_grade", ""),
                     entry_quality_score=tags.get("entry_boost", 0),
                     ml_win_prob=ml_win_prob if 'ml_win_prob' in locals() else 0.5,
                     sentiment_score=sentiment_score if 'sentiment_score' in locals() else 0.0,
                     patterns_aligned=tags.get("patterns_aligned", []),
                     patterns_conflicting=tags.get("patterns_conflicting", []),
                     direction=best_decision.action.value,
                     entry_price=entry_price,
                     stop_loss=best_decision.stop_loss or 0,
                     take_profit=best_decision.take_profit or 0,
                 ))
             except Exception as oe:
                 logger.debug("outcome_record_open_error", extra={"ticket": ticket, "error": str(oe)})
             
             logger.info("throttle_and_tracking_updated", extra={
                 "hourly": self._trade_throttle["hourly_count"],
                 "symbol": symbol,
                 "tracked_ticket": ticket
             })

        # ─── 10. Brain Learning & Stats ───
        if self.trainer:
            try:
                await self.trainer.update_regime_stats(
                    symbol=symbol,
                    regime=regime.value,
                    session=session_value,
                    atr=float(m5_candles['close'].pct_change().std() * 100) if len(m5_candles) > 20 else 0,
                )
            except Exception: pass

        self.last_decisions[symbol] = {
            "stage": pipeline_result["stage"],
            "result": pipeline_result["result"],
            "reason": pipeline_result["reason"],
            "action": best_decision.action.value,
            "confidence": best_decision.confidence,
            "strategy": best_decision.strategy_name,
            "regime": regime.value,
            "session": session_value,
            "cycle": self.cycle_count,
            "sl": best_decision.stop_loss,
            "tp": best_decision.take_profit,
            "strategies_tried": [c[0] for c in candidates],
            "candidates": len(candidates),
        }

        # ─── 10. Shadow Runner — ทดสอบ strategy อื่นๆ คู่ขนาน ───
        if self.shadow_runner:
            try:
                equity = account.equity if account else 10000.0
                self.shadow_runner.run_shadow(
                    symbol=symbol,
                    candles_by_tf=candles_by_tf,
                    profile=profile,
                    regime=regime,
                    session=session_value,
                    live_strategy=best_decision.strategy_name,
                    cycle=self.cycle_count,
                    account_equity=equity,
                )
                # Cache M5 candles for shadow evaluator
                m5 = candles_by_tf.get("M5")
                if m5 is not None and len(m5) > 0:
                    self._shadow_candle_cache[symbol] = m5
            except Exception as e:
                logger.debug("shadow_runner_error", extra={
                    "symbol": symbol, "error": str(e),
                })

    # ────────────────────────────────────────────────────────────────
    # _check_positions() — Position Health Check
    # ────────────────────────────────────────────────────────────────

    async def _check_positions(self, account=None) -> None:
        """
        Check health of all positions (LIVE mode only).

        Performs 7 tasks:
            0. Hedging Guard: check risk and Hedge if needed (Critical Priority)
            1. Position Close Detection: detect closed positions -> notify Telegram
            2. Break-Even: if profit >= +1R -> move SL to entry price
            3. Trailing Stop: trail SL with profit (ATR/fixed)
            4. Profit Lock: lock profit in tiers
            5. Manual Trade Protection: set SL for manual trades (magic=0)
            6. PostFill: verify all positions have SL -> close immediately if not
        """
        if not self.mt5:
            return

        positions = await asyncio.to_thread(self.mt5.get_positions)

        # ─── 0. Hedging Guard (Critical) ───
        # ─── 0. Hedging Guard (Critical) ───
        if account and getattr(self.settings, 'hedge_enable', False):
            await self.hedge_manager.monitor_risks(positions, account.equity, account.balance)

        # ─── Position Close Detection: detect disappeared tickets ───
        current_tickets = {p["ticket"]: p for p in positions}

        if self._tracked_positions:
            closed_tickets = set(self._tracked_positions.keys()) - set(current_tickets.keys())

            for ticket in closed_tickets:
                prev = self._tracked_positions[ticket]
                # Fetch deal info from MT5 history
                deal_info = await asyncio.to_thread(self.mt5.get_closed_deal_info, ticket)

                profit = deal_info["profit"] if deal_info else prev.get("profit", 0.0)
                close_price = deal_info["price_open"] if deal_info else 0.0  # price_open ของ deal out = close price
                reason = deal_info.get("comment", "") if deal_info else "unknown"

                logger.info("position_closed_detected", extra={
                    "ticket": ticket,
                    "symbol": prev.get("symbol", ""),
                    "profit": profit,
                    "reason": reason,
                })

                # Send Telegram notification
                if self.telegram:
                    asyncio.create_task(self.telegram.notify_trade_close(
                        symbol=prev.get("symbol", "?"),
                        ticket=ticket,
                        action=prev.get("type", ""),
                        lot_size=prev.get("volume", 0.0),
                        profit=profit,
                        entry_price=prev.get("price_open", 0.0),
                        close_price=close_price,
                        reason=reason,
                        mode=self.mode.value,
                    ))

                # ─── Brain Learning: record trade outcome to AI memory ───
                if self.brain_memory:
                    try:
                        entry_price = prev.get("price_open", 0.0)
                        sl_price = prev.get("sl", 0.0)
                        risk_dist = abs(entry_price - sl_price) if sl_price > 0 else 1.0
                        rr = profit / (risk_dist * prev.get("volume", 0.01) * 100) if risk_dist > 0 else 0.0

                        self.brain_memory.record_trade_outcome(
                            strategy_name=prev.get("strategy", "unknown"),
                            symbol=prev.get("symbol", ""),
                            regime=prev.get("regime", "UNKNOWN"),
                            session=prev.get("session", ""),
                            profit_usd=profit,
                            risk_reward=round(rr, 2),
                        )
                        logger.info("brain_trade_recorded", extra={
                            "ticket": ticket,
                            "symbol": prev.get("symbol"),
                            "profit": profit,
                            "rr": round(rr, 2),
                            "strategy": prev.get("strategy", "unknown"),
                        })

                        # ─── Pattern Learning: record per-pattern outcomes ───
                        entry_patterns = prev.get("patterns", [])
                        if entry_patterns and self.brain_memory:
                            try:
                                is_win = profit > 0
                                self.brain_memory.record_pattern_outcomes(
                                    symbol=prev.get("symbol", ""),
                                    regime=prev.get("regime", "UNKNOWN"),
                                    pattern_win_rates={
                                        p: (1.0 if is_win else 0.0)
                                        for p in entry_patterns
                                    },
                                    patterns_found={p: 1 for p in entry_patterns},
                                )
                                logger.info("pattern_outcome_recorded", extra={
                                    "ticket": ticket,
                                    "patterns": entry_patterns,
                                    "is_win": is_win,
                                    "profit": profit,
                                })
                            except Exception as pe:
                                logger.debug("pattern_record_error", extra={
                                    "ticket": ticket, "error": str(pe),
                                })

                    except Exception as e:
                        logger.warning("brain_record_error", extra={
                            "ticket": ticket, "error": str(e),
                        })

                # ─── Outcome Analyzer: record trade close for learning ───
                try:
                    self._outcome_analyzer.record_trade_close(
                        ticket=ticket,
                        pnl=profit,
                        hold_bars=0,  # TODO: compute from tracked open_time
                        hold_seconds=0.0,
                        exit_type=reason or "unknown",
                    )
                except Exception as oe:
                    logger.debug("outcome_record_close_error", extra={
                        "ticket": ticket, "error": str(oe),
                    })

                # ─── Online Learner: learn from every closed trade ───
                try:
                    if hasattr(self, 'online_learner') and self.online_learner:
                        self.online_learner.learn_from_trade({
                            "symbol": prev.get("symbol", ""),
                            "direction": prev.get("type", ""),
                            "strategy": prev.get("strategy", "unknown"),
                            "profit": profit,
                            "session": prev.get("session", ""),
                            "hour": datetime.now(timezone.utc).hour,
                            "day_of_week": datetime.now(timezone.utc).weekday(),
                            "regime": prev.get("regime", "UNKNOWN"),
                        })
                except Exception as le:
                    logger.debug("online_learner_error", extra={
                        "ticket": ticket, "error": str(le),
                    })

                # ─── Overtrading Protection: record win/loss to cooldown + dampener ───
                closed_symbol = prev.get("symbol", "")
                if profit < 0:
                    self.cooldown_mgr.record_loss(closed_symbol)
                    self.risk_dampener.record_loss(closed_symbol)
                    logger.info("safety_loss_recorded", extra={
                        "ticket": ticket,
                        "symbol": closed_symbol,
                        "profit": profit,
                        "cooldown": self.cooldown_mgr.get_status(closed_symbol),
                        "dampener": self.risk_dampener.get_status(closed_symbol),
                    })
                elif profit > 0:
                    self.cooldown_mgr.record_win(closed_symbol)
                    self.risk_dampener.record_win(closed_symbol)
                    logger.info("safety_win_recorded", extra={
                        "ticket": ticket,
                        "symbol": closed_symbol,
                        "profit": profit,
                    })

        # Update tracked positions (keep strategy/regime/session for brain learning)
        for p in positions:
            ticket = p["ticket"]
            if ticket in self._tracked_positions:
                # Update profit only (other values remain)
                self._tracked_positions[ticket]["profit"] = p["profit"]
                self._tracked_positions[ticket]["sl"] = p.get("sl", 0)
            else:
                # New position — get strategy/regime/session from last_decisions
                sym = p["symbol"]
                last_dec = self.last_decisions.get(sym, {})
                strategy_name = last_dec.get("strategy", "unknown")
                
                # Mark as newly tracked — skip Time Decay this cycle
                if not hasattr(self, '_newly_tracked_tickets'):
                    self._newly_tracked_tickets = set()
                self._newly_tracked_tickets.add(ticket)
                
                # If strategy='unknown', this is a Manual Trade or Bot just started
                # Only notify for TRULY new positions (not startup detection)
                if self.telegram and strategy_name == "unknown" and self.cycle_count > 1:
                    # Calculate actual risk from SL distance
                    sl_price = p.get("sl", 0.0)
                    entry = p["price_open"]
                    vol = p["volume"]
                    risk_usd = 0.0
                    risk_pct = 0.0
                    
                    if sl_price > 0:
                        dist = abs(entry - sl_price)
                        contract_size = 1.0  # default fallback
                        if self.mt5:
                            try:
                                sym_profile = self.mt5.get_symbol_info(sym)
                                if sym_profile:
                                    contract_size = sym_profile.contract_size
                            except Exception as e:
                                logger.warning("manual_trade_profile_error", extra={
                                    "symbol": sym, "error": str(e),
                                })
                        risk_usd = dist * vol * contract_size
                        if account and account.equity > 0:
                            risk_pct = (risk_usd / account.equity) * 100.0

                    asyncio.create_task(self.telegram.notify_trade_open(
                        symbol=sym,
                        action=p["type"],
                        lot_size=vol,
                        entry_price=entry,
                        stop_loss=sl_price,
                        take_profit=p.get("tp", 0.0),
                        risk_usd=risk_usd,
                        risk_pct=risk_pct,
                        strategy_name="Manual/External",
                        ticket=ticket,
                        mode=self.mode.value
                    ))
                elif self.cycle_count <= 1:
                    logger.info("startup_position_tracked", extra={
                        "ticket": ticket, "symbol": sym, "strategy": strategy_name,
                    })

                self._tracked_positions[ticket] = {
                    "symbol": sym,
                    "type": p["type"],
                    "volume": p["volume"],
                    "price_open": p["price_open"],
                    "profit": p["profit"],
                    "sl": p.get("sl", 0),
                    "strategy": strategy_name if strategy_name != "unknown" else "Manual/External",
                    "regime": last_dec.get("regime", "UNKNOWN"),
                    "session": last_dec.get("session", ""),
                }

                # ─── Auto-setup Trailing Stop + TP for new positions ───
                if getattr(self.settings, 'trailing_auto_setup', True):
                    # Auto trailing stop handled by TrailingManager globally
                    pass

                    # Auto TP management
                    tp_mode = getattr(self.settings, 'tp_management_mode', 'off')
                    if tp_mode != "off":
                        tiers = [
                            TPTier(r_target=self.settings.tp_partial_tier1_r,
                                   close_pct=self.settings.tp_partial_tier1_pct, move_sl_to="be"),
                            TPTier(r_target=self.settings.tp_partial_tier2_r,
                                   close_pct=self.settings.tp_partial_tier2_pct, move_sl_to="prev_tp"),
                            TPTier(r_target=self.settings.tp_partial_tier3_r,
                                   close_pct=self.settings.tp_partial_tier3_pct, move_sl_to=None),
                        ]
                        tp_cfg = TPConfig(
                            mode=tp_mode,
                            tiers=tiers,
                            atr_tp_multiplier=getattr(self.settings, 'tp_dynamic_atr_mult', 3.0),
                            trailing_tp_atr_distance=getattr(self.settings, 'tp_trailing_distance_atr', 0.5),
                        )
                        self.tp_manager.set_tp(ticket, tp_cfg, volume=p["volume"])

        # Remove tickets that disappeared (already processed above)
        active_tickets = {p["ticket"] for p in positions}
        self._tracked_positions = {
            t: v for t, v in self._tracked_positions.items()
            if t in active_tickets
        }

        for pos in positions:
            ticket = pos["ticket"]
            entry = pos["price_open"]       # Entry price
            current = pos["price_current"]  # Current price
            sl = pos["sl"]                  # Current Stop Loss
            is_buy = pos["type"] == "BUY"   # Buy or Sell

            # ─── Break-Even Check ───
            # If profit >= +1R (per settings.breakeven_r_multiple) -> move SL to entry
            if sl > 0 and should_move_to_breakeven(
                ticket=ticket,
                entry_price=entry,
                current_price=current,
                stop_loss=sl,
                r_multiple=self.settings.breakeven_r_multiple,
                is_buy=is_buy,
            ):
                success = await asyncio.to_thread(self.mt5.modify_sl, ticket, entry)  # Move SL -> entry price
                if success:
                    mark_be_moved(ticket)  # Mark as BE moved (prevent spam)
                    logger.info("be_move_success", extra={
                        "ticket": ticket,
                        "symbol": pos["symbol"],
                        "entry": entry,
                        "stage": "be_move",
                        "result": "ok",
                    })

        # ─── Ghost Guard: Virtual SL/TP (check first — may close position) ───
        await asyncio.to_thread(self.ghost_guard.check_all, positions)

        # ─── Fetch ATR values for trailing + TP ───
        atr_values: dict[str, float] = {}
        candles_cache_all: dict = {}
        position_symbols = {p["symbol"] for p in positions}
        for sym in position_symbols:
            # Resolve broker symbol if possible
            broker_sym = sym
            if self.mt5 and hasattr(self.mt5, 'adapter'):
                broker_sym = self.mt5.adapter.map_symbol(sym)

            c = fetch_candles(broker_sym, timeframe="M5", count=50, cycle=self.cycle_count)
            if c is not None and len(c) >= 15:
                candles_cache_all[sym] = c
                # Calculate ATR
                import numpy as np
                high = c["high"].values
                low = c["low"].values
                close = c["close"].values
                tr = np.maximum(
                    high[1:] - low[1:],
                    np.maximum(
                        np.abs(high[1:] - close[:-1]),
                        np.abs(low[1:] - close[:-1]),
                    ),
                )
                atr_values[sym] = float(np.mean(tr[-14:]))

        # ─── Unified Position Guardian: Trailing + Profit Lock + TP + Manual Protection ───
        # Handles ALL positions (bot + manual) with auto-enrollment
        guardian_result = await asyncio.to_thread(
            self.guardian.process_all, positions, atr_values, candles_cache_all,
        )

        # Telegram notifications for Guardian actions
        if self.telegram:
            if guardian_result.get("manual_protected", 0) > 0:
                asyncio.create_task(self.telegram._send(
                    f"🛡️ Guardian: {guardian_result['manual_protected']} manual trades protected"
                ))
            if guardian_result.get("trailing_actions", 0) > 0:
                asyncio.create_task(self.telegram._send(
                    f"📈 Guardian: {guardian_result['trailing_actions']} trailing SL updates"
                ))
            if guardian_result.get("lock_actions", 0) > 0:
                asyncio.create_task(self.telegram._send(
                    f"🔒 Guardian: {guardian_result['lock_actions']} profit locks triggered"
                ))

        # ─── Legacy TrailingManager (BE + ratchet — kept for gold-specific logic) ───
        await self.trailing_manager.process_all_positions()

        # ─── Efficiency Exits: Time Decay + RSI Pulse ───
        # Close stale/exhausted positions to free capital for better setups
        # Skip positions that were just tracked this cycle (bot restart detection)
        newly_tracked = getattr(self, '_newly_tracked_tickets', set())
        # Dedup: only send exit notification once per ticket
        if not hasattr(self, '_efficiency_exit_sent'):
            self._efficiency_exit_sent: set[int] = set()
        for pos_dict in positions:
            ticket = pos_dict["ticket"]
            if ticket in newly_tracked:
                continue  # Don't time-decay a position we just discovered
            if ticket in self._efficiency_exit_sent:
                continue  # Already sent exit for this ticket — skip spam
            symbol = pos_dict["symbol"]
            entry = pos_dict["price_open"]
            current = pos_dict["price_current"]
            sl = pos_dict.get("sl", 0)
            is_buy = pos_dict["type"] == "BUY"
            direction = "BUY" if is_buy else "SELL"

            # Calculate R
            if is_buy:
                profit_pts = current - entry
                sl_dist = entry - sl if sl > 0 else 5.0
            else:
                profit_pts = entry - current
                sl_dist = sl - entry if sl > 0 else 5.0
            if sl_dist <= 0: sl_dist = 5.0
            current_r = profit_pts / sl_dist

            # Time Decay: check elapsed time
            open_time = pos_dict.get("time", 0)
            if open_time > 0:
                now_ts = time.time()
                elapsed_min = (now_ts - open_time) / 60.0 if open_time < 1e12 else 0

                # Create a simple namespace for pos.ticket
                class _PosRef:
                    def __init__(self, t): self.ticket = t
                pos_ref = _PosRef(ticket)

                td_result = self.trailing_manager.check_time_decay(pos_ref, elapsed_min, current_r)
                if td_result and td_result.get("action") == "CLOSE":
                    logger.info("efficiency_time_decay", extra={
                        "ticket": ticket, "symbol": symbol,
                        "reason": td_result["reason"],
                    })
                    if self.mode == TradingMode.LIVE:
                        await asyncio.to_thread(self.mt5.close_position, ticket)
                        self._efficiency_exit_sent.add(ticket)
                        if self.telegram:
                            asyncio.create_task(self.telegram._send(
                                f"⏰ Time Decay Exit\n#{ticket} {symbol}\n{td_result['reason']}"
                            ))
                    continue  # Skip pulse check if already closing

            # Pulse Exit: check RSI
            if symbol in candles_cache_all:
                import pandas_ta as _ta
                _c = candles_cache_all[symbol]
                _rsi = _ta.rsi(_c["close"], length=14)
                if _rsi is not None and len(_rsi) > 0:
                    rsi_val = float(_rsi.iloc[-1])
                    class _PosRef2:
                        def __init__(self, t): self.ticket = t
                    pos_ref2 = _PosRef2(ticket)

                    pulse_result = self.trailing_manager.check_pulse_exit(pos_ref2, rsi_val, direction, current_r)
                    if pulse_result and pulse_result.get("action") == "CLOSE":
                        logger.info("efficiency_pulse_exit", extra={
                            "ticket": ticket, "symbol": symbol,
                            "reason": pulse_result["reason"],
                        })
                        if self.mode == TradingMode.LIVE:
                            await asyncio.to_thread(self.mt5.close_position, ticket)
                            self._efficiency_exit_sent.add(ticket)
                            if self.telegram:
                                asyncio.create_task(self.telegram._send(
                                    f"💫 RSI Pulse Exit\n#{ticket} {symbol}\n{pulse_result['reason']}"
                                ))
                        continue  # Skip tick volume check if already closing

            # Tick Volume Dynamic Exit: check for volume exhaustion/divergence
            if profit_pts > 0:  # Only exit early if we are in profit
                vol_signal = self._tick_volume_signals.get(symbol)
                if vol_signal and vol_signal.is_valid:
                    exit_reason = None
                    if getattr(vol_signal, 'is_climax', False):
                        exit_reason = "Volume Climax (Exhaustion Detected)"
                    elif getattr(vol_signal, 'has_divergence', False):
                        exit_reason = "Price-Volume Divergence"
                    
                    if exit_reason:
                        logger.info("tick_volume_dynamic_exit", extra={
                            "ticket": ticket, "symbol": symbol,
                            "reason": exit_reason,
                            "profit_pts": profit_pts,
                            "vol_score": getattr(vol_signal, 'score', 0)
                        })
                        if self.mode == TradingMode.LIVE:
                            await asyncio.to_thread(self.mt5.close_position, ticket)
                            self._efficiency_exit_sent.add(ticket)
                            if self.telegram:
                                asyncio.create_task(self.telegram._send(
                                    f"🔋 Tick Volume Exit\n#{ticket} {symbol}\n{exit_reason}"
                                ))
                        continue

        # ─── (Optional) Legacy Profit Lock Manager could be removed if logic is fully ported ───
        # For now, disable old profit lock to avoid conflict
        # await asyncio.to_thread(self.profit_lock_manager.check_all, positions)

        # Clear newly tracked tickets for next cycle
        if hasattr(self, '_newly_tracked_tickets'):
            self._newly_tracked_tickets.clear()
        # Purge stale entries from efficiency exit dedup set
        self._efficiency_exit_sent -= (self._efficiency_exit_sent - active_tickets)

        # ─── Manual Trade Protection: now handled by PositionGuardian above ───
        # Legacy code removed — guardian.process_all() covers this

        # ─── PostFill: verify SL on all positions ───
        # If position has no SL -> try modify, if fails -> close immediately
        # ─── PostFill: verify SL on all positions ───
        # If position has no SL -> try modify, if fails -> close immediately
        await self.postfill.verify_all_active()

    # ────────────────────────────────────────────────────────────────
    # Self-Training — Automatic training (background)
    # ────────────────────────────────────────────────────────────────

    async def _run_self_training(self) -> None:
        """
        Run self-training session in background.
        """
        # return  # DISABLED FOR DEBUGGING
        
        if not self.training_orchestrator:
            return

        try:
            logger.info("self_training_start", extra={"cycle": self.cycle_count})
            report = await self.training_orchestrator.run_training_session()
            logger.info("self_training_complete", extra={
                "session_id": report.session_id,
                "duration_s": report.duration_seconds,
                "symbols": len(report.symbols_trained),
                "strategies": report.strategies_tested,
            })
        except Exception as e:
            logger.error("self_training_error", extra={
                "error": str(e),
                "cycle": self.cycle_count,
            }, exc_info=True)

    async def _run_shadow_evaluation(self) -> None:
        """Run shadow trade evaluation in background."""
        if not self.trainer:
            return
        try:
            result = await self.trainer.run_shadow_training_cycle(
                candles_by_symbol=self._shadow_candle_cache
            )
            if result.get("evaluated", 0) > 0:
                logger.info("shadow_training_complete", extra=result)
        except Exception as e:
            logger.error("shadow_evaluation_error", extra={
                "error": str(e),
            }, exc_info=True)

    # ────────────────────────────────────────────────────────────────
    # Shutdown + Kill Switch
    # ────────────────────────────────────────────────────────────────

    async def _shutdown(self) -> None:
        """Graceful shutdown — stop the loop safely."""
        self.running = False
        logger.info(
            "master_loop_shutdown",
            extra={"total_cycles": self.cycle_count},
        )

    def activate_kill_switch(self) -> None:
        """
        Kill-Switch: stop sending orders immediately.

        ⚠️ When kill-switch is activated:
            - Loop stops processing next symbol immediately
            - No new orders will be sent
            - Open positions will NOT be auto-closed (must close manually)
        """
        self.kill_switch = True
        logger.critical(
            "kill_switch_activated",
            extra={"cycle": self.cycle_count},
        )
