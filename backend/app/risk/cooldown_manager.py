"""
Cooldown Manager — ระบบหยุดเทรดชั่วคราวหลังขาดทุน.

กฎ:
    - ขาดทุน 1 ครั้ง → cooldown 5 นาที (ตั้งค่าได้)
    - ขาดทุน 2+ ครั้งติดกัน → ปิดเทรดตลอด session ที่เหลือ
    - ชนะ → รีเซ็ต consecutive losses + cooldown
    - เปลี่ยน session → เคลียร์สถานะทั้งหมด

ป้องกัน:
    - Revenge trading (เข้าเทรดทันทีหลังแพ้)
    - Overtrading after loss streak
    - Emotional decision making

ใช้ time.monotonic() ไม่กระทบ clock skew.
"""

import time
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CooldownState:
    """สถานะ cooldown ของ symbol."""
    consecutive_losses: int = 0
    cooldown_until: float = 0.0       # monotonic timestamp
    session_disabled: bool = False     # ปิดเทรดตลอด session
    last_loss_time: float = 0.0       # monotonic timestamp ของการแพ้ล่าสุด


class CooldownManager:
    """
    จัดการ cooldown หลังขาดทุน — ป้องกัน revenge trading.

    Usage:
        cm = CooldownManager(cooldown_minutes=5, max_consecutive=2)
        cm.record_loss("XAUUSDc")
        allowed, reason = cm.is_allowed("XAUUSDc")
        if not allowed:
            # บล็อก — อยู่ระหว่าง cooldown
    """

    def __init__(
        self,
        db=None,  # Configurable SQLiteStore
        cooldown_minutes: int = 5,
        max_consecutive_losses: int = 2,
    ) -> None:
        self.db = db
        self.cooldown_seconds = cooldown_minutes * 60
        self.max_consecutive_losses = max_consecutive_losses
        self._states: dict[str, CooldownState] = {}
        
        # Load state if DB available
        if self.db:
            self._load_state()

    def _load_state(self) -> None:
        """โหลดสถานะจาก DB."""
        if not self.db:
            return
            
        try:
            import json
            saved_json = self.db.get_state("cooldown_manager_state", "{}")
            data = json.loads(saved_json)
            for sym, d in data.items():
                self._states[sym] = CooldownState(
                    consecutive_losses=d.get("consecutive_losses", 0),
                    cooldown_until=d.get("cooldown_until", 0.0),
                    session_disabled=d.get("session_disabled", False),
                    last_loss_time=d.get("last_loss_time", 0.0)
                )
        except Exception as e:
            logger.error("cooldown_load_error", extra={"error": str(e)})

    def _save_state(self) -> None:
        """บันทึกสถานะลง DB."""
        if not self.db:
            return
            
        try:
            import json
            data = {
                sym: {
                    "consecutive_losses": s.consecutive_losses,
                    "cooldown_until": s.cooldown_until,
                    "session_disabled": s.session_disabled,
                    "last_loss_time": s.last_loss_time
                }
                for sym, s in self._states.items()
            }
            self.db.set_state("cooldown_manager_state", json.dumps(data))
        except Exception as e:
            logger.error("cooldown_save_error", extra={"error": str(e)})

    def _get_state(self, symbol: str) -> CooldownState:
        """ดึง/สร้าง state ของ symbol."""
        if symbol not in self._states:
            self._states[symbol] = CooldownState()
        return self._states[symbol]

    def record_loss(self, symbol: str) -> None:
        """
        บันทึกขาดทุน — ตั้ง cooldown หรือปิด session.
        """
        state = self._get_state(symbol)
        state.consecutive_losses += 1
        state.last_loss_time = time.monotonic()

        if state.consecutive_losses >= self.max_consecutive_losses:
            # 🚨 NUCLEAR COOLDOWN: 1 Hour Block
            # ปรับจากเดิมปิด session -> บล็อก 1 ชม. (ตาม request)
            # หรือถ้าอยากปิด session ก็ใช้ session_disabled = True
            # แต่อยากให้ reset ได้ถ้าผ่านไปนานๆ
            
            # Request: "Block trading 1 hour immediately"
            nuclear_block_time = 3600 # 1 hour
            state.cooldown_until = time.monotonic() + nuclear_block_time
            state.session_disabled = True # Mark as 'Nuclear' block
            
            logger.warning("cooldown_nuclear_activated", extra={
                "symbol": symbol,
                "consecutive_losses": state.consecutive_losses,
                "block_seconds": nuclear_block_time,
                "detail": "NUCLEAR COOLDOWN: 1 Hour Block Activated",
            })
        else:
            # Normal cooldown
            state.cooldown_until = time.monotonic() + self.cooldown_seconds
            logger.info("cooldown_set", extra={
                "symbol": symbol,
                "consecutive_losses": state.consecutive_losses,
                "cooldown_seconds": self.cooldown_seconds,
            })
            
        self._save_state()

    def record_win(self, symbol: str) -> None:
        """บันทึกชนะ — รีเซ็ต consecutive losses + cooldown."""
        state = self._get_state(symbol)
        if state.consecutive_losses > 0:
            logger.info("cooldown_reset_on_win", extra={
                "symbol": symbol,
                "prev_losses": state.consecutive_losses,
            })
        state.consecutive_losses = 0
        state.cooldown_until = 0.0
        state.session_disabled = False # Reset nuclear block too if they manage to win (e.g. manual trade)
        self._save_state()

    def is_allowed(self, symbol: str) -> tuple[bool, str]:
        """
        ตรวจว่า symbol นี้เทรดได้ไหม.
        """
        state = self._get_state(symbol)

        # ตรวจ cooldown timer
        now = time.monotonic()
        if state.cooldown_until > now:
            remaining = int(state.cooldown_until - now)
            # Check if it's nuclear
            if state.session_disabled:
                 return (False, f"NUCLEAR_COOLDOWN: Blocked for {remaining//60}m {remaining%60}s (Tilt Protection)")
            
            return (
                False,
                f"COOLDOWN: เหลือ {remaining}s (หลังจากแพ้ {state.consecutive_losses} ครั้ง)",
            )
            
        # ถ้าเวลาหมดแล้ว แต่ติด flag session_disabled (Nuclear) -> ปลดได้ (เพราะเราใช้ timer 1 hr)
        if state.session_disabled and state.cooldown_until <= now:
            state.session_disabled = False
            self._save_state()

        return (True, "")

    def reset_session(self) -> None:
        """เคลียร์สถานะทั้งหมด — เรียกเมื่อเปลี่ยน session."""
        if self._states:
            symbols = list(self._states.keys())
            logger.info("cooldown_session_reset", extra={
                "symbols": symbols,
            })
        self._states.clear()
        self._save_state()

    def get_status(self, symbol: str) -> dict:
        """ดึงสถานะปัจจุบันสำหรับ dashboard."""
        state = self._get_state(symbol)
        now = time.monotonic()
        remaining = max(0, int(state.cooldown_until - now))
        return {
            "consecutive_losses": state.consecutive_losses,
            "cooldown_remaining_s": remaining,
            "is_nuclear": state.session_disabled,
        }
