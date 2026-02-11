"""
Guards — ระบบป้องกันเพิ่มเติม (DD, daily loss, capital floor, rollover).

ทำงานเป็น layer เสริมจาก Pre-Trade Gate:
    - Drawdown Guard: บล็อกเมื่อ floating DD > 10%
    - Daily Loss Guard: บล็อกเมื่อขาดทุนวันนี้เกินลิมิต
    - Capital Floor: บล็อกเมื่อ equity ต่ำกว่า 90% ของยอดตั้งต้น
    - Rollover Block: บล็อกช่วง rollover (swap time)
"""

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.domain.models import AccountState

logger = get_logger(__name__)


def check_floating_dd(account: AccountState, max_dd_pct: float = 10.0) -> bool:
    """
    ตรวจ floating drawdown.
    
    Returns:
        True = ปลอดภัย (DD ≤ limit)
        False = อันตราย (DD > limit) → ห้ามเปิดเทรดใหม่
    """
    if account.equity <= 0:
        return False
    dd_pct = abs(account.floating_pl) / account.equity * 100
    safe = dd_pct <= max_dd_pct
    if not safe:
        logger.warning("floating_dd_exceeded", extra={
            "dd_pct": round(dd_pct, 2),
            "max_dd_pct": max_dd_pct,
            "equity": account.equity,
            "floating_pl": account.floating_pl,
        })
    return safe


def check_capital_floor(
    account: AccountState,
    floor_pct: float = 90.0,
) -> bool:
    """
    ตรวจ capital floor — ปกป้อง 90% ของทุนตั้งต้น.
    
    Returns:
        True = equity ยังอยู่เหนือ floor
        False = equity ต่ำกว่า floor → ห้ามเทรด
    """
    if account.initial_balance <= 0:
        return True  # ยังไม่มี initial balance → ข้าม

    floor = account.initial_balance * (floor_pct / 100)
    safe = account.equity >= floor
    if not safe:
        logger.critical("capital_floor_breach", extra={
            "equity": account.equity,
            "floor": floor,
            "initial_balance": account.initial_balance,
        })
    return safe


def check_daily_loss(
    account: AccountState,
    max_daily_loss_pct: float = 5.0,
) -> bool:
    """
    ตรวจขาดทุนรายวัน.
    
    Returns:
        True = ขาดทุนวันนี้ยังไม่เกินลิมิต
        False = ขาดทุนเกิน → ห้ามเทรดจนจบวัน
    """
    if account.balance <= 0:
        return False
    daily_loss_pct = abs(min(0, account.daily_pl)) / account.balance * 100
    safe = daily_loss_pct <= max_daily_loss_pct
    if not safe:
        logger.warning("daily_loss_exceeded", extra={
            "daily_loss_pct": round(daily_loss_pct, 2),
            "daily_pl": account.daily_pl,
            "max_pct": max_daily_loss_pct,
        })
    return safe


def is_rollover_window(now: datetime | None = None) -> bool:
    """
    ตรวจว่าอยู่ในช่วง rollover หรือไม่.
    
    Rollover มักเกิดช่วง 21:00-22:00 UTC — spread กว้าง, swap charge.
    ไม่ควรเปิดเทรดใหม่ในช่วงนี้.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    # ช่วง rollover: 21:00 - 22:00 UTC (ปรับได้ตาม broker)
    return 21 <= now.hour < 22
