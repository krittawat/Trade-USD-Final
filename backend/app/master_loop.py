"""
Antigravity AI Trading System — Master Loop (FULL PERSISTENCE).

ลูปหลักของระบบเทรด — ทำงาน asynchronous ตลอดเวลา:
    1. ดึงสัญลักษณ์ที่จะเทรดจาก .env (TRADING_SYMBOLS)
    2. แต่ละ symbol: วิเคราะห์ตลาด → เลือก strategy → Risk Gate → Execute
    3. จัดการ position health (ตรวจ SL, Break-Even, DD guard)
    4. เก็บทุก decision ลง SQLite (ไม่มี silent block)
    5. Multi-timeframe: M1/M5/M15/H1 ตาม strategy ที่ลงทะเบียน

รอบ (Cycle):
    DRY_RUN  = ทุก 5 วินาที
    LIVE     = ทุก 3 วินาที

Brain Integration:
    - ทุก cycle: ถาม Brain ว่า strategy ไหนดีที่สุด → ให้ priority
    - ทุก 120 cycles (~10 นาที): run training cycle เพื่อเรียนรู้จากเทรดใหม่

Performance (8GB RAM):
    - Candle dedup: ข้ามการวิเคราะห์ซ้ำถ้าแท่งเทียนล่าสุดไม่เปลี่ยน
    - Multi-timeframe: ดึง candles เฉพาะ timeframe ที่ต้องใช้
"""

import asyncio
import time
from datetime import datetime, timezone

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.mode import TradingMode
from app.db.sqlite import SQLiteStore
from app.domain.enums import Action
from app.mt5.client import MT5Client
from app.mt5.market_data import fetch_candles, reset_terminal_cache
from app.brain.regime import classify_regime
from app.strategy.factory import StrategyFactory
from app.risk.gate import PreTradeGate
from app.risk.postfill import PostFillGuard
from app.risk.breakeven import should_move_to_breakeven, mark_be_moved, cleanup_old_be_records
from app.risk.trade_manager import TrailingStopManager, ProfitLockManager, ManualTradeProtector, GhostGuard
from app.execution.pipeline import ExecutionPipeline
from app.execution.shadow_runner import ShadowRunner
from app.services.session import get_current_session
from app.services.news_filter import NewsFilter
from app.services.telegram import TelegramNotifier

logger = get_logger(__name__)

# ─── สัญลักษณ์ที่จะเทรด ───
# โหลดจาก .env: TRADING_SYMBOLS=XAUUSDm,EURUSDm,GBPUSDm,USDJPYm
# ถ้าไม่ตั้ง → fallback เป็น XAUUSDm
DEFAULT_SYMBOLS = ["XAUUSDm"]

# ─── ช่วงเวลาระหว่าง cycle (วินาที) ───
CYCLE_INTERVAL = 5.0       # DRY_RUN mode — 5 วินาที/รอบ
LIVE_CYCLE_INTERVAL = 3.0  # LIVE mode — 3 วินาที/รอบ (เร็วขึ้น)

# ─── Multi-timeframe map ───
# strategy timeframe → (mt5_timeframe, จำนวนแท่งเทียนที่ดึง)
# ครอบคลุมทุก timeframe ที่ registered strategies ใช้
TIMEFRAME_CANDLE_MAP = {
    "M1":  ("M1", 250),    # 250 แท่งเทียน M1 (~4 ชม.)
    "M5":  ("M5", 250),    # 250 แท่งเทียน M5 (~20 ชม.)
    "M15": ("M15", 250),   # 250 แท่งเทียน M15 (~2.5 วัน)
    "H1":  ("H1", 250),    # 250 แท่งเทียน H1 (~10 วัน)
}


# ====================================================================
# MasterLoop — ลูปหลักของระบบเทรด
# ====================================================================

class MasterLoop:
    """
    Main trading loop — ควบคุม execution pipeline ทุก symbol.

    วงจร:
        run() → _run_cycle() → _process_symbol() × N symbols
                              → _check_positions() (LIVE only)
                              → training (ทุก 120 cycles)
                              → health_check (ทุก 60 cycles)
    """

    def __init__(
        self,
        settings: Settings,
        mt5_client: MT5Client | None = None,
        factory: StrategyFactory | None = None,
        db: SQLiteStore | None = None,
        telegram: TelegramNotifier | None = None,
    ) -> None:
        """
        สร้าง MasterLoop พร้อม dependencies ทั้งหมด.

        Args:
            settings: ค่า config จาก .env
            mt5_client: MT5 client สำหรับเชื่อมต่อ broker
            factory: StrategyFactory ที่ลงทะเบียน strategies แล้ว
            db: SQLiteStore สำหรับเก็บ trade journal + decisions
            telegram: TelegramNotifier สำหรับแจ้งเตือนเปิด/ปิดออเดอร์
        """
        self.settings = settings
        self.mode = TradingMode(settings.trading_mode)  # LIVE / DRY_RUN / REPLAY / BACKTEST
        self.mt5 = mt5_client
        self.factory = factory or StrategyFactory()
        self.db = db
        self.telegram = telegram

        # ─── Risk Engine components ───
        self.gate = PreTradeGate(settings)               # Pre-Trade Gate (hard gatekeeper)
        self.news_filter = NewsFilter(settings.news_block_minutes)  # News filter
        self.pipeline = ExecutionPipeline(                # Execution pipeline เต็มรูป
            settings, mt5_client, self.gate, db=db, news_filter=self.news_filter,
            telegram=telegram,
        )
        self.postfill = PostFillGuard(mt5_client)        # Post-Fill: ตรวจ SL หลังเปิดออเดอร์

        # ─── Trade Management ───
        self.trailing_manager = TrailingStopManager(mt5_client)
        self.profit_lock_manager = ProfitLockManager(mt5_client)
        self.ghost_guard = GhostGuard(mt5_client)
        self.manual_protector = ManualTradeProtector(
            mt5_client,
            emergency_sl_pct=settings.manual_sl_pct if hasattr(settings, 'manual_sl_pct') else 0.02,
        )
        self._protect_manual = getattr(settings, 'protect_manual_trades', True)

        # ─── Brain Integration (inject จาก lifespan) ───
        self.brain_memory = None  # MemoryStore — set จาก main.py lifespan
        self.trainer = None       # Trainer — set จาก main.py lifespan
        self.training_orchestrator = None  # TrainingOrchestrator — self-training system
        self._last_training_time = 0.0     # monotonic timestamp of last self-training

        # ─── สถานะลูป ───
        self.running = False          # ลูปทำงานอยู่หรือไม่
        self.kill_switch = False      # Kill-switch: หยุดส่งออเดอร์ทันที
        self.cycle_count = 0          # นับรอบ
        self.last_decisions: dict[str, dict] = {}  # เก็บ decision ล่าสุดต่อ symbol (สำหรับ API)

        # ─── Performance: candle dedup ───
        # ข้ามการวิเคราะห์ซ้ำถ้าแท่งเทียนล่าสุดเหมือนเดิม
        self._last_candle_hash: dict[str, str] = {}  # symbol → "time:close"

        # ─── Position Tracker: ตรวจจับ position ที่ถูกปิด ───
        # เก็บ set ของ ticket → {ticket: {symbol, type, volume, entry_price, profit}}
        self._tracked_positions: dict[int, dict] = {}

        # ─── Shadow Runner: ทดสอบ strategy คู่ขนาน ───
        self.shadow_runner: ShadowRunner | None = None
        if getattr(settings, 'shadow_enabled', True):
            self.shadow_runner = ShadowRunner(factory=self.factory, db=db)

        # ─── Performance: pre-compute needed timeframes ───
        # คำนวณครั้งเดียวตอน init (ไม่ต้องคำนวณใหม่ทุก symbol)
        self._needed_tfs: set[str] = {"M5", "H1"}  # M5 always needed + H1 for MTF
        for strat in self.factory._strategies.values():
            tf = getattr(strat, "timeframe", "M5")
            self._needed_tfs.add(tf)

    # ────────────────────────────────────────────────────────────────
    # run() — เริ่มลูปหลัก
    # ────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        เริ่มลูปหลัก — วนจนกว่าจะหยุดหรือ kill-switch ทำงาน.

        interval:
            LIVE     → 3 วินาที/รอบ
            DRY_RUN  → 5 วินาที/รอบ
        """
        self.running = True
        logger.info("master_loop_started", extra={
            "mode": self.mode.value,
            "symbols": DEFAULT_SYMBOLS,
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
            logger.info("master_loop_cancelled")  # ถูก cancel จาก lifespan shutdown
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
    # _run_cycle() — รอบ 1 cycle
    # ────────────────────────────────────────────────────────────────

    async def _run_cycle(self) -> None:
        """
        Execute 1 cycle: ประมวลผลทุก symbol → ตรวจ position → training → health.

        Performance:
            - Session + news ถูก cache ต่อ cycle (ไม่เรียกซ้ำต่อ symbol)
            - flush_traces() ท้าย cycle (batch commit)
            - prune traces ทุก 1,000 cycles
            - cleanup BE records ทุก 500 cycles
            - Cycle timing metrics
        """
        self.cycle_count += 1
        cycle_start = time.monotonic()  # ใช้ monotonic clock สำหรับ timing
        self.pipeline.set_cycle(self.cycle_count)

        # ─── อ่าน symbols จาก .env (comma-separated) ───
        symbols = [
            s.strip() for s in self.settings.trading_symbols.split(",") if s.strip()
        ] or DEFAULT_SYMBOLS

        # ─── Per-cycle cache: session + account (เรียกครั้งเดียว) ───
        session = get_current_session()
        session_value = session.value

        # ─── MT5 Auto-reconnect: ทุก 30 cycles ถ้า disconnected ───
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
            account = await asyncio.to_thread(self.mt5.get_account_state)

        # ─── Performance: reset terminal cache (1 MT5 check/cycle) ───
        reset_terminal_cache(self.cycle_count)

        # ─── Batch MT5 state: ดึง spread, positions, market_open ครั้งเดียว ───
        mt5_states: dict[str, dict] = {}
        if self.mt5 and self.mt5.is_connected():
            mt5_states = await asyncio.to_thread(self.mt5.get_batch_mt5_state, symbols)

        # ─── ประมวลผลแต่ละ symbol ───
        for symbol in symbols:
            # yield control ให้ event loop รับ HTTP requests ได้
            await asyncio.sleep(0)

            if self.kill_switch:
                logger.warning("kill_switch_active", extra={"symbol": symbol})
                break

            try:
                await self._process_symbol(
                    symbol, account, session_value,
                    mt5_state=mt5_states.get(symbol),
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
            self.db.flush_traces()

        # ─── Flush shadow trades ───
        if self.shadow_runner:
            self.shadow_runner.flush()

        # ─── Position health checks (LIVE mode เท่านั้น) ───
        if self.mode.can_send_orders and self.mt5:
            await self._check_positions()

        # ─── Brain Training: ทุก 120 cycles (~10 นาที @ 5 วินาที/cycle) ───
        if self.trainer and self.cycle_count % 120 == 0 and self.cycle_count > 0:
            try:
                result = await self.trainer.run_training_cycle()
                logger.info("brain_training_done", extra=result)
            except Exception as e:
                logger.error("brain_training_error", extra={"error": str(e)})

        # ─── Self-Training: ทุก N ชั่วโมง (ตั้งค่าใน .env) ───
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
                asyncio.create_task(self._run_self_training())

        # ─── Prune old traces: ทุก 1,000 cycles (ป้องกัน DB โต) ───
        if self.db and self.cycle_count % 1000 == 0:
            self.db.prune_old_traces(keep_days=7)

        # ─── Cleanup BE records: ทุก 500 cycles ───
        if self.cycle_count % 500 == 0:
            cleanup_old_be_records()

        # ─── Prune shadow trades: ทุก 1,000 cycles ───
        if self.db and self.cycle_count % 1000 == 0:
            self.db.prune_old_shadow_trades(keep_days=7)

        # ─── Health emission + cycle timing: ทุก 60 cycles ───
        if self.cycle_count % 60 == 0:
            cycle_ms = (time.monotonic() - cycle_start) * 1000
            logger.info(
                "health_check",
                extra={
                    "cycle": self.cycle_count,
                    "symbols_count": len(symbols),
                    "cycle_ms": round(cycle_ms, 1),
                    "mode": self.mode.value,
                    "equity": account.equity if account else 0,
                    "open_positions": account.open_positions if account else 0,
                    "session": session_value,
                },
            )

    # ────────────────────────────────────────────────────────────────
    # _process_symbol() — ประมวลผล 1 symbol
    # ────────────────────────────────────────────────────────────────

    async def _process_symbol(
        self, symbol: str, account=None, session_value: str = "",
        mt5_state: dict | None = None,
    ) -> None:
        """
        Run execution pipeline สำหรับ 1 symbol.

        Performance:
            - session_value: cached ต่อ cycle (ไม่เรียก get_current_session() ซ้ำ)
            - Candle dedup: ข้ามถ้าแท่งเทียนเดิม
            - Brain fast path: ถ้า Brain recommend + signal → ใช้เลย
        """
        # ─── 1. Profile — ข้อมูล symbol ───
        profile = None
        if self.mt5 and self.mt5.is_connected():
            profile = await asyncio.to_thread(self.mt5.get_symbol_info, symbol)

        if profile is None:
            from app.domain.models import SymbolProfile
            profile = SymbolProfile(symbol=symbol)

        # ─── 2. Multi-timeframe candles (ใช้ pre-computed _needed_tfs) ───
        def _fetch_all_candles():
            result = {}
            for tf in self._needed_tfs:
                tf_key, cnt = TIMEFRAME_CANDLE_MAP.get(tf, ("M5", 250))
                c = fetch_candles(symbol, timeframe=tf_key, count=cnt, cycle=self.cycle_count)
                if c is not None and len(c) >= 30:
                    result[tf] = c
            return result

        candles_by_tf = await asyncio.to_thread(_fetch_all_candles)

        m5_candles = candles_by_tf.get("M5")
        if m5_candles is None or len(m5_candles) < 30:
            self.last_decisions[symbol] = {
                "result": "blocked",
                "reason": "insufficient_candles",
            }
            return

        # ─── 3. Performance: Candle dedup ───
        last_row = m5_candles.iloc[-1]
        candle_hash = f"{last_row.get('time', '')}:{last_row['close']}"
        if candle_hash == self._last_candle_hash.get(symbol):
            return
        self._last_candle_hash[symbol] = candle_hash

        # ─── 4. Regime — วิเคราะห์สภาวะตลาด ───
        regime = classify_regime(m5_candles)

        # ─── 5. Session — ใช้ค่า cached จาก _run_cycle() ───
        if not session_value:
            session_value = get_current_session().value

        # ─── 6. Smart Strategy Selection → เลือกเฉพาะที่เหมาะกับ regime ───
        brain_rec = None
        if self.brain_memory:
            brain_rec = self.brain_memory.get_best_strategy(
                symbol=symbol,
                regime=regime.value,
                session=session_value,
            )

        # Filter strategies ที่ match regime ก่อน (ลดจาก ~18 เหลือ ~5-8)
        regime_matched = []
        regime_unmatched = []
        for strat_name, strategy in self.factory._strategies.items():
            regimes = getattr(strategy, 'regimes', None)
            if regimes and regime in regimes:
                regime_matched.append((strat_name, strategy))
            else:
                regime_unmatched.append((strat_name, strategy))

        # Priority order: brain_rec → regime_matched → rest (limit 5)
        candidates = []
        if brain_rec and brain_rec in self.factory._strategies:
            candidates.append((brain_rec, self.factory._strategies[brain_rec]))

        for item in regime_matched:
            if item[0] != brain_rec:
                candidates.append(item)

        # Add a few unmatched as fallback (diversity)
        for item in regime_unmatched[:2]:
            if item[0] != brain_rec:
                candidates.append(item)

        # ลอง strategy → เลือกตัวที่ confidence สูงสุด
        best_decision = None
        best_confidence = -1.0
        strategies_tried = 0

        h1_candles = candles_by_tf.get("H1")  # For MTF confirmation

        for strat_name, strategy in candidates:
            tf = getattr(strategy, 'timeframe', 'M5')
            candles = candles_by_tf.get(tf, m5_candles)

            try:
                decision = strategy.analyze(
                    candles, profile, regime,
                    session=session_value,
                    h1_candles=h1_candles,
                )
                strategies_tried += 1
                if decision.action != Action.HOLD and decision.confidence > best_confidence:
                    best_confidence = decision.confidence
                    best_decision = decision
                    # Brain recommend + match → ใช้เลย (fast path)
                    if brain_rec and strat_name == brain_rec:
                        break
            except Exception as e:
                logger.debug("strategy_error", extra={
                    "strategy": strat_name, "symbol": symbol,
                    "error": str(e),
                })

        if best_decision is None:
            best_decision = self.factory.get_decision(
                candles=m5_candles, profile=profile, regime=regime, session=session_value,
                brain_recommendation=brain_rec,
            )

        # ─── 7. Pipeline — ส่ง Decision + ใช้ cached mt5_state ───
        if account is None:
            from app.domain.models import AccountState
            account = AccountState(balance=100.0, equity=100.0)

        # ใช้ mt5_state ที่ batch มาแล้ว (ไม่ต้องเรียก MT5 อีก)
        pipeline_result = await self.pipeline.execute(
            decision=best_decision,
            profile=profile,
            account=account,
            regime=regime.value,
            session=session_value,
            mt5_state=mt5_state,
        )

        # ─── 8. Brain Learning — อัปเดต regime stats ทุก cycle ───
        if self.trainer:
            try:
                await self.trainer.update_regime_stats(
                    symbol=symbol,
                    regime=regime.value,
                    session=session_value,
                    atr=float(m5_candles['close'].pct_change().std() * 100) if len(m5_candles) > 20 else 0,
                )
            except Exception:
                pass  # brain learning ล้มเหลว → ไม่กระทบ trading

        # ─── 9. เก็บ decision ล่าสุด ───
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
            "strategies_tried": strategies_tried,
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
            except Exception as e:
                logger.debug("shadow_runner_error", extra={
                    "symbol": symbol, "error": str(e),
                })

    # ────────────────────────────────────────────────────────────────
    # _check_positions() — ตรวจสุขภาพ positions
    # ────────────────────────────────────────────────────────────────

    async def _check_positions(self) -> None:
        """
        ตรวจสุขภาพ positions ทั้งหมด (LIVE mode เท่านั้น).

        ทำ 6 อย่าง:
            1. Position Close Detection: ตรวจจับ position ที่ถูกปิด → แจ้ง Telegram
            2. Break-Even: ถ้ากำไร >= +1R → ย้าย SL มาที่ราคาเข้า
            3. Trailing Stop: ลาก SL ตามกำไร (ATR/fixed)
            4. Profit Lock: ล็อคกำไรเป็นชั้นๆ
            5. Manual Trade Protection: ตั้ง SL ให้ไม้ manual (magic=0)
            6. PostFill: ตรวจว่าทุก position มี SL → ถ้าไม่มีจะปิดทันที
        """
        if not self.mt5:
            return

        positions = await asyncio.to_thread(self.mt5.get_positions)

        # ─── Position Close Detection: ตรวจจับ ticket ที่หายไป ───
        current_tickets = {p["ticket"]: p for p in positions}

        if self._tracked_positions:
            closed_tickets = set(self._tracked_positions.keys()) - set(current_tickets.keys())

            for ticket in closed_tickets:
                prev = self._tracked_positions[ticket]
                # ดึงข้อมูล deal จาก MT5 history
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

                # ส่ง Telegram notification
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

                # ─── Brain Learning: บันทึกผลเทรดเข้า AI memory ───
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
                    except Exception as e:
                        logger.warning("brain_record_error", extra={
                            "ticket": ticket, "error": str(e),
                        })

        # อัปเดต tracked positions (เก็บ strategy/regime/session สำหรับ brain learning)
        for p in positions:
            ticket = p["ticket"]
            if ticket in self._tracked_positions:
                # อัปเดตเฉพาะ profit (ค่าอื่นคงเดิม)
                self._tracked_positions[ticket]["profit"] = p["profit"]
                self._tracked_positions[ticket]["sl"] = p.get("sl", 0)
            else:
                # Position ใหม่ — ดึง strategy/regime/session จาก last_decisions
                sym = p["symbol"]
                last_dec = self.last_decisions.get(sym, {})
                self._tracked_positions[ticket] = {
                    "symbol": sym,
                    "type": p["type"],
                    "volume": p["volume"],
                    "price_open": p["price_open"],
                    "profit": p["profit"],
                    "sl": p.get("sl", 0),
                    "strategy": last_dec.get("strategy", "unknown"),
                    "regime": last_dec.get("regime", "UNKNOWN"),
                    "session": last_dec.get("session", ""),
                }

        # ลบ tickets ที่หายไป (ถูก process แล้วข้างบน)
        active_tickets = {p["ticket"] for p in positions}
        self._tracked_positions = {
            t: v for t, v in self._tracked_positions.items()
            if t in active_tickets
        }

        for pos in positions:
            ticket = pos["ticket"]
            entry = pos["price_open"]       # ราคาเข้า
            current = pos["price_current"]  # ราคาปัจจุบัน
            sl = pos["sl"]                  # Stop Loss ปัจจุบัน
            is_buy = pos["type"] == "BUY"   # เป็น Buy หรือ Sell

            # ─── Break-Even Check ───
            # ถ้ากำไร >= +1R (ตาม settings.breakeven_r_multiple) → ย้าย SL มาที่ entry
            if sl > 0 and should_move_to_breakeven(
                ticket=ticket,
                entry_price=entry,
                current_price=current,
                stop_loss=sl,
                r_multiple=self.settings.breakeven_r_multiple,
                is_buy=is_buy,
            ):
                success = await asyncio.to_thread(self.mt5.modify_sl, ticket, entry)  # ย้าย SL → entry price
                if success:
                    mark_be_moved(ticket)  # จำว่าเคย move BE แล้ว (ป้องกัน spam)
                    logger.info("be_move_success", extra={
                        "ticket": ticket,
                        "symbol": pos["symbol"],
                        "entry": entry,
                        "stage": "be_move",
                        "result": "ok",
                    })

        # ─── Ghost Guard: Virtual SL/TP (ตรวจก่อน — อาจปิด position) ───
        await asyncio.to_thread(self.ghost_guard.check_all, positions)

        # ─── Trailing Stop: ลาก SL ตามกำไร ───
        await asyncio.to_thread(self.trailing_manager.update_all, positions)

        # ─── Profit Lock: ล็อคกำไรเป็นชั้น ───
        await asyncio.to_thread(self.profit_lock_manager.check_all, positions)

        # ─── Manual Trade Protection: ตั้ง SL ให้ไม้ manual ───
        if self._protect_manual:
            await asyncio.to_thread(self.manual_protector.protect_unguarded, positions)

        # ─── PostFill: ตรวจ SL ทุก position ───
        # ถ้า position ไม่มี SL → ลอง modify, ถ้าทำไม่ได้ → ปิดทันที
        await self.postfill.verify_all_positions()

    # ────────────────────────────────────────────────────────────────
    # Self-Training — ฝึกซ้อมอัตโนมัติ (background)
    # ────────────────────────────────────────────────────────────────

    async def _run_self_training(self) -> None:
        """
        รัน self-training session ใน background.

        ไม่ block master loop — ทำงาน async.
        Timeout: 5 นาที (ตั้งค่าใน TrainingOrchestrator).
        """
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

    # ────────────────────────────────────────────────────────────────
    # Shutdown + Kill Switch
    # ────────────────────────────────────────────────────────────────

    async def _shutdown(self) -> None:
        """Graceful shutdown — หยุดลูปอย่างปลอดภัย."""
        self.running = False
        logger.info(
            "master_loop_shutdown",
            extra={"total_cycles": self.cycle_count},
        )

    def activate_kill_switch(self) -> None:
        """
        Kill-Switch: หยุดส่งออเดอร์ทันที.

        ⚠️ เมื่อเปิด kill-switch:
            - ลูปจะหยุดประมวลผล symbol ถัดไปทันที
            - จะไม่ส่งออเดอร์ใหม่
            - positions ที่เปิดอยู่จะไม่ถูกปิดอัตโนมัติ (ต้องปิดเอง)
        """
        self.kill_switch = True
        logger.critical(
            "kill_switch_activated",
            extra={"cycle": self.cycle_count},
        )
