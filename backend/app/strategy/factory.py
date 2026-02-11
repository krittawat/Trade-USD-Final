"""
Strategy Factory — เลือกและปรับแต่ง strategy ตามสภาวะตลาด.

หน้าที่:
    - เลือก strategy ที่เหมาะกับ symbol + regime + session
    - ใช้ข้อมูลจาก AI Brain เพื่อเลือก strategy ที่ performance ดีที่สุด
    - Validate ทุก strategy ก่อนใช้งาน (ผ่าน validators.py)
    - Return Decision object เท่านั้น — ไม่ส่งออเดอร์เอง

การเลือก strategy:
    1. ดูจาก regime (trending/ranging/high-vol)
    2. ดูจาก session (Asia/London/NY)
    3. ถาม AI Brain: "อะไรที่เคยใช้ได้ผลดีในสภาวะนี้?"
    4. Fallback: ใช้ default strategy
"""

from typing import Optional

import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)


class StrategyFactory:
    """
    โรงงานผลิต Strategy — เลือกและสร้าง strategy ที่เหมาะสม.
    
    วิธีใช้:
        factory = StrategyFactory()
        factory.register(ScalpingStrategy())
        factory.register(SniperStrategy())
        
        decision = factory.get_decision(candles, profile, regime)
    """

    def __init__(self) -> None:
        # ลงทะเบียน strategy ทั้งหมด
        self._strategies: dict[str, BaseStrategy] = {}
        # default strategy name
        self._default: str | None = None

    def register(self, strategy: BaseStrategy) -> None:
        """
        ลงทะเบียน strategy ใหม่.
        
        Args:
            strategy: instance ของ BaseStrategy
        """
        self._strategies[strategy.name] = strategy
        logger.info("strategy_registered", extra={"name": strategy.name})
        
        # strategy แรกที่ลงทะเบียนเป็น default
        if self._default is None:
            self._default = strategy.name

    def select_strategy(
        self,
        regime: RegimeType = RegimeType.UNKNOWN,
        session: str = "CLOSED",
        brain_recommendation: str | None = None,
    ) -> Optional[BaseStrategy]:
        """
        เลือก strategy ที่เหมาะสม.
        
        ลำดับความสำคัญ:
            1. AI Brain recommendation (ถ้ามี)
            2. Strategy ที่เหมาะกับ regime ปัจจุบัน  
            3. Default strategy
        """
        # --- 1. ถ้า AI Brain แนะนำ ---
        if brain_recommendation and brain_recommendation in self._strategies:
            logger.info("strategy_selected_by_brain", extra={
                "strategy": brain_recommendation,
            })
            return self._strategies[brain_recommendation]

        # --- 2. หา strategy ที่เหมาะกับ regime ---
        for name, strategy in self._strategies.items():
            if regime in strategy.suitable_regimes:
                logger.info("strategy_selected_by_regime", extra={
                    "strategy": name,
                    "regime": regime.value,
                })
                return strategy

        # --- 3. Fallback: ใช้ default ---
        if self._default and self._default in self._strategies:
            logger.info("strategy_selected_default", extra={
                "strategy": self._default,
            })
            return self._strategies[self._default]

        logger.warning("no_strategy_available")
        return None

    def get_decision(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        session: str = "CLOSED",
        brain_recommendation: str | None = None,
    ) -> Decision:
        """
        เลือก strategy แล้ววิเคราะห์ → return Decision.
        
        ถ้าไม่มี strategy ที่เหมาะ → return HOLD.
        """
        strategy = self.select_strategy(regime, session, brain_recommendation)
        
        if strategy is None:
            return Decision(
                symbol=profile.symbol,
                action="HOLD",
                confidence=0.0,
                reason="ไม่มี strategy ที่เหมาะสมกับสภาวะตลาดปัจจุบัน",
            )

        try:
            decision = strategy.analyze(candles, profile, regime)
            logger.info("strategy_decision", extra={
                "symbol": profile.symbol,
                "strategy": strategy.name,
                "action": decision.action,
                "confidence": decision.confidence,
                "stage": "signal",
                "result": "ok",
            })
            return decision
        except Exception as e:
            # ห้าม silent fail — log error แล้ว return HOLD
            logger.error("strategy_error", extra={
                "symbol": profile.symbol,
                "strategy": strategy.name,
                "error": str(e),
                "type": type(e).__name__,
                "stage": "signal",
                "result": "error",
            }, exc_info=True)
            return Decision(
                symbol=profile.symbol,
                action="HOLD",
                confidence=0.0,
                reason=f"Strategy error: {e}",
                strategy_name=strategy.name,
            )
