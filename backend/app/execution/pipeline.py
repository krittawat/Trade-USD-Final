"""
Execution Pipeline — ไปป์ไลน์เดียวสำหรับทุกโหมด.

flow: signal → gate → risk → order_plan → execute

กฎสำคัญ:
    - ไปป์ไลน์เดียวกัน ใช้กับทุกโหมด (LIVE/DRY/REPLAY/BACKTEST)
    - ไม่มี logic แยกต่างหาก — single source of truth
    - Strategy ไม่สามารถข้ามด่าน risk gate ได้
    - ทุกขั้นตอน log ด้วย structured JSON

ขั้นตอน:
    1. SIGNAL:   Strategy วิเคราะห์ → Decision
    2. GATE:     Pre-Trade Gate ตรวจ 14 เงื่อนไข
    3. RISK:     คำนวณ lot size + ตรวจความเสี่ยง
    4. ORDER:    สร้าง OrderPlan
    5. EXECUTE:  ส่งออเดอร์ (LIVE) หรือ log (DRY/BACKTEST)
    6. POSTFILL: ตรวจ SL หลัง fill
"""

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.mode import TradingMode
from app.domain.enums import Action, BlockReason, TradeStage
from app.domain.models import Decision, GateResult, OrderPlan

logger = get_logger(__name__)


class ExecutionPipeline:
    """
    Unified Execution Pipeline — หัวใจของระบบเทรด.
    
    ทุก trade decision ต้องผ่านไปป์ไลน์นี้.
    ใช้โค้ดเดียวกันสำหรับ LIVE, DRY_RUN, REPLAY, BACKTEST.
    
    วิธีใช้:
        pipeline = ExecutionPipeline(settings)
        result = await pipeline.execute(decision, profile, account)
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mode = TradingMode(settings.trading_mode)

    async def execute(
        self,
        decision: Decision,
        profile=None,
        account=None,
        gate=None,
        mt5_client=None,
    ) -> dict:
        """
        รันไปป์ไลน์ทั้งหมด: signal → gate → risk → execute.
        
        Returns:
            dict: {
                "stage": ขั้นตอนสุดท้ายที่ผ่าน,
                "result": "ok" | "blocked" | "error",
                "decision": Decision,
                "order_plan": OrderPlan | None,
                "gate_result": GateResult | None,
                "reason": str,
            }
        """
        result = {
            "stage": TradeStage.SIGNAL.value,
            "result": "ok",
            "decision": decision,
            "order_plan": None,
            "gate_result": None,
            "reason": "",
        }

        # --- ขั้นตอน 1: SIGNAL (Decision มาจาก Strategy แล้ว) ---
        logger.info("pipeline_signal", extra={
            "symbol": decision.symbol,
            "action": decision.action,
            "confidence": decision.confidence,
            "strategy": decision.strategy_name,
            "stage": "signal",
            "result": "ok",
            "mode": self.mode.value,
        })

        # ถ้า HOLD → จบเลย ไม่ต้องผ่าน gate
        if decision.action == Action.HOLD:
            result["reason"] = decision.reason
            return result

        # --- ขั้นตอน 2: GATE (Pre-Trade Gate) ---
        result["stage"] = TradeStage.GATE.value
        if gate:
            gate_result = gate.check(
                decision=decision,
                profile=profile,
                account=account,
            )
            result["gate_result"] = gate_result

            if not gate_result.passed:
                result["result"] = "blocked"
                result["reason"] = ", ".join([r.value for r in gate_result.reasons])
                logger.warning("pipeline_blocked", extra={
                    "symbol": decision.symbol,
                    "reasons": result["reason"],
                    "stage": "gate",
                    "result": "blocked",
                })
                return result

        # --- ขั้นตอน 3: RISK (คำนวณ lot size) ---
        result["stage"] = TradeStage.RISK.value
        # TODO: sizing.calculate_lot_size(decision, profile, account, settings)
        
        # --- ขั้นตอน 4: ORDER (สร้าง OrderPlan) ---
        result["stage"] = TradeStage.ORDER.value
        # TODO: สร้าง OrderPlan จาก sizing result

        # --- ขั้นตอน 5: EXECUTE ---
        if self.mode.can_send_orders:
            # LIVE mode → ส่งออเดอร์จริง
            logger.info("pipeline_execute_live", extra={
                "symbol": decision.symbol,
                "stage": "order",
                "mode": "LIVE",
            })
            # TODO: mt5_client.send_order(order_plan)
        else:
            # DRY_RUN / BACKTEST → log เท่านั้น
            logger.info("pipeline_execute_dry", extra={
                "symbol": decision.symbol,
                "stage": "order",
                "mode": self.mode.value,
                "result": "ok",
            })

        # --- ขั้นตอน 6: POSTFILL ---
        result["stage"] = TradeStage.POSTFILL.value
        # TODO: verify SL attached

        result["result"] = "ok"
        return result
