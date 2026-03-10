"""
Session Guard — จำกัดจำนวนเทรดต่อ session ต่อ symbol.

กฎ:
    - สูงสุด 3 เทรดต่อ session ต่อ symbol (ตั้งค่าได้)
    - เปลี่ยน session → รีเซ็ตนับใหม่

ป้องกัน:
    - Overtrading ในช่วง session เดียว
    - เปิดเทรดไม่หยุดเมื่อตลาดกระชากไปมา

ใช้ใน:
    - Gate: บล็อกเมื่อเทรดครบ max
    - Pipeline: บันทึกเมื่อเปิดเทรดสำเร็จ
"""

from app.core.logging import get_logger

logger = get_logger(__name__)


class SessionGuard:
    """
    ตัวนับเทรดต่อ session ต่อ symbol.

    Usage:
        sg = SessionGuard(max_trades=3)
        allowed, reason = sg.is_allowed("XAUUSDc", "LONDON")
        sg.record_trade("XAUUSDc", "LONDON")
    """

    def __init__(self, max_trades_per_session: int = 3) -> None:
        self.max_trades = max_trades_per_session
        # {symbol: {session: count}}
        self._counts: dict[str, dict[str, int]] = {}
        # {symbol: {date_str: count}} -- New: Daily limits
        self._daily_counts: dict[str, dict[str, int]] = {}

    def record_trade(self, symbol: str, session: str) -> None:
        """บันทึกเทรดที่เปิดสำเร็จ (Update both Session & Daily)."""
        # 1. Session Count
        if symbol not in self._counts:
            self._counts[symbol] = {}
        if session not in self._counts[symbol]:
            self._counts[symbol][session] = 0
        self._counts[symbol][session] += 1
        
        # 2. Daily Count
        from datetime import datetime
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        if symbol not in self._daily_counts:
            self._daily_counts[symbol] = {}
        
        # Clean up old days (keep only today)
        current_keys = list(self._daily_counts[symbol].keys())
        for k in current_keys:
            if k != today:
                del self._daily_counts[symbol][k]
                
        if today not in self._daily_counts[symbol]:
            self._daily_counts[symbol][today] = 0
            
        self._daily_counts[symbol][today] += 1

        logger.info("session_guard_trade_recorded", extra={
            "symbol": symbol,
            "session": session,
            "session_count": self._counts[symbol][session],
            "daily_count": self._daily_counts[symbol][today],
        })

    def is_allowed(self, symbol: str, session: str) -> tuple[bool, str]:
        """
        ตรวจว่าเทรดเพิ่มได้ไหม (Session Limit Only).
        """
        count = self._counts.get(symbol, {}).get(session, 0)
        if count >= self.max_trades:
            return (
                False,
                f"SESSION_MAX: เทรดครบ {count}/{self.max_trades} ใน session {session}",
            )
        return (True, "")

    def is_daily_allowed(self, symbol: str, max_daily: int) -> tuple[bool, str]:
        """
        New: ตรวจว่าเทรดเพิ่มได้ไหม (Daily Limit).
        """
        from datetime import datetime
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        count = self._daily_counts.get(symbol, {}).get(today, 0)
        
        if count >= max_daily:
            return (
                False,
                f"DAILY_MAX: เทรดครบ {count}/{max_daily} สำหรับวันนี้ ({today})",
            )
        return (True, "")

    def reset_session(self, session: str | None = None) -> None:
        """
        รีเซ็ตนับ — เคลียร์ session ที่กำหนด (Daily counts ไม่ถูก reset).
        """
        if session is None:
            if self._counts:
                logger.info("session_guard_full_reset")
            self._counts.clear()
        else:
            for symbol in self._counts:
                if session in self._counts[symbol]:
                    del self._counts[symbol][session]
            logger.info("session_guard_session_reset", extra={
                "session": session,
            })

    def get_status(self, symbol: str, session: str) -> dict:
        """ดึงสถานะปัจจุบันสำหรับ dashboard."""
        from datetime import datetime
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        s_count = self._counts.get(symbol, {}).get(session, 0)
        d_count = self._daily_counts.get(symbol, {}).get(today, 0)
        
        return {
            "session_used": s_count,
            "session_max": self.max_trades,
            "daily_used": d_count,
            "daily_date": today,
        }
