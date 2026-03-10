"""
Trainer — สรุปและเรียนรู้จากข้อมูลใหม่เป็นระยะ (Periodic Learning).

หน้าที่:
    - ทุก N cycles (ถูกเรียกจาก MasterLoop) → ดึงเทรดใหม่จาก trade journal
    - สรุปผลเทรด → อัปเดต MemoryStore (strategy_performance + regime_stats)
    - ทำให้ AI Brain เรียนรู้จากผลจริงโดยอัตโนมัติ

กฎ RAM (8GB mode):
    - ทำงานแบบ batch — ดึงทีละ 500 rows (LIMIT 500)
    - ไม่ load ทั้งหมดเข้า RAM
    - ใช้ SQLite queries สำหรับ aggregation (ไม่ทำ Python-side)

การทำงาน:
    Trainer.run_training_cycle()
        1. ดึงเทรดใหม่จาก trade_journal (WHERE id > last_training_id)
        2. คำนวณ R:R ratio จาก entry/SL/TP
        3. บันทึกผลเข้า MemoryStore.record_trade_outcome()
        4. Return สรุป: {trades_processed, strategies_updated}

    Trainer.update_regime_stats()
        - อัปเดตสถิติ regime (ATR, ADX, price range) เข้า MemoryStore
"""

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.brain.memory_store import MemoryStore

logger = get_logger(__name__)


class Trainer:
    """
    AI Trainer — สรุปผลเทรดและอัปเดต memory เป็นระยะ.

    ทำงานเป็น background task — ไม่กระทบ main trading loop.
    ถูกเรียกจาก MasterLoop ทุก 120 cycles (~10 นาที ที่ 5 วินาที/cycle).
    """

    def __init__(self, memory_store: MemoryStore | None = None, sqlite_store=None) -> None:
        """
        Args:
            memory_store: MemoryStore instance (AI Brain) สำหรับเก็บผลเรียนรู้
            sqlite_store: SQLiteStore instance สำหรับอ่าน trade journal
        """
        self.memory = memory_store
        self.db = sqlite_store              # SQLiteStore — เก็บ trade journal
        self._last_training_id = 0          # ID ของเทรดล่าสุดที่ประมวลผลแล้ว

    # ────────────────────────────────────────────────────────────────
    # run_training_cycle — รอบการเรียนรู้หลัก
    # ────────────────────────────────────────────────────────────────

    async def run_training_cycle(self) -> dict:
        """
        รอบการเรียนรู้หลัก — ดึงเทรดใหม่แล้วอัปเดต Brain.

        ขั้นตอน:
            1. ดึงเทรดใหม่จาก trade_journal (WHERE id > _last_training_id)
            2. วนลูปทุกเทรด:
               a. ดึง strategy_name, symbol, regime, session, profit
               b. คำนวณ R:R ratio จาก entry/SL/TP
               c. บันทึกเข้า MemoryStore.record_trade_outcome()
            3. อัปเดต _last_training_id
            4. Return สรุป

        Batch Size:
            - LIMIT 500 — ป้องกัน RAM overflow จากเทรดจำนวนมาก

        Returns:
            dict: {"trades_processed": N, "strategies_updated": N}
        """
        # ถ้า memory หรือ db ไม่พร้อม → ข้าม (ไม่ crash)
        if not self.memory or not self.db:
            logger.debug("training_skipped", extra={"reason": "memory or db not available"})
            return {"trades_processed": 0, "strategies_updated": 0}

        logger.info("training_cycle_start")
        processed = 0
        strategies_seen = set()  # เก็บชื่อ strategies ที่อัปเดต (unique)

        try:
            # --- ดึง connection จาก SQLiteStore ---
            conn = self.db._conn
            if not conn:
                return {"trades_processed": 0, "strategies_updated": 0}

            # --- Query เทรดใหม่ที่ยังไม่ได้ประมวลผล ---
            # เรียง ASC เพื่อประมวลผลตามลำดับเวลา
            rows = conn.execute("""
                SELECT id, symbol, action, lot_size, entry_price, stop_loss,
                       take_profit, risk_usd, strategy_name, regime, session,
                       mode, profit_usd
                FROM trade_journal
                WHERE id > ?
                ORDER BY id ASC
                LIMIT 500
            """, (self._last_training_id,)).fetchall()

            # --- วนลูปทุกเทรด → บันทึกเข้า Brain ---
            for row in rows:
                trade_id = row["id"]
                strategy = row["strategy_name"] or "unknown"
                symbol = row["symbol"]
                regime = row["regime"] or "UNKNOWN"
                session = row["session"] or "CLOSED"
                profit = row["profit_usd"] or 0.0

                # --- คำนวณ Risk:Reward ratio ---
                rr = 0.0
                if row["stop_loss"] and row["entry_price"]:
                    sl_dist = abs(row["entry_price"] - row["stop_loss"])  # ระยะ SL
                    if sl_dist > 0 and row["take_profit"]:
                        tp_dist = abs(row["take_profit"] - row["entry_price"])  # ระยะ TP
                        rr = tp_dist / sl_dist  # R:R = TP distance / SL distance

                # --- บันทึกเข้า Brain Memory ---
                self.memory.record_trade_outcome(
                    strategy_name=strategy,
                    symbol=symbol,
                    regime=regime,
                    session=session,
                    profit_usd=profit,
                    risk_reward=rr,
                )

                strategies_seen.add(strategy)
                # อัปเดต last ID เพื่อไม่ให้ดึงซ้ำ
                self._last_training_id = max(self._last_training_id, trade_id)
                processed += 1

        except Exception as e:
            # ❌ log error แต่ไม่หยุดระบบ (non-critical)
            logger.error("training_cycle_error", extra={
                "error": str(e),
                "processed_so_far": processed,
            }, exc_info=True)

        logger.info("training_cycle_complete", extra={
            "trades_processed": processed,
            "strategies_updated": len(strategies_seen),
            "last_id": self._last_training_id,
        })

        return {
            "trades_processed": processed,
            "strategies_updated": len(strategies_seen),
        }

    # ────────────────────────────────────────────────────────────────
    # update_regime_stats — อัปเดตสถิติ regime
    # ────────────────────────────────────────────────────────────────

    async def update_regime_stats(self, symbol: str, regime: str,
                                   session: str = "", atr: float = 0.0,
                                   adx: float = 0.0, price_range: float = 0.0) -> None:
        """
        อัปเดต regime statistics สำหรับสัญลักษณ์.

        ส่งค่า indicator ปัจจุบัน → MemoryStore จะคำนวณ EMA ให้อัตโนมัติ.

        Args:
            symbol: สัญลักษณ์ เช่น XAUUSDc
            regime: สภาวะตลาด เช่น TRENDING_UP
            session: session เช่น NY
            atr: ค่า ATR ปัจจุบัน (ส่งเป็น volatility)
            adx: ค่า ADX ปัจจุบัน (ส่งเป็น trend_strength)
            price_range: ช่วงราคา high - low
        """
        if not self.memory:
            return
        # ส่งค่า indicator ไปให้ MemoryStore อัปเดต EMA
        self.memory.update_regime_stats(
            symbol=symbol,
            regime=regime,
            session=session,
            volatility=atr,
            price_range=price_range,
            trend_strength=adx,
        )
        logger.debug("regime_stats_updated", extra={"symbol": symbol, "regime": regime})

    # ────────────────────────────────────────────────────────────────
    # run_shadow_training_cycle — learn from shadow trades
    # ────────────────────────────────────────────────────────────────

    async def run_shadow_training_cycle(
        self,
        candles_by_symbol: dict | None = None,
    ) -> dict:
        """
        Evaluate shadow trades and feed results into Brain.

        Steps:
            1. Use ShadowEvaluator to check pending shadow trades
            2. Walk forward through candles to determine WIN/LOSS
            3. Feed results into MemoryStore
            4. Refresh scoreboard

        Returns:
            dict: {"evaluated": N, "wins": N, "losses": N, "expired": N}
        """
        if not self.db:
            return {"evaluated": 0, "wins": 0, "losses": 0, "expired": 0}

        try:
            from app.brain.shadow_evaluator import ShadowEvaluator

            evaluator = ShadowEvaluator(db=self.db, memory=self.memory)
            result = evaluator.evaluate_pending(
                candles_by_symbol=candles_by_symbol
            )
            evaluator.clear_cache()
            return result

        except Exception as e:
            logger.error("shadow_training_error", extra={
                "error": str(e),
            }, exc_info=True)
            return {"evaluated": 0, "wins": 0, "losses": 0, "expired": 0}

