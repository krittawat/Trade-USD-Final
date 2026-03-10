"""
Risk Dampener — ปรับ lot size อัตโนมัติตาม streak ชนะ/แพ้.

กฎ:
    - 0 แพ้             → multiplier 1.0 (เต็ม risk)
    - แพ้ 1 ครั้งติด    → multiplier 0.7
    - แพ้ 2 ครั้งติด    → multiplier 0.5
    - แพ้ 3+ ครั้งติด   → multiplier 0.3 (พื้น)
    - ชนะ 3+ ครั้งติด   → ค่อยๆ เพิ่ม: min(1.0, floor + recovery × win_streak)

ป้องกัน:
    - Revenge trading ด้วย lot ใหญ่หลังแพ้
    - Overexposure หลัง loss streak
    - ค่อยๆ เพิ่ม lot กลับเมื่อเริ่มชนะ (ไม่กระโดด)

ใช้ใน pipeline.py หลัง sizing ก่อนส่งออเดอร์.
"""

from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DampenerState:
    """สถานะ dampener ของ symbol."""
    consecutive_losses: int = 0
    consecutive_wins: int = 0


class RiskDampener:
    """
    ปรับ lot size อัตโนมัติ — ลดเมื่อแพ้ เพิ่มเมื่อชนะ.

    Usage:
        rd = RiskDampener()
        rd.record_loss("XAUUSDc")
        mult = rd.get_multiplier("XAUUSDc")  # 0.7
        order_plan.lot_size *= mult
    """

    def __init__(
        self,
        enabled: bool = True,
        loss1_mult: float = 0.7,
        loss2_mult: float = 0.5,
        loss3_mult: float = 0.3,
        win_recovery: float = 0.1,
    ) -> None:
        self.enabled = enabled
        self.loss1_mult = loss1_mult
        self.loss2_mult = loss2_mult
        self.loss3_mult = loss3_mult  # floor
        self.win_recovery = win_recovery
        self._states: dict[str, DampenerState] = {}

    def _get_state(self, symbol: str) -> DampenerState:
        if symbol not in self._states:
            self._states[symbol] = DampenerState()
        return self._states[symbol]

    def record_loss(self, symbol: str) -> None:
        """บันทึกแพ้ — เพิ่ม consecutive losses, รีเซ็ต wins."""
        state = self._get_state(symbol)
        state.consecutive_losses += 1
        state.consecutive_wins = 0
        mult = self.get_multiplier(symbol)
        logger.info("risk_dampener_loss", extra={
            "symbol": symbol,
            "consecutive_losses": state.consecutive_losses,
            "new_multiplier": mult,
        })

    def record_win(self, symbol: str) -> None:
        """บันทึกชนะ — เพิ่ม consecutive wins, รีเซ็ต losses."""
        state = self._get_state(symbol)
        state.consecutive_wins += 1
        state.consecutive_losses = 0
        mult = self.get_multiplier(symbol)
        logger.info("risk_dampener_win", extra={
            "symbol": symbol,
            "consecutive_wins": state.consecutive_wins,
            "new_multiplier": mult,
        })

    def get_multiplier(self, symbol: str) -> float:
        """
        คืนค่า lot multiplier สำหรับ symbol.

        Returns:
            float: 0.3 — 1.0
        """
        if not self.enabled:
            return 1.0

        state = self._get_state(symbol)

        if state.consecutive_losses >= 3:
            # 🚨 HARD CAP / TILT PROTECTION
            # ถ้าแพ้ 3 ครั้งติด -> บังคับลด Committment ลงต่ำสุด (Floor)
            return self.loss3_mult  # 0.3

        elif state.consecutive_losses == 2:
            return self.loss2_mult  # 0.5

        elif state.consecutive_losses == 1:
            return self.loss1_mult  # 0.7

        elif state.consecutive_wins >= 3:
            # ค่อยๆ เพิ่มจาก floor กลับไป 1.0
            recovery = self.loss3_mult + self.win_recovery * state.consecutive_wins
            return min(1.0, recovery)
        else:
            return 1.0

    def is_tilt_active(self, symbol: str) -> bool:
        """ตรวจว่ากำลังหัวร้อนหรือไม่ (แพ้ติดกัน >= 2)."""
        state = self._get_state(symbol)
        return state.consecutive_losses >= 2

    def reset_session(self) -> None:
        """เคลียร์สถานะทั้งหมด — เรียกเมื่อเปลี่ยน session."""
        if self._states:
            logger.info("risk_dampener_session_reset", extra={
                "symbols": list(self._states.keys()),
            })
        self._states.clear()

    def get_status(self, symbol: str) -> dict:
        """ดึงสถานะปัจจุบันสำหรับ dashboard."""
        state = self._get_state(symbol)
        return {
            "consecutive_losses": state.consecutive_losses,
            "consecutive_wins": state.consecutive_wins,
            "multiplier": self.get_multiplier(symbol),
        }
