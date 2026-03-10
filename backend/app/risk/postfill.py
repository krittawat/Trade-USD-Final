"""
Post-Fill Guard — ผู้พิทักษ์หลังการเข้าออเดอร์.

หน้าที่:
1. ตรวจสอบว่าออเดอร์ที่เพิ่งเปิด (หรือเปิดค้างอยู่) มี SL/TP ครบถ้วนตามแผน.
2. ถ้าไม่มี SL/TP system ต้องพยายาม Modify Position ทันที.
3. ถ้า Modify ไม่สำเร็จ -> Emergency Close ทันทีเพื่อความปลอดภัย.
"""

import asyncio
from typing import Optional

from app.core.logging import get_logger
from app.mt5.client import MT5Client

logger = get_logger(__name__)


class PostFillGuard:
    """
    Guard ที่ทำงานหลังจาก order filled.
    ตรวจสอบความปลอดภัยของ position.
    """

    def __init__(self, mt5_client: MT5Client):
        self.mt5 = mt5_client
        self.max_retries = 3
        self.retry_delay = 1.0 # seconds

    async def verify_single(self, ticket: int, expected_sl: float, expected_tp: float = 0.0):
        """
        ตรวจสอบ position เดียวแบบเจาะจง (เรียกทันทีหลังได้รับ ticket).
        """
        if ticket <= 0:
            return

        logger.info("postfill_verify_start", extra={"ticket": ticket})

        for attempt in range(1, self.max_retries + 1):
            await asyncio.sleep(0.5) # รอแป๊บนึงให้ Broker update server side

            # Returns Dict or None
            position = self.mt5.get_position_by_ticket(ticket)
            if not position:
                logger.warning("postfill_pos_not_found", extra={"ticket": ticket, "attempt": attempt})
                continue

            # ตรวจ SL
            current_sl = position['sl']
            current_tp = position['tp']
            
            # SL ต้องมีค่า > 0
            has_sl = current_sl > 0
            
            # Logic: ถ้าไม่มี SL เลย -> ต้องใส่
            if not has_sl:
                logger.warning("postfill_missing_sl", extra={
                    "ticket": ticket,
                    "symbol": position['symbol'],
                    "attempt": attempt
                })
                
                # พยายาม Modified
                # modify_position expects values, returns bool (if client.py fixed)
                # MT5Client.modify_sl returns bool.
                res = self.mt5.modify_sl(ticket, new_sl=expected_sl, new_tp=expected_tp if expected_tp > 0 else current_tp)
                if res: 
                    logger.info("postfill_fixed_sl", extra={"ticket": ticket, "sl": expected_sl})
                    return # Fixed
                else:
                    logger.error("postfill_modify_failed", extra={"ticket": ticket})
            else:
                # มี SL แล้ว -> ปลอดภัย
                logger.debug("postfill_sl_ok", extra={"ticket": ticket, "sl": current_sl})
                return

            await asyncio.sleep(self.retry_delay)

        # ถ้า loop จบแล้วยังไม่มี SL -> อันตรายมาก -> Emergency Close
        logger.critical("postfill_emergency_close", extra={"ticket": ticket, "reason": "FAILED_TO_ATTACH_SL"})
        self.mt5.close_position(ticket)

    async def verify_all_active(self):
        """ตรวจสอบทุก position ที่เปิดอยู่ (เรียกโดย Cron/Loop)."""
        positions = self.mt5.get_positions()
        if not positions:
            return

        for pos in positions:
            # Skip Manual Trades (Magic = 0) to avoid conflict with ManualTradeProtector
            if pos.get('magic', -1) == 0:
                continue

            # pos is Dict
            if pos['sl'] <= 0:
                # เจอ position ไร้ SL!
                logger.warning("postfill_scan_found_naked_pos", extra={"ticket": pos['ticket'], "symbol": pos['symbol']})
                
                # Action: Close it. Naked position is forbidden.
                self.mt5.close_position(pos['ticket']) 
                logger.critical("postfill_scan_closed_naked", extra={"ticket": pos['ticket']})
