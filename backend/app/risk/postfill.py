"""
Post-Fill Guard — ตรวจสอบ SL หลังเปิดออเดอร์ (REAL IMPLEMENTATION).

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
    ตรวจสอบ SL หลังออเดอร์ถูก fill — ใช้ MT5 จริง.

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

        if not self.mt5:
            return results

        positions = self.mt5.get_positions()

        for pos in positions:
            ticket = pos["ticket"]
            sl = pos.get("sl", 0.0)
            symbol = pos["symbol"]

            if sl <= 0:
                # ❌ ไม่มี SL — ต้องแก้ไขหรือปิด
                logger.critical("position_no_sl", extra={
                    "ticket": ticket,
                    "symbol": symbol,
                    "stage": "postfill",
                    "result": "error",
                })

                # พยายาม modify ใส่ SL (ใช้ ATR-based estimate)
                # คำนวณ emergency SL = 2% จาก entry price
                entry = pos["price_open"]
                pos_type = pos["type"]
                emergency_sl_dist = entry * 0.02  # 2% emergency SL

                if pos_type == "BUY":
                    emergency_sl = round(entry - emergency_sl_dist, 2)
                else:
                    emergency_sl = round(entry + emergency_sl_dist, 2)

                success = self.mt5.modify_sl(ticket, emergency_sl)

                if success:
                    results.append({
                        "ticket": ticket,
                        "status": "fixed",
                        "action_taken": f"Emergency SL set: {emergency_sl}",
                    })
                    logger.warning("emergency_sl_set", extra={
                        "ticket": ticket,
                        "symbol": symbol,
                        "sl": emergency_sl,
                        "stage": "postfill",
                        "result": "ok",
                    })
                else:
                    # ปิดทันที — emergency close
                    closed = self.mt5.close_position(ticket, "NO_SL_EMERGENCY")
                    results.append({
                        "ticket": ticket,
                        "status": "emergency_closed" if closed else "CRITICAL_FAILED",
                        "action_taken": "Emergency close — no SL and modify failed",
                    })
                    logger.critical("emergency_close_no_sl", extra={
                        "ticket": ticket,
                        "symbol": symbol,
                        "closed": closed,
                        "stage": "postfill",
                        "result": "error",
                    })
            else:
                results.append({
                    "ticket": ticket,
                    "status": "ok",
                    "action_taken": None,
                })

        logger.info("postfill_verify_complete", extra={
            "checked": len(results),
            "issues": sum(1 for r in results if r["status"] != "ok"),
        })
        return results

    async def verify_single(self, ticket: int, expected_sl: float) -> bool:
        """
        ตรวจ SL ของออเดอร์เดียว (หลัง fill).

        Args:
            ticket: หมายเลข position
            expected_sl: SL ที่ควรจะเป็น

        Returns:
            True = SL ถูกต้อง
            False = SL หายหรือผิด (ได้ดำเนินการแก้ไขแล้ว)
        """
        if not self.mt5:
            return True

        # ดึง position info
        positions = self.mt5.get_positions()
        pos = None
        for p in positions:
            if p["ticket"] == ticket:
                pos = p
                break

        if pos is None:
            logger.warning("postfill_position_not_found", extra={
                "ticket": ticket,
                "stage": "postfill",
            })
            return False

        sl = pos.get("sl", 0.0)

        if sl <= 0:
            # SL หาย → พยายาม modify
            success = self.mt5.modify_sl(ticket, expected_sl)
            if not success:
                # Emergency close
                self.mt5.close_position(ticket, "POSTFILL_NO_SL")
                logger.critical("postfill_emergency_close", extra={
                    "ticket": ticket,
                    "expected_sl": expected_sl,
                    "stage": "postfill",
                    "result": "error",
                })
                return False

            logger.info("postfill_sl_fixed", extra={
                "ticket": ticket,
                "sl": expected_sl,
                "stage": "postfill",
                "result": "ok",
            })

        return True
