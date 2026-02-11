"""
Audit — บันทึกเหตุการณ์สำคัญของระบบ.

ทุก action ที่กระทบเงินจริง ต้อง audit:
    - ส่งออเดอร์, แก้ไขออเดอร์, ปิดออเดอร์
    - เปลี่ยนโหมด (DRY → LIVE)
    - Kill switch activated
    - Risk violation
"""

from datetime import datetime, timezone

from app.core.logging import get_logger

logger = get_logger(__name__)


def log_audit_event(
    event_type: str,
    details: dict,
    symbol: str = "",
    severity: str = "INFO",
) -> None:
    """
    บันทึก audit event.
    
    Args:
        event_type: ประเภทเหตุการณ์ เช่น "ORDER_SENT", "MODE_CHANGE"
        details: รายละเอียด
        symbol: สัญลักษณ์ (ถ้าเกี่ยวข้อง)
        severity: INFO / WARNING / CRITICAL
    """
    audit_entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "symbol": symbol,
        "severity": severity,
        **details,
    }
    
    # TODO: เขียนลง SQLite audit table + log file
    logger.info("audit_event", extra=audit_entry)
