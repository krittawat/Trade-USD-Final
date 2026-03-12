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


def check_floating_dd(account: AccountState, symbol: str = "", max_dd_pct: float = 10.0) -> bool:
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
    
    # --- SURVIVOR MODE BYPASS ---
    # หากเป็น XAU หรือ BTC อนุญาตให้เทรดเพื่อกู้พอร์ตแม้ DD จะสูง (แต่ต้องคุม Lot เล็ก)
    symbol_str = str(symbol).upper() if symbol else ""
    if not safe and symbol_str in ["XAUUSD", "XAUUSDM", "BTCUSD", "BTCUSDM"]:
        logger.info("survivor_mode_bypass", extra={
            "symbol": symbol,
            "dd_pct": float(round(dd_pct, 2)),
            "action": "ALLOW_RECOVERY_TRADE"
        })
        return True

    if not safe:
        logger.warning("floating_dd_exceeded", extra={
            "dd_pct": float(round(dd_pct, 2)),
            "max_dd_pct": max_dd_pct,
            "equity": account.equity,
            "floating_pl": account.floating_pl,
        })
    return safe


def check_floating_dd_usd(account: AccountState, max_dd_usd: float = 14.0) -> bool:
    """
    ตรวจ floating drawdown เป็นยอดเงินบัญชี (Account Currency).
    
    Returns:
        True = ปลอดภัย (DD ≤ limit)
        False = อันตราย (DD > limit) → ห้ามเปิดเทรดใหม่ และควร Kill-Switch
    """
    if max_dd_usd <= 0:
        return True
        
    # floating_pl is negative when in drawdown
    safe = account.floating_pl >= -max_dd_usd
    if not safe:
        logger.critical("floating_dd_usd_KILLSWITCH_TRIGGERED", extra={
            "floating_pl": account.floating_pl,
            "max_dd_usd": max_dd_usd,
            "action": "HALT_TRADING_24H"
        })
    return safe


def check_capital_floor(
    account: AccountState,
    floor_pct: float = 90.0,
) -> bool:
    """
    ตรวจ capital floor (High-Water Mark + Capital Shield) — ปกป้อง 90% ของทุนสูงสุดหรือตั้งต้น.
    
    Returns:
        True = equity ยังอยู่เหนือ floor
        False = equity ต่ำกว่า floor → ห้ามเทรด
    """
    if account.initial_balance <= 0 and account.peak_equity <= 0:
        return True  # ยังไม่มี reference balance → ข้าม

    # ใช้ Peak Equity เป็นฐานคิดถ้ามันโตขึ้น (Trailing Capital Floor)
    base_balance = account.peak_equity if account.peak_equity > account.initial_balance else account.initial_balance
    floor = base_balance * (floor_pct / 100)
    
    safe = account.equity >= floor
    if not safe:
        logger.critical("capital_floor_breach", extra={
            "equity": account.equity,
            "floor": floor,
            "peak_equity": account.peak_equity,
            "initial_balance": account.initial_balance,
            "base_balance_used": base_balance,
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
            "daily_loss_pct": float(round(daily_loss_pct, 2)),
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
