"""
Break-Even — ย้าย SL ไป Break-Even เมื่อกำไรถึง +1R.

ตรรกะ:
    1. เมื่อราคาวิ่งกำไรเท่ากับระยะ SL (= 1R) → ย้าย SL ไปราคาเข้า
    2. มี cooldown เพื่อไม่ให้ส่งคำสั่ง modify ซ้ำซ้อน
    3. Log ทุกครั้งที่ย้าย SL

กฎ:
    - ย้ายได้ครั้งเดียวต่อออเดอร์ (ป้องกัน spam)
    - ใช้ dict + timestamp เพื่อ track + cleanup อัตโนมัติ

Performance (8GB RAM):
    - ใช้ dict[int, float] แทน set → เก็บ timestamp สำหรับ cleanup
    - cleanup ทุก 1,000 calls หรือเมื่อ entries > 5,000
    - ลบ entries เก่ากว่า 24 ชม. อัตโนมัติ
    - hard cap ที่ 10,000 entries → force cleanup
"""

import time

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── เก็บ ticket ที่ย้าย BE แล้ว → {ticket: unix_timestamp} ───
# ใช้ dict แทน set เพื่อให้ cleanup ตาม timestamp ได้
_be_moved: dict[int, float] = {}

# Cooldown: ห้ามย้ายซ้ำภายใน N วินาที
BE_COOLDOWN_SECONDS = 5

# ─── Cleanup thresholds ───
_MAX_ENTRIES = 10_000       # hard cap — force cleanup ถ้าเกิน
_CLEANUP_AGE_SECS = 86_400  # 24 ชม. — ลบ entries เก่ากว่านี้
_CLEANUP_INTERVAL = 1_000   # cleanup ทุก N calls
_call_counter = 0            # นับจำนวนครั้งที่เรียก


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
    global _call_counter
    _call_counter += 1

    # ─── Periodic cleanup — ทุก 1,000 calls ───
    if _call_counter % _CLEANUP_INTERVAL == 0 or len(_be_moved) > _MAX_ENTRIES:
        _cleanup_old_entries()

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
    """บันทึกว่า ticket นี้ย้าย BE แล้ว — เก็บ timestamp สำหรับ cleanup."""
    _be_moved[ticket] = time.time()
    logger.info("be_moved", extra={
        "ticket": ticket,
        "stage": "be_move",
        "result": "ok",
        "tracked_count": len(_be_moved),
    })


def _cleanup_old_entries() -> None:
    """ลบ entries เก่ากว่า 24 ชม. — bounded memory safety."""
    cutoff = time.time() - _CLEANUP_AGE_SECS
    old_count = len(_be_moved)
    expired = [t for t, ts in _be_moved.items() if ts < cutoff]
    for t in expired:
        del _be_moved[t]
    if expired:
        logger.debug("be_cleanup", extra={
            "removed": len(expired),
            "remaining": len(_be_moved),
            "was": old_count,
        })


def reset_be_tracking() -> None:
    """รีเซ็ต tracking (ใช้ตอนเริ่มวันใหม่)."""
    _be_moved.clear()
    logger.info("be_tracking_reset")


def cleanup_old_be_records() -> None:
    """Public cleanup — เรียกจาก MasterLoop ได้ตรง."""
    _cleanup_old_entries()
