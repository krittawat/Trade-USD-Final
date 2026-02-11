"""
Recommender — แนะนำ strategy parameters จาก AI Brain.

หน้าที่:
    - ถาม Memory Store: "อะไรที่เคยใช้ได้ผล?"
    - แนะนำ strategy name + parameters สำหรับ Factory
    - ไม่ override Risk Engine decisions

วิธีทำงาน:
    1. ดูจาก strategy_performance table
    2. กรองตาม symbol + regime + session
    3. เรียงตาม profit_factor × win_rate
    4. Return recommendation
"""

from typing import Optional

from app.core.logging import get_logger
from app.brain.memory_store import MemoryStore

logger = get_logger(__name__)


class Recommender:
    """
    AI Strategy Recommender — แนะนำ strategy จากข้อมูลอดีต.
    
    ไม่มีสิทธิ์ override Risk Engine — แค่แนะนำเท่านั้น.
    """

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    def recommend(
        self,
        symbol: str,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
    ) -> Optional[str]:
        """
        แนะนำ strategy ที่ดีที่สุดสำหรับสภาวะปัจจุบัน.
        
        Returns:
            ชื่อ strategy (e.g., "scalping", "sniper")
            None ถ้ายังไม่มีข้อมูลเพียงพอ
        """
        recommendation = self.memory.get_best_strategy(symbol, regime, session)
        
        if recommendation:
            logger.info("brain_recommendation", extra={
                "symbol": symbol,
                "strategy": recommendation,
                "regime": regime,
                "session": session,
            })
        else:
            logger.debug("brain_no_recommendation", extra={
                "symbol": symbol,
                "reason": "ข้อมูลไม่เพียงพอสำหรับการแนะนำ",
            })

        return recommendation
