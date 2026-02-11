"""
Post-Fill Guard — ตรวจสอบหลังเปิดออเดอร์.

หน้าที่:
    - ยืนยันว่า SL ถูกใส่ในออเดอร์จริงๆ
    - ถ้าไม่มี SL → พยายาม modify ใส่ SL
    - ถ้า modify ไม่ได้ → ปิดออเดอร์ทันที (emergency close)

กฎเหล็ก:
    - ห้ามมีออเดอร์เปิดโดยไม่มี SL
    - ตรวจทุก position ทุกรอบ loop
"""

from app.core.logging import get_logger

logger = get_logger(__name__)


class PostFillGuard:
    """
    ตรวจสอบ SL หลังออเดอร์ถูก fill.
    
    ถ้า SL หายหรือไม่ถูกต้อง → แก้ไขหรือปิดทิ้ง ห้ามปล่อย.
    """

    def __init__(self, mt5_client=None) -> None:
        self.mt5 = mt5_client

    async def verify_all_positions(self) -> list[dict]:
        """
        ตรวจ SL ของทุก position ที่เปิดอยู่.
        
        Returns:
            list[dict]: รายงานผล [{ticket, status, action_taken}]
        """
        results = []
        # TODO: ดึง positions จาก MT5
        # positions = self.mt5.get_positions()
        # for pos in positions:
        #     if pos.sl <= 0:
        #         # พยายาม modify ใส่ SL
        #         success = self.mt5.modify_sl(pos.ticket, calculated_sl)
        #         if not success:
        #             # ปิดทันที — emergency close
        #             self.mt5.close_position(pos.ticket, "NO_SL_EMERGENCY")
        #             logger.critical("emergency_close_no_sl", extra={...})
        logger.info("postfill_verify_complete", extra={"checked": len(results)})
        return results

    async def verify_single(self, ticket: int, expected_sl: float) -> bool:
        """
        ตรวจ SL ของออเดอร์เดียว.
        
        Args:
            ticket: หมายเลขออเดอร์
            expected_sl: SL ที่ควรจะเป็น
        
        Returns:
            True = SL ถูกต้อง
            False = SL หายหรือผิด (ได้ดำเนินการแก้ไขแล้ว)
        """
        logger.info("postfill_verify_single", extra={
            "ticket": ticket,
            "expected_sl": expected_sl,
            "stage": "postfill",
        })
        # TODO: ตรวจจริง
        return True
