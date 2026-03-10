"""
TrainingOrchestrator — ควบคุมรอบการฝึกซ้อมทั้งหมด.

หน้าที่:
    - ทุก 6 ชั่วโมง: ดึง historical candles → รัน tournament → evolve params
    - Validate params ใหม่บน hold-out data ก่อนใช้จริง
    - บันทึก TrainingReport ลง MemoryStore
    - ตรวจจับ chart patterns + บันทึก pattern performance
    - ดึงข่าวเศรษฐกิจ + บันทึก news-context ที่มีผลต่อการเทรด
    - รองรับ manual trigger ผ่าน API

กฎ RAM (8GB mode):
    - Max duration per session: 5 นาที (timeout)
    - ดึงแท่งเทียน max 2000 bars ต่อ symbol
    - Walk-forward: 80% train / 20% validate

กฎ Safety:
    - Evolved params ต้อง score สูงกว่า baseline บน validation data
    - ถ้า validation fail → ไม่ใช้ params ใหม่ (keep baseline)
    - ไม่ override Risk Engine เด็ดขาด
"""

import asyncio
import time
import uuid
from datetime import datetime, timezone

import pandas as pd

from app.core.logging import get_logger
from app.domain.models import PracticeResult, TrainingReport, SymbolProfile
from app.brain.practice_engine import PracticeEngine
from app.brain.strategy_evolver import StrategyEvolver
from app.brain.pattern_detector import PatternDetector
from app.brain.news_collector import NewsCollector

logger = get_logger(__name__)

# --- Config ---
TRAIN_SPLIT = 0.8            # 80% train / 20% validate
MAX_CANDLES = 4000           # จำนวนแท่งเทียนสูงสุดที่ดึง (14 days at M5)
TOP_N_STRATEGIES = 5         # จำนวน strategies ที่จะ evolve (top N จาก tournament)


class TrainingOrchestrator:
    """
    ตัวควบคุมรอบการฝึกซ้อมทั้งหมด.

    Lifecycle:
        1. ดึง historical candles จาก MT5 ทุก symbol
        2. แบ่ง train/validate (80/20)
        3. รัน tournament: ทุก strategy แข่งกันบน train data
        4. Evolve: ปรับ params ของ top strategies
        5. Validate: ทดสอบ params ใหม่บน validate data
        6. Save: บันทึก params ที่ดีกว่าเดิม
        7. Report: สร้าง TrainingReport
    """

    def __init__(
        self,
        factory=None,
        memory_store=None,
        mt5_client=None,
        settings=None,
    ) -> None:
        """
        Args:
            factory: StrategyFactory
            memory_store: MemoryStore (AI Brain)
            mt5_client: MT5Client (สำหรับดึง historical candles)
            settings: Settings
        """
        self.factory = factory
        self.memory = memory_store
        self.mt5 = mt5_client
        self.settings = settings

        # --- Pattern + News components ---
        self.pattern_detector = PatternDetector()
        self.news_collector = NewsCollector()

        # --- Sub-components (enhanced with pattern + news) ---
        self.practice_engine = PracticeEngine(
            factory=factory,
            pattern_detector=self.pattern_detector,
            news_collector=self.news_collector,
        )
        self.evolver = StrategyEvolver(memory_store=memory_store)

        # --- State ---
        self._running = False
        self._last_report: TrainingReport | None = None

    # ────────────────────────────────────────────────────────────────
    # Properties
    # ────────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_report(self) -> TrainingReport | None:
        return self._last_report

    # ────────────────────────────────────────────────────────────────
    # run_training_session — รอบการฝึกหลัก
    # ────────────────────────────────────────────────────────────────

    async def run_training_session(self) -> TrainingReport:
        """
        รันรอบฝึกซ้อมเต็ม:
            1. ดึง candles ทุก symbol
            2. Tournament + Evolve + Validate
            3. Save results + report

        Returns:
            TrainingReport: สรุปผลการฝึกซ้อม

        Raises:
            asyncio.TimeoutError: ถ้าเกิน MAX_TRAINING_DURATION
        """
        if self._running:
            logger.warning("training_already_running")
            return self._empty_report("already_running")

        self._running = True
        session_id = str(uuid.uuid4())[:8]
        started_at = datetime.now(timezone.utc)
        start_time = time.monotonic()

        logger.info("training_session_start", extra={
            "session_id": session_id,
        })

        symbols_trained: list[str] = []
        all_best: list[PracticeResult] = []
        all_evolved: dict = {}
        all_improvements: dict = {}
        strategies_tested = 0

        try:
            # --- Refresh news data before training ---
            try:
                await self.news_collector.ensure_fresh()
            except Exception:
                pass  # News refresh failure doesn't block training

            # --- ดึงรายชื่อ symbols ---
            symbols = self._get_symbols()
            
            # --- Get configured timeout ---
            timeout_seconds = 1800.0
            if self.settings and hasattr(self.settings, 'training_timeout_seconds'):
                timeout_seconds = self.settings.training_timeout_seconds

            for symbol in symbols:
                # --- Timeout check ---
                elapsed = time.monotonic() - start_time
                if elapsed > timeout_seconds:
                    logger.warning("training_timeout", extra={
                        "elapsed": round(elapsed, 1),
                        "symbols_done": len(symbols_trained),
                        "timeout": timeout_seconds,
                    })
                    break

                try:
                    # ── ใช้ asyncio.to_thread สำหรับการดึงข้อมูลที่ใช้เวลานาน (blocking) ──
                    result = await self._train_symbol(
                        symbol=symbol,
                        session_id=session_id,
                        start_time=start_time,
                        timeout_seconds=timeout_seconds,
                    )
                    if result:
                        symbols_trained.append(symbol)
                        strategies_tested += result.get("strategies_tested", 0)
                        if result.get("best"):
                            all_best.append(result["best"])
                        if result.get("evolved"):
                            all_evolved[symbol] = result["evolved"]
                        if result.get("improvement"):
                            all_improvements[symbol] = result["improvement"]

                        # --- Record pattern + news outcomes ---
                        if self.memory:
                            try:
                                if result.get("pattern_results"):
                                    self.memory.record_pattern_outcomes(
                                        symbol=symbol,
                                        pattern_win_rates=result.get("pattern_win_rates", {}),
                                        patterns_found=result["pattern_results"],
                                    )
                                if result.get("news_stats"):
                                    self.memory.record_news_outcomes(
                                        symbol=symbol,
                                        news_stats=result["news_stats"],
                                    )
                            except Exception:
                                pass  # recording failure doesn't block training
                except Exception as e:
                    logger.error("training_symbol_error", extra={
                        "symbol": symbol,
                        "error": str(e),
                    }, exc_info=True)

        except Exception as e:
            logger.error("training_session_error", extra={
                "session_id": session_id,
                "error": str(e),
            }, exc_info=True)

        finally:
            self._running = False

        # --- สร้าง report ---
        completed_at = datetime.now(timezone.utc)
        duration = time.monotonic() - start_time

        report = TrainingReport(
            session_id=session_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=round(duration, 1),
            symbols_trained=symbols_trained,
            strategies_tested=strategies_tested,
            best_performers=all_best,
            params_evolved=all_evolved,
            improvements=all_improvements,
        )

        self._last_report = report

        # --- Save report to MemoryStore ---
        self._save_report(report)

        logger.info("training_session_complete", extra={
            "session_id": session_id,
            "duration_s": round(duration, 1),
            "symbols": len(symbols_trained),
            "strategies": strategies_tested,
            "improvements": len(all_improvements),
        })

        return report

    # ────────────────────────────────────────────────────────────────
    # _train_symbol — ฝึกซ้อม 1 symbol
    # ────────────────────────────────────────────────────────────────

    async def _train_symbol(
        self, symbol: str, session_id: str = "", start_time: float = 0.0, timeout_seconds: float = 1800.0
    ) -> dict | None:
        """
        ฝึกซ้อม 1 symbol:
            1. ดึง candles
            2. แบ่ง train / validate
            3. Tournament → Evolve → Validate
        """
        # --- ดึง candles (Async-safe via to_thread) ---
        candles = await asyncio.to_thread(self._fetch_historical_candles, symbol)
        if candles is None or len(candles) < 100:
            logger.debug("training_skip_insufficient_data", extra={
                "symbol": symbol,
                "candles": len(candles) if candles is not None else 0,
            })
            return None

        # --- แบ่ง train / validate ---
        split_idx = int(len(candles) * TRAIN_SPLIT)
        train_candles = candles.iloc[:split_idx].copy()
        validate_candles = candles.iloc[split_idx:].copy()

        profile = await asyncio.to_thread(self._get_profile, symbol)

        # --- 1. Tournament: ทุก strategy แข่งกันบน train data ---
        tournament_results = await self.practice_engine.run_tournament(
            symbol=symbol,
            candles=train_candles,
            profile=profile,
        )

        if not tournament_results:
            return None

        strategies_tested = len(tournament_results)

        # --- เลือก top N strategies สำหรับ evolution ---
        top_strategies = tournament_results[:TOP_N_STRATEGIES]
        best_result = top_strategies[0] if top_strategies else None

        # --- 2. Evolve top strategies ---
        best_evolved = None
        best_improvement = 0.0

        for result in top_strategies:
            # --- Internal timeout check ---
            if start_time > 0 and (time.monotonic() - start_time) > timeout_seconds:
                logger.warning("training_timeout_internal", extra={
                    "symbol": symbol,
                    "strategy": result.strategy_name,
                    "elapsed": round(time.monotonic() - start_time, 1),
                })
                break
                
            if result.total_trades < 2:
                continue  # ข้าม strategy ที่ trades น้อยเกินไป (ผ่อนปรนเพื่อ 100-day discovery)

            try:
                gens, pop = self._get_training_depth(
                    result.strategy_name, symbol,
                )
                evolve_result = await self.evolver.evolve(
                    practice_engine=self.practice_engine,
                    symbol=symbol,
                    candles=train_candles,
                    profile=profile,
                    strategy_name=result.strategy_name,
                    baseline_params=result.params,
                    generations=gens,
                    population_size=pop,
                    deadline=start_time + timeout_seconds if start_time > 0 else 0.0,
                )

                # --- 3. Validate บน hold-out data ---
                if evolve_result["improvement"] > 0:
                    is_valid = await self._validate_evolved(
                        symbol=symbol,
                        strategy_name=result.strategy_name,
                        evolved_params=evolve_result["best_params"],
                        baseline_params=result.params,
                        validation_candles=validate_candles,
                        profile=profile,
                    )

                    if is_valid and evolve_result["improvement"] > best_improvement:
                        best_evolved = evolve_result
                        best_improvement = evolve_result["improvement"]

            except Exception as e:
                logger.debug("evolve_error", extra={
                    "strategy": result.strategy_name,
                    "error": str(e),
                })

            # Yield control
            await asyncio.sleep(0)

        return {
            "strategies_tested": strategies_tested,
            "best": best_result,
            "evolved": best_evolved,
            "improvement": best_improvement if best_improvement > 0 else None,
            "pattern_results": best_result.patterns_found if best_result else {},
            "pattern_win_rates": best_result.pattern_win_rates if best_result else {},
            "news_stats": best_result.news_stats if best_result else {},
        }

    # ────────────────────────────────────────────────────────────────
    # _validate_evolved — ตรวจสอบ params ใหม่บน unseen data
    # ────────────────────────────────────────────────────────────────

    async def _validate_evolved(
        self,
        symbol: str,
        strategy_name: str,
        evolved_params: dict,
        baseline_params: dict,
        validation_candles: pd.DataFrame,
        profile: SymbolProfile | None = None,
    ) -> bool:
        """
        Validate evolved params บน hold-out data.

        Criteria:
            - Score ของ evolved params >= baseline score on validation data
            - Total trades >= 3 (ไม่ overfit)

        Returns:
            True ถ้า evolved params ดีกว่า baseline
        """
        try:
            # Baseline score on validation
            base_result = await self.practice_engine.run_practice(
                symbol=symbol,
                strategy_name=strategy_name,
                candles=validation_candles,
                profile=profile,
                params=baseline_params,
            )

            # Evolved score on validation
            evolved_result = await self.practice_engine.run_practice(
                symbol=symbol,
                strategy_name=strategy_name,
                candles=validation_candles,
                profile=profile,
                params=evolved_params,
            )

            is_better = (
                evolved_result.score >= base_result.score
                and evolved_result.total_trades >= 2
            )

            logger.info("validation_result", extra={
                "strategy": strategy_name,
                "symbol": symbol,
                "base_score": round(base_result.score, 4),
                "evolved_score": round(evolved_result.score, 4),
                "passed": is_better,
            })

            return is_better

        except Exception as e:
            logger.error("validation_error", extra={"error": str(e)})
            return False

    # ────────────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────────────

    def _get_symbols(self) -> list[str]:
        """ดึงรายชื่อ symbols จาก settings."""
        if self.settings:
            return [
                s.strip()
                for s in self.settings.trading_symbols.split(",")
                if s.strip()
            ]
        return ["XAUUSDc"]

    def _fetch_historical_candles(self, symbol: str) -> pd.DataFrame | None:
        """ดึง historical candles จาก MT5."""
        if not self.mt5:
            return None

        try:
            from app.mt5.market_data import fetch_candles
            
            broker_symbol = symbol
            if self.mt5 and hasattr(self.mt5, 'adapter'):
                broker_symbol = self.mt5.adapter.map_symbol(symbol)
                
            return fetch_candles(broker_symbol, timeframe="M5", count=MAX_CANDLES)
        except Exception as e:
            logger.error("fetch_candles_error", extra={
                "symbol": symbol, "error": str(e),
            })
            return None

    def _get_profile(self, symbol: str) -> SymbolProfile:
        """ดึง SymbolProfile จาก MT5 หรือสร้าง default."""
        if self.mt5 and self.mt5.is_connected():
            profile = self.mt5.get_symbol_info(symbol)
            if profile:
                return profile
        return SymbolProfile(symbol=symbol)

    def _get_setting(self, key: str, default: int) -> int:
        """ดึงค่า setting หรือ default."""
        if self.settings:
            return getattr(self.settings, key, default)
        return default

    def _get_training_depth(
        self, strategy_name: str, symbol: str,
    ) -> tuple[int, int]:
        """
        Adaptive training depth per strategy performance.

        Returns:
            (generations, population_size)

        Rules:
            - New strategy (< 30 trades) → 10 gen × 15 pop (explore)
            - Passing criteria (WR≥50, PF≥1.3) → 3 gen × 8 pop (fine-tune)
            - Failing criteria → 8 gen × 20 pop (intensive fix)
        """
        default_gens = self._get_setting("evolution_generations", 5)
        default_pop = self._get_setting("evolution_population", 10)

        if not self.memory:
            return default_gens, default_pop

        try:
            perf = self.memory.get_strategy_performance(
                strategy_name=strategy_name, symbol=symbol, regime="ALL",
            )
            
            # --- Identify Gold / Silver focus ---
            is_precious_metal = bool("XAU" in symbol.upper() or "XAG" in symbol.upper() or "GOLD" in symbol.upper() or "SILVER" in symbol.upper())
            
            if not perf or perf.get("total_trades", 0) < 30:
                # New strategy → explore more
                gens, pop = (15, 20) if is_precious_metal else (10, 15)
                depth = "explore_boosted" if is_precious_metal else "explore"
            elif perf.get("win_rate", 0) >= 50 and perf.get("profit_factor", 0) >= 1.3:
                # Already passing → fine-tune only
                gens, pop = (5, 12) if is_precious_metal else (3, 8)
                depth = "fine_tune_boosted" if is_precious_metal else "fine_tune"
            else:
                # Failing → intensive search
                gens, pop = (12, 25) if is_precious_metal else (8, 20)
                depth = "intensive_boosted" if is_precious_metal else "intensive"

            logger.info("adaptive_training_depth", extra={
                "strategy": strategy_name, "symbol": symbol,
                "depth": depth, "gens": gens, "pop": pop,
                "wr": perf.get("win_rate", 0) if perf else 0,
                "pf": perf.get("profit_factor", 0) if perf else 0,
                "trades": perf.get("total_trades", 0) if perf else 0,
            })
            return gens, pop

        except Exception:
            return default_gens, default_pop

    def _save_report(self, report: TrainingReport) -> None:
        """บันทึก training report ลง MemoryStore."""
        if not self.memory:
            return
        try:
            self.memory.save_training_session(report)
        except Exception as e:
            logger.error("save_report_error", extra={"error": str(e)})

    def _empty_report(self, reason: str = "") -> TrainingReport:
        """สร้าง empty report."""
        now = datetime.now(timezone.utc)
        return TrainingReport(
            session_id=f"empty_{reason}",
            started_at=now,
            completed_at=now,
        )
