"""
Pre-Trade Gate — ด่านตรวจก่อนเทรด (Hard Gatekeeper).

นี่คือหัวใจของ safety system — ทุกเทรดต้องผ่านด่านนี้.
ถ้าไม่ผ่านข้อใดข้อหนึ่ง → บล็อกเทรด + log เหตุผลชัดเจน.

14 ด่านตรวจ:
    1.  MT5 เชื่อมต่ออยู่
    2.  สัญลักษณ์เทรดได้
    3.  ตลาดเปิด
    4.  มี Profile + schema valid
    5.  Spread ≤ threshold
    6.  อยู่ใน session ที่อนุญาต
    7.  ไม่อยู่ในช่วงข่าว (±30 นาที)
    8.  Floating DD ≤ 10%
    9.  ขาดทุนวันนี้ไม่เกินลิมิต
    10. Capital floor ≥ 90% ของยอดตั้งต้น
    11. จำนวน positions ไม่เกิน max
    12. มี SL
    13. Lot size valid
    14. ความเสี่ยง USD ≤ 2% equity

กฎเหล็ก:
    - ห้าม bypass ด่านนี้ไม่ว่ากรณีใด
    - ทุกเหตุผลที่บล็อกต้องแสดงบน dashboard
    - "ไม่เทรด" ต้องมีเหตุผลเสมอ — ห้ามบล็อกเงียบ
"""

from app.core.logging import get_logger
from app.domain.enums import BlockReason
from app.domain.models import (
    AccountState,
    Decision,
    GateResult,
    SymbolProfile,
)

logger = get_logger(__name__)


class PreTradeGate:
    """
    Pre-Trade Gate — ตรวจสอบ 14 เงื่อนไขก่อนอนุญาตให้เทรด.
    
    วิธีใช้:
        gate = PreTradeGate(settings)
        result = gate.check(decision, profile, account)
        if not result.passed:
            # บล็อก — แสดงเหตุผลบน dashboard
            for reason in result.reasons:
                log_blocked(reason)
    """

    def __init__(self, settings) -> None:
        self.settings = settings

    def check(
        self,
        decision: Decision,
        profile: SymbolProfile,
        account: AccountState,
        mt5_connected: bool = False,
        market_open: bool = False,
        current_spread: float = 0.0,
        current_session: str = "CLOSED",
        news_safe: bool = True,
        open_positions_count: int = 0,
    ) -> GateResult:
        """
        ตรวจสอบทุกเงื่อนไข — return GateResult.
        
        ตรวจทุกข้อแม้ข้อก่อนหน้าไม่ผ่าน (เพื่อรายงานทุกปัญหาพร้อมกัน).
        """
        reasons: list[BlockReason] = []
        details: dict = {}

        # --- ด่าน 1: MT5 เชื่อมต่อ ---
        if not mt5_connected:
            reasons.append(BlockReason.MT5_DISCONNECTED)

        # --- ด่าน 2: สัญลักษณ์เทรดได้ ---
        if not profile.is_active:
            reasons.append(BlockReason.SYMBOL_NOT_TRADABLE)

        # --- ด่าน 3: ตลาดเปิด ---
        if not market_open:
            reasons.append(BlockReason.MARKET_CLOSED)

        # --- ด่าน 4: Profile ครบถ้วน ---
        if profile.contract_size <= 0 or profile.point <= 0:
            reasons.append(BlockReason.PROFILE_INCOMPLETE)

        # --- ด่าน 5: Spread ≤ threshold ---
        if current_spread > profile.spread_max_allowed:
            reasons.append(BlockReason.SPREAD_GUARD)
            details["spread_current"] = current_spread
            details["spread_max"] = profile.spread_max_allowed

        # --- ด่าน 6: Session ที่อนุญาต ---
        if profile.sessions_allowed and current_session not in profile.sessions_allowed:
            reasons.append(BlockReason.SESSION_BLOCKED)
            details["current_session"] = current_session

        # --- ด่าน 7: News safe ---
        if not news_safe:
            reasons.append(BlockReason.NEWS_BLOCK)

        # --- ด่าน 8: Floating DD ≤ 10% ---
        if account.equity > 0:
            floating_dd_pct = abs(account.floating_pl) / account.equity * 100
            if floating_dd_pct > self.settings.floating_dd_block_pct:
                reasons.append(BlockReason.FLOATING_DD_EXCEEDED)
                details["floating_dd_pct"] = round(floating_dd_pct, 2)

        # --- ด่าน 9: Daily loss limit ---
        # TODO: implement daily loss tracking
        # if account.daily_pl < -daily_loss_limit:
        #     reasons.append(BlockReason.DAILY_LOSS_EXCEEDED)

        # --- ด่าน 10: Capital floor ---
        if account.initial_balance > 0:
            floor = account.initial_balance * (self.settings.capital_floor_pct / 100)
            if account.equity < floor:
                reasons.append(BlockReason.CAPITAL_FLOOR_BREACH)
                details["equity"] = account.equity
                details["floor"] = floor

        # --- ด่าน 11: Max positions ---
        if open_positions_count >= self.settings.max_positions_per_symbol:
            reasons.append(BlockReason.MAX_POSITIONS)
            details["open"] = open_positions_count
            details["max"] = self.settings.max_positions_per_symbol

        # --- ด่าน 12: SL mandatory ---
        if decision.stop_loss is None or decision.stop_loss <= 0:
            reasons.append(BlockReason.NO_STOP_LOSS)

        # --- ด่าน 13: Lot size valid (จะตรวจตอน sizing) ---
        # ตรวจที่ sizing.py อีกครั้ง

        # --- ด่าน 14: Risk ≤ 2% equity (จะตรวจตอน sizing) ---
        # ตรวจที่ sizing.py อีกครั้ง

        # --- สรุปผล ---
        passed = len(reasons) == 0

        if not passed:
            logger.warning(
                "gate_blocked",
                extra={
                    "symbol": decision.symbol,
                    "reasons": [r.value for r in reasons],
                    "details": details,
                    "stage": "gate",
                    "result": "blocked",
                },
            )
        else:
            logger.info(
                "gate_passed",
                extra={
                    "symbol": decision.symbol,
                    "stage": "gate",
                    "result": "ok",
                },
            )

        return GateResult(passed=passed, reasons=reasons, details=details)
