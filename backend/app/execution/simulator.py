"""
Simulator — DRY_RUN / BACKTEST executor.

หน้าที่:
    - บันทึก decisions ทุกตัวลง SQLite (ไม่ส่งออเดอร์จริง)
    - จำลอง fill ตาม backtest rules
    - คำนวณ simulated P&L

ใช้ร่วมกับ ExecutionPipeline — เมื่อ mode ≠ LIVE.
"""

from app.core.logging import get_logger
from app.domain.models import Decision, OrderPlan

logger = get_logger(__name__)


class Simulator:
    """
    จำลองการเทรด — log decisions + คำนวณ virtual P&L.
    
    ใช้ในโหมด DRY_RUN และ BACKTEST.
    """

    def __init__(self, sqlite_store=None) -> None:
        self.store = sqlite_store
        self.virtual_trades: list[dict] = []

    async def simulate_fill(self, plan: OrderPlan, current_price: float) -> dict:
        """
        จำลองการ fill ออเดอร์.
        
        Returns:
            dict: {"ticket": virtual_ticket, "fill_price": price, "status": "simulated"}
        """
        ticket = len(self.virtual_trades) + 1
        trade = {
            "ticket": ticket,
            "symbol": plan.symbol,
            "action": plan.action,
            "lot": plan.lot_size,
            "fill_price": current_price,
            "sl": plan.stop_loss,
            "tp": plan.take_profit,
            "risk_usd": plan.risk_usd,
            "status": "simulated",
        }
        self.virtual_trades.append(trade)
        
        logger.info("simulated_fill", extra={
            "ticket": ticket,
            "symbol": plan.symbol,
            "action": plan.action,
            "price": current_price,
        })
        return trade
