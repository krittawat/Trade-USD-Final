"""
Strategy Validators — ตรวจสอบ strategy ก่อนใช้งาน.

ตรวจ:
    1. SL mandatory: Decision ที่เป็น BUY/SELL ต้องมี SL
    2. Deterministic: ผลเหมือนเดิมถ้า input เหมือนเดิม
    3. No repaint: ห้ามมองข้อมูลอนาคต
"""

from app.core.logging import get_logger
from app.domain.enums import Action
from app.domain.models import Decision

logger = get_logger(__name__)


def validate_decision(decision: Decision) -> tuple[bool, str]:
    """
    ตรวจสอบ Decision ว่าถูกต้องตามกฎ.
    
    Returns:
        (True, "ok"): ถ้าผ่าน
        (False, "เหตุผล"): ถ้าไม่ผ่าน
    """
    # --- 1. SL mandatory สำหรับ BUY/SELL ---
    if decision.action in (Action.BUY, Action.SELL):
        if decision.stop_loss is None or decision.stop_loss <= 0:
            reason = f"Strategy '{decision.strategy_name}' ส่ง {decision.action} โดยไม่มี SL — บล็อก"
            logger.error("validator_no_sl", extra={
                "strategy": decision.strategy_name,
                "symbol": decision.symbol,
                "action": decision.action,
            })
            return False, reason

    # --- 2. Confidence ต้องอยู่ในช่วง 0-1 ---
    if not (0.0 <= decision.confidence <= 1.0):
        reason = f"Confidence {decision.confidence} อยู่นอกช่วง 0-1"
        logger.error("validator_bad_confidence", extra={
            "strategy": decision.strategy_name,
            "confidence": decision.confidence,
        })
        return False, reason

    # --- 3. Symbol ต้องไม่เป็นค่าว่าง ---
    if not decision.symbol:
        return False, "Symbol เป็นค่าว่าง"

    return True, "ok"


def validate_no_repaint(strategy_class) -> bool:
    """
    ตรวจว่า strategy ไม่ repaint (hook สำหรับตรวจในอนาคต).
    
    วิธีตรวจ:
        - รัน strategy กับข้อมูลเดิม 2 ครั้ง → ผลต้องเหมือนกัน
        - ตัดแท่งสุดท้ายออก → ผลของแท่งก่อนหน้าต้องไม่เปลี่ยน
    
    TODO: implement จริงเมื่อมี template strategies
    """
    logger.info("no_repaint_check", extra={"strategy": strategy_class.__name__})
    return True  # Stub — จะ implement เมื่อมี strategy จริง
