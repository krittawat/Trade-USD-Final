"""
Session Detector — ตรวจเซสชันตลาด.

เซสชัน (UTC):
    - ASIA:    00:00 - 08:00 UTC (Tokyo 09:00-17:00 JST)
    - LONDON:  07:00 - 16:00 UTC (London 07:00-16:00 GMT)
    - NEW_YORK: 12:00 - 21:00 UTC (NY 08:00-17:00 EST)
    - OVERLAP: ช่วงที่ 2+ เซสชันทับกัน (สภาพคล่องสูงสุด)
    - CLOSED:  นอกเวลาทำการ (วันหยุด)

ใช้:
    - Gate: ตรวจว่าอยู่ใน session ที่อนุญาต
    - Factory: เลือก strategy ที่เหมาะกับ session
    - AI Brain: วิเคราะห์ performance ต่อ session
"""

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.domain.enums import MarketSession

logger = get_logger(__name__)

# เวลาเปิด-ปิดของแต่ละ session (UTC hours)
SESSION_HOURS = {
    MarketSession.ASIA: (0, 8),       # 00:00 - 08:00 UTC
    MarketSession.LONDON: (7, 16),    # 07:00 - 16:00 UTC
    MarketSession.NEW_YORK: (12, 21), # 12:00 - 21:00 UTC
}

# Overlap zones
OVERLAP_LONDON_NY = (12, 16)  # London + NY overlap


def get_current_session(now: datetime | None = None) -> MarketSession:
    """
    ตรวจว่าตอนนี้อยู่ในเซสชันไหน.
    
    ถ้ามีหลายเซสชันทับกัน → return OVERLAP.
    ถ้าอยู่นอกเวลา → return CLOSED.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # 1. เช็ควันหยุด (Saturday=5, Sunday=6)
    # หมายเหตุ: ตลาดปกติเปิดประมาณ 22:00 UTC วันอาทิตย์
    weekday = now.weekday()
    hour = now.hour
    if weekday == 5 or (weekday == 6 and hour < 22):
        return MarketSession.WEEKEND

    active_sessions = []
    for session, (start, end) in SESSION_HOURS.items():
        if start <= hour < end:
            active_sessions.append(session)

    if len(active_sessions) >= 2:
        return MarketSession.OVERLAP
    elif len(active_sessions) == 1:
        return active_sessions[0]
    else:
        return MarketSession.CLOSED


def is_session_allowed(
    current_session: MarketSession,
    allowed_sessions: list[str],
) -> bool:
    """
    ตรวจว่าเซสชันปัจจุบันอยู่ในรายการที่อนุญาตหรือไม่.
    
    ถ้า allowed_sessions ว่าง → อนุญาตทุกเซสชัน.
    """
    if not allowed_sessions:
        return True  # ไม่ได้จำกัด → อนุญาตทั้งหมด
    return current_session.value in allowed_sessions
