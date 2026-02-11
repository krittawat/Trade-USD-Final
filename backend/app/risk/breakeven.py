"""
Break-Even — ย้าย SL ไป Break-Even เมื่อกำไรถึง +1R.

ตรรกะ:
    1. เมื่อราคาวิ่งกำไรเท่ากับระยะ SL (= 1R) → ย้าย SL ไปราคาเข้า
    2. มี cooldown เพื่อไม่ให้ส่งคำสั่ง modify ซ้ำซ้อน
    3. Log ทุกครั้งที่ย้าย SL

กฎ:
    - ย้ายได้ครั้งเดียวต่อออเดอร์ (ป้องกัน spam)
    - ใช้ flag ใน runtime state เพื่อ track ว่า BE ย้ายแล้ว
"""

from datetime import datetime, timezone

from app.core.logging import get_logger

logger = get_logger(__name__)

# เก็บ ticket ที่ย้าย BE แล้ว — ป้องกัน spam
_be_moved: set[int] = set()

# Cooldown: ห้ามย้ายซ้ำภายใน N วินาที
BE_COOLDOWN_SECONDS = 5


def should_move_to_breakeven(
    ticket: int,
    entry_price: float,
    current_price: float,
    stop_loss: float,
    r_multiple: float = 1.0,
    is_buy: bool = True,
) -> bool:
    """
    ตรวจว่าควรย้าย SL ไป Break-Even หรือยัง.
    
    เงื่อนไข:
        - กำไร ≥ 1R (ระยะ SL)
        - ยังไม่เคยย้าย BE สำหรับ ticket นี้
    
    Args:
        ticket: หมายเลขออเดอร์
        entry_price: ราคาเข้า
        current_price: ราคาปัจจุบัน
        stop_loss: SL ปัจจุบัน
        r_multiple: ตัวคูณ R (default 1.0 = 1R)
        is_buy: True ถ้าเป็น BUY, False ถ้า SELL
    """
    # ถ้า BE ย้ายไปแล้ว → ข้าม
    if ticket in _be_moved:
        return False

    # คำนวณ SL distance (1R)
    sl_distance = abs(entry_price - stop_loss)
    required_profit = sl_distance * r_multiple

    # ตรวจว่ากำไรถึง +1R หรือยัง
    if is_buy:
        current_profit = current_price - entry_price
    else:
        current_profit = entry_price - current_price

    return current_profit >= required_profit


def mark_be_moved(ticket: int) -> None:
    """บันทึกว่า ticket นี้ย้าย BE แล้ว."""
    _be_moved.add(ticket)
    logger.info("be_moved", extra={
        "ticket": ticket,
        "stage": "be_move",
        "result": "ok",
    })


def reset_be_tracking() -> None:
    """รีเซ็ต tracking (ใช้ตอนเริ่มวันใหม่)."""
    _be_moved.clear()
    logger.info("be_tracking_reset")
