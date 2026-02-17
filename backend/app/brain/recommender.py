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

    def recommend_with_params(
        self,
        symbol: str,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
    ) -> dict:
        """
        แนะนำ strategy + evolved parameters.

        Returns:
            dict: {
                "strategy": str | None,
                "params": dict | None,  # evolved params ถ้ามี
            }
        """
        strategy = self.recommend(symbol, regime, session)
        params = None

        if strategy:
            # ดึง evolved params จาก MemoryStore
            params = self.memory.get_evolved_params(
                strategy_name=strategy,
                symbol=symbol,
                regime="ALL",  # ใช้ ALL regime ก่อน
            )

            if params:
                logger.info("brain_evolved_params_found", extra={
                    "symbol": symbol,
                    "strategy": strategy,
                    "params_keys": list(params.keys()),
                })

        return {
            "strategy": strategy,
            "params": params,
        }

    def recommend_with_patterns(
        self,
        symbol: str,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
    ) -> dict:
        """
        แนะนำ strategy + patterns ที่มี win_rate สูง.

        Returns:
            dict: {
                "strategy": str | None,
                "params": dict | None,
                "best_patterns": [{pattern_name, win_rate, total_trades}],
            }
        """
        base = self.recommend_with_params(symbol, regime, session)

        # ดึง best patterns จาก MemoryStore
        best_patterns = self.memory.get_best_patterns(
            symbol=symbol,
            regime=regime,
            min_trades=5,
            limit=5,
        )

        if best_patterns:
            logger.info("brain_pattern_guidance", extra={
                "symbol": symbol,
                "top_pattern": best_patterns[0]["pattern_name"] if best_patterns else None,
                "pattern_count": len(best_patterns),
            })

        base["best_patterns"] = best_patterns
        return base
