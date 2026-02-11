"""
Risk Sizing — คำนวณ lot size จากความเสี่ยง.

สูตร:
    risk_usd = equity × max_risk_pct / 100
    sl_distance = |entry_price - stop_loss|
    lot = risk_usd / (sl_distance × contract_size)
    lot = round_to_step(lot, volume_step)

กฎเหล็ก:
    - ความเสี่ยงจริงต้อง ≤ 2% ของ equity
    - Lot size ต้องอยู่ในช่วง volume_min — volume_max
    - ปัดเศษลง (floor) เสมอ — ไม่ปัดขึ้น
"""

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.enums import BlockReason
from app.domain.models import AccountState, Decision, OrderPlan, SymbolProfile
from app.mt5.broker_specs import round_lot, is_lot_valid

logger = get_logger(__name__)


def calculate_lot_size(
    decision: Decision,
    profile: SymbolProfile,
    account: AccountState,
    settings: Settings,
    entry_price: float | None = None,
) -> OrderPlan | BlockReason:
    """
    คำนวณ lot size จากความเสี่ยงที่กำหนด.
    
    Args:
        decision: ผลลัพธ์จาก strategy (มี SL)
        profile: ข้อมูลสัญลักษณ์
        account: สถานะบัญชี
        settings: การตั้งค่าระบบ
        entry_price: ราคาเข้า (ถ้าไม่ระบุ จะใช้ราคาปัจจุบัน)
    
    Returns:
        OrderPlan: ถ้าคำนวณสำเร็จ
        BlockReason: ถ้าไม่สามารถคำนวณ lot ที่ valid ได้
    """
    # --- ตรวจสอบ SL ---
    if decision.stop_loss is None or decision.stop_loss <= 0:
        return BlockReason.NO_STOP_LOSS

    # --- คำนวณความเสี่ยง USD ---
    max_risk_usd = account.equity * (settings.max_risk_per_trade_pct / 100)
    
    # --- คำนวณ SL distance ---
    # ใช้ entry_price จาก argument หรือประมาณจาก decision
    price = entry_price or decision.stop_loss * 1.001  # Stub: ใช้ราคาจริงจาก MT5
    sl_distance = abs(price - decision.stop_loss)
    
    if sl_distance <= 0:
        logger.error("sl_distance_zero", extra={"symbol": decision.symbol})
        return BlockReason.NO_STOP_LOSS

    # --- คำนวณ lot ---
    # lot = risk_usd / (sl_distance × contract_size)
    raw_lot = max_risk_usd / (sl_distance * profile.contract_size)
    
    # --- ปัดเศษตาม volume step (ปัดลงเสมอ) ---
    lot = round_lot(raw_lot, profile)
    
    # --- ตรวจสอบ lot valid ---
    if not is_lot_valid(lot, profile):
        logger.warning("lot_size_invalid", extra={
            "symbol": decision.symbol,
            "raw_lot": raw_lot,
            "rounded_lot": lot,
            "min": profile.volume_min,
            "max": profile.volume_max,
        })
        return BlockReason.LOT_SIZE_INVALID

    # --- คำนวณความเสี่ยง USD จริง ---
    actual_risk_usd = lot * profile.contract_size * sl_distance
    actual_risk_pct = (actual_risk_usd / account.equity * 100) if account.equity > 0 else 0

    # --- ตรวจสอบความเสี่ยงไม่เกินลิมิต ---
    if actual_risk_pct > settings.max_risk_per_trade_pct:
        logger.warning("risk_exceeded", extra={
            "symbol": decision.symbol,
            "risk_pct": round(actual_risk_pct, 2),
            "max_pct": settings.max_risk_per_trade_pct,
        })
        return BlockReason.RISK_EXCEEDED

    # --- สร้าง OrderPlan ---
    logger.info("lot_calculated", extra={
        "symbol": decision.symbol,
        "lot": lot,
        "risk_usd": round(actual_risk_usd, 2),
        "risk_pct": round(actual_risk_pct, 2),
        "sl_distance": round(sl_distance, profile.digits),
        "stage": "risk",
        "result": "ok",
    })

    return OrderPlan(
        symbol=decision.symbol,
        action=decision.action,
        lot_size=lot,
        stop_loss=decision.stop_loss,
        take_profit=decision.take_profit,
        risk_usd=round(actual_risk_usd, 2),
        risk_pct=round(actual_risk_pct, 2),
        entry_price=entry_price,
        strategy_name=decision.strategy_name,
        comment=f"AG|{decision.strategy_name}|R{round(actual_risk_pct, 1)}%",
    )
