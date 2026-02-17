"""
Shadow Runner — รัน strategy ทุกตัวแบบ virtual คู่ขนานกับ LIVE.

หน้าที่:
    - ทุก cycle: รับ candle data เดียวกันกับ LIVE (ไม่ดึง MT5 เพิ่ม)
    - ทดสอบ ALL registered strategies → บันทึกผลลง shadow_trades
    - ไม่ส่งออเดอร์จริง, ไม่ผ่าน gate, ไม่เรียก MT5
    - ใช้เปรียบเทียบ: strategy ไหนดีที่สุดในสภาวะตลาดจริง?

Performance:
    - Zero MT5 calls (reuse candles จาก LIVE)
    - Error blacklist: strategy ที่พังซ้ำ → ข้าม 100 cycles
    - Max 8 strategies ต่อ cycle (ตัดตัวที่ priority ต่ำ)
    - Abort ถ้ารวมเกิน 50ms
    - Buffer shadow trades → batch INSERT ทุก cycle
"""

import time
from datetime import datetime, timezone

import pandas as pd

from app.core.logging import get_logger
from app.db.sqlite import SQLiteStore
from app.domain.enums import Action
from app.domain.models import Decision, SymbolProfile
from app.strategy.factory import StrategyFactory

logger = get_logger(__name__)

# ─── Constants ───
MAX_SHADOW_STRATEGIES = 8     # จำกัด strategies ที่ shadow ต่อ cycle
ABORT_THRESHOLD_MS = 50.0     # หยุดถ้าเกิน 50ms
BLACKLIST_AFTER_ERRORS = 3    # blacklist strategy หลัง error 3 ครั้ง
BLACKLIST_COOLDOWN_CYCLES = 100  # ปลด blacklist หลัง 100 cycles


class ShadowRunner:
    """
    Shadow Strategy Tester — จำลองทุก strategy คู่ขนานกับ LIVE.

    ใช้ข้อมูลตลาดเดียวกัน (candle data ที่ LIVE ดึงมาแล้ว)
    แต่ไม่ส่ง order จริง — บันทึกลง SQLite shadow_trades table.

    Performance Optimizations:
        - Error blacklist: strategies ที่ error ซ้ำ → ข้ามจนกว่าจะ cooldown
        - Strategy limit: max 8 strategies/cycle
        - Abort threshold: หยุดประมวลผลถ้าเกิน 50ms
        - Direct buffer: ไม่สร้าง intermediate dict (buffer to DB directly)
    """

    def __init__(
        self,
        factory: StrategyFactory,
        db: SQLiteStore | None = None,
    ) -> None:
        self.factory = factory
        self.db = db
        self._shadow_buffer: list[tuple] = []
        # Error tracking: strategy_name → {errors: int, blacklisted_at: cycle}
        self._error_tracker: dict[str, dict] = {}

    def run_shadow(
        self,
        *,
        symbol: str,
        candles_by_tf: dict[str, pd.DataFrame],
        profile: SymbolProfile,
        regime,
        session: str = "",
        live_strategy: str = "",
        cycle: int = 0,
        account_equity: float = 10000.0,
    ) -> int:
        """
        รัน strategy ทุกตัว (ยกเว้น LIVE) → เก็บผล shadow trades.

        Returns:
            int: จำนวน signals ที่พบ
        """
        start_time = time.monotonic()
        signal_count = 0

        m5_candles = candles_by_tf.get("M5")
        if m5_candles is None or len(m5_candles) < 30:
            return 0

        # ─── เตรียม candidate list (ข้าม LIVE + blacklisted) ───
        candidates = []
        for strat_name, strategy in self.factory._strategies.items():
            if strat_name == live_strategy:
                continue
            if self._is_blacklisted(strat_name, cycle):
                continue
            candidates.append((strat_name, strategy))

        # จำกัดจำนวน strategies
        candidates = candidates[:MAX_SHADOW_STRATEGIES]

        regime_value = regime.value if hasattr(regime, "value") else str(regime)
        risk_usd = account_equity * 0.02  # 2% risk

        # ─── วิ่ง strategies ───
        for strat_name, strategy in candidates:
            # Abort ถ้าใช้เวลาเกิน threshold
            elapsed_ms = (time.monotonic() - start_time) * 1000
            if elapsed_ms > ABORT_THRESHOLD_MS:
                logger.debug("shadow_abort_threshold", extra={
                    "symbol": symbol, "elapsed_ms": round(elapsed_ms, 1),
                    "tested": signal_count,
                })
                break

            try:
                tf = getattr(strategy, "timeframe", "M5")
                candles = candles_by_tf.get(tf, m5_candles)
                if candles is None or len(candles) < 30:
                    continue

                decision: Decision = strategy.analyze(candles, profile, regime)

                if decision.action == Action.HOLD:
                    continue

                # ─── คำนวณ virtual sizing + buffer directly ───
                entry_price = float(candles.iloc[-1]["close"])
                sl_distance = abs(entry_price - decision.stop_loss) if decision.stop_loss else 0.0
                lot_size = 0.01
                risk_pct = 2.0

                if sl_distance > 0 and profile.contract_size > 0:
                    lot_raw = risk_usd / (sl_distance * profile.contract_size)
                    step = profile.volume_step or 0.01
                    lot_size = max(
                        profile.volume_min,
                        min(profile.volume_max, round(lot_raw / step) * step),
                    )
                    actual_risk = sl_distance * lot_size * profile.contract_size
                    risk_pct = (actual_risk / account_equity * 100) if account_equity > 0 else 0.0

                # ─── Buffer directly (no intermediate dict) ───
                self._shadow_buffer.append((
                    datetime.now(timezone.utc).isoformat(),
                    symbol,
                    strat_name,
                    decision.action.value,
                    round(decision.confidence, 3),
                    regime_value,
                    session,
                    entry_price,
                    decision.stop_loss or 0.0,
                    decision.take_profit or 0.0,
                    round(lot_size, 2),
                    round(risk_usd, 2),
                    round(risk_pct, 2),
                    cycle,
                    decision.reason[:200],
                ))
                signal_count += 1

                # Clear error count on success
                if strat_name in self._error_tracker:
                    del self._error_tracker[strat_name]

            except Exception as e:
                self._record_error(strat_name, cycle)
                logger.debug("shadow_strategy_error", extra={
                    "strategy": strat_name,
                    "symbol": symbol,
                    "error": str(e),
                })

        elapsed_ms = (time.monotonic() - start_time) * 1000

        if signal_count > 0:
            logger.info("shadow_cycle_complete", extra={
                "symbol": symbol,
                "signals": signal_count,
                "tested": len(candidates),
                "elapsed_ms": round(elapsed_ms, 1),
                "cycle": cycle,
            })

        return signal_count

    def _is_blacklisted(self, strategy_name: str, current_cycle: int) -> bool:
        """ตรวจว่า strategy ถูก blacklist จาก errors ซ้ำหรือยัง."""
        tracker = self._error_tracker.get(strategy_name)
        if not tracker:
            return False
        blacklisted_at = tracker.get("blacklisted_at", 0)
        if blacklisted_at > 0:
            # Cooldown period
            if current_cycle - blacklisted_at < BLACKLIST_COOLDOWN_CYCLES:
                return True
            # Reset after cooldown
            del self._error_tracker[strategy_name]
            return False
        return False

    def _record_error(self, strategy_name: str, cycle: int) -> None:
        """บันทึก error count — blacklist หลังถึง threshold."""
        if strategy_name not in self._error_tracker:
            self._error_tracker[strategy_name] = {"errors": 0, "blacklisted_at": 0}
        tracker = self._error_tracker[strategy_name]
        tracker["errors"] += 1
        if tracker["errors"] >= BLACKLIST_AFTER_ERRORS:
            tracker["blacklisted_at"] = cycle
            logger.info("shadow_strategy_blacklisted", extra={
                "strategy": strategy_name,
                "errors": tracker["errors"],
                "cycle": cycle,
                "cooldown": BLACKLIST_COOLDOWN_CYCLES,
            })

    def flush(self) -> int:
        """
        Batch INSERT shadow trades ที่ buffer ไว้ → SQLite.
        Returns: จำนวน rows ที่ insert
        """
        if not self.db or not self._shadow_buffer:
            return 0

        count = len(self._shadow_buffer)
        try:
            self.db.save_shadow_trades_batch(self._shadow_buffer)
            self._shadow_buffer.clear()
            return count
        except Exception as e:
            logger.error("shadow_flush_error", extra={
                "error": str(e), "buffered": count,
            })
            self._shadow_buffer.clear()
            return 0
