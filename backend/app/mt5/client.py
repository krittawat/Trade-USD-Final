"""
MT5 Client — เชื่อมต่อ MetaTrader 5 สำหรับเทรดจริง.

หน้าที่:
    - เชื่อมต่อ/ตัดการเชื่อมต่อ MT5
    - ดึงข้อมูลสัญลักษณ์ (symbol info)
    - ส่งคำสั่งเทรด (order send)
    - แก้ไขออเดอร์ (modify SL/TP)
    - ปิดออเดอร์
    - ดึงสถานะบัญชี

กฎสำคัญ:
    - ห้ามส่งออเดอร์จริงในโหมด DRY_RUN
    - ทุกออเดอร์ต้องมี SL
    - Log ทุก action ด้วย structured logger
"""

from typing import Optional

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.errors import ConnectionError, OrderError
from app.domain.models import AccountState, OrderPlan

logger = get_logger(__name__)


class MT5Client:
    """
    MetaTrader 5 client wrapper.
    
    ห่อหุ้ม MetaTrader5 Python package เพื่อ:
    - จัดการ connection lifecycle
    - Log ทุก action
    - ป้องกันการส่งออเดอร์โดยไม่ผ่าน risk gate
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._connected = False

    def connect(self) -> bool:
        """
        เชื่อมต่อ MT5 terminal.
        
        Raises:
            ConnectionError: ถ้าเชื่อมต่อไม่ได้
        """
        # TODO: import MetaTrader5 as mt5; mt5.initialize(...)
        logger.info("mt5_connect_attempt", extra={
            "server": self.settings.mt5_server,
            "login": self.settings.mt5_login,
        })
        # self._connected = mt5.initialize(
        #     path=self.settings.mt5_path,
        #     login=self.settings.mt5_login,
        #     password=self.settings.mt5_password,
        #     server=self.settings.mt5_server,
        #     timeout=self.settings.mt5_timeout,
        # )
        # if not self._connected:
        #     raise ConnectionError(f"MT5 connect failed: {mt5.last_error()}")
        self._connected = True  # Stub
        logger.info("mt5_connected")
        return True

    def disconnect(self) -> None:
        """ตัดการเชื่อมต่อ MT5."""
        # mt5.shutdown()
        self._connected = False
        logger.info("mt5_disconnected")

    def is_connected(self) -> bool:
        """ตรวจสอบว่ายังเชื่อมต่อ MT5 อยู่."""
        return self._connected

    def get_account_state(self) -> AccountState:
        """
        ดึงสถานะบัญชีปัจจุบัน.
        
        Returns:
            AccountState: ข้อมูลบัญชี (balance, equity, margin, ฯลฯ)
        """
        # TODO: info = mt5.account_info()
        logger.debug("fetch_account_state")
        return AccountState(
            balance=0.0,
            equity=0.0,
            margin=0.0,
            free_margin=0.0,
        )

    def get_symbol_info(self, symbol: str) -> Optional[dict]:
        """
        ดึงข้อมูลสัญลักษณ์เทรด.
        
        Returns:
            dict: ข้อมูลสัญลักษณ์ (contract_size, volume_step, point, ฯลฯ)
            None: ถ้าสัญลักษณ์ไม่มีหรือเทรดไม่ได้
        """
        # TODO: mt5.symbol_info(symbol)
        logger.debug("fetch_symbol_info", extra={"symbol": symbol})
        return None

    def get_positions(self, symbol: str | None = None) -> list[dict]:
        """
        ดึมตำแหน่งเปิด (open positions).
        
        Args:
            symbol: กรองตามสัญลักษณ์ (None = ทั้งหมด)
        """
        # TODO: mt5.positions_get(symbol=symbol)
        return []

    def send_order(self, plan: OrderPlan) -> dict:
        """
        ส่งคำสั่งเทรดจาก OrderPlan.
        
        กฎสำคัญ:
            - ห้ามเรียกโดยตรง — ต้องผ่าน ExecutionPipeline เท่านั้น
            - OrderPlan ต้องมี SL เสมอ
        
        Raises:
            OrderError: ถ้าส่งคำสั่งไม่สำเร็จ
        """
        if plan.stop_loss <= 0:
            raise OrderError("SL is mandatory — ห้ามส่งออเดอร์ไม่มี SL", {
                "symbol": plan.symbol,
            })

        logger.info("send_order", extra={
            "symbol": plan.symbol,
            "action": plan.action,
            "lot": plan.lot_size,
            "sl": plan.stop_loss,
            "tp": plan.take_profit,
            "risk_usd": plan.risk_usd,
        })

        # TODO: mt5.order_send(request)
        return {"ticket": 0, "status": "stub"}

    def modify_sl(self, ticket: int, new_sl: float) -> bool:
        """
        แก้ไข Stop Loss ของออเดอร์.
        
        ใช้สำหรับ Break-Even และ trailing stop.
        """
        logger.info("modify_sl", extra={"ticket": ticket, "new_sl": new_sl})
        # TODO: mt5.order_send(modify request)
        return True

    def close_position(self, ticket: int, reason: str = "") -> bool:
        """
        ปิดออเดอร์.
        
        Args:
            ticket: หมายเลขออเดอร์
            reason: เหตุผลที่ปิด (สำหรับ log)
        """
        logger.info("close_position", extra={"ticket": ticket, "reason": reason})
        # TODO: mt5.Close(ticket)
        return True
