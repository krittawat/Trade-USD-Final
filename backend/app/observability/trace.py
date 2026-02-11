"""
Decision Trace — สร้างร่องรอยการตัดสินใจ.

ทุก decision ถูกบันทึก:
    - symbol, action, confidence, reason
    - ผ่าน gate หรือไม่ (+ เหตุผลที่บล็อก)
    - lot size, risk USD, SL/TP
    - timestamp

ใช้แสดง "decision trace timeline" บน dashboard.
"""

from datetime import datetime, timezone
from typing import Optional

from app.core.logging import get_logger
from app.domain.models import Decision, GateResult, OrderPlan

logger = get_logger(__name__)


class DecisionTracer:
    """สร้างและบันทึก decision traces สำหรับ dashboard."""

    def __init__(self, sqlite_store=None) -> None:
        self.store = sqlite_store

    def trace(
        self,
        decision: Decision,
        gate_result: Optional[GateResult] = None,
        order_plan: Optional[OrderPlan] = None,
        final_result: str = "ok",
    ) -> dict:
        """
        สร้าง trace entry จาก decision + gate result + order plan.
        
        Returns:
            dict: trace entry ที่พร้อมบันทึก
        """
        trace_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": decision.symbol,
            "action": decision.action,
            "confidence": decision.confidence,
            "strategy": decision.strategy_name,
            "reason": decision.reason,
            "result": final_result,
            "gate_passed": gate_result.passed if gate_result else None,
            "gate_reasons": [r.value for r in gate_result.reasons] if gate_result else [],
            "lot_size": order_plan.lot_size if order_plan else None,
            "risk_usd": order_plan.risk_usd if order_plan else None,
            "sl": order_plan.stop_loss if order_plan else decision.stop_loss,
            "tp": order_plan.take_profit if order_plan else decision.take_profit,
        }

        # TODO: บันทึกลง SQLite
        logger.info("decision_trace", extra=trace_entry)
        return trace_entry
