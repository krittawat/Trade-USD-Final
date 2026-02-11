"""
Strategy Base — Interface สำหรับทุก strategy.

ทุก strategy ต้อง:
    1. สืบทอดจาก BaseStrategy
    2. Implement method analyze() → Decision
    3. ห้ามส่งออเดอร์เอง — return Decision object เท่านั้น
    4. Decision ต้องมี SL ถ้า action เป็น BUY/SELL

Strategy ทำได้แค่:
    - วิเคราะห์ข้อมูล (candles, indicators)
    - สร้าง Decision (BUY/SELL/HOLD + เหตุผล)

Strategy ห้ามทำ:
    - เรียก MT5 โดยตรง
    - ส่งคำสั่งเทรด
    - แก้ไข SL/TP ของออเดอร์
    - อ่าน/เขียนสถานะบัญชี
"""

from abc import ABC, abstractmethod

import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

logger = get_logger(__name__)


class BaseStrategy(ABC):
    """
    Abstract base class สำหรับทุก strategy.
    
    วิธีใช้:
        class MyStrategy(BaseStrategy):
            name = "my_strategy"
            
            def analyze(self, candles, profile) -> Decision:
                # วิเคราะห์ข้อมูล...
                return Decision(symbol=profile.symbol, action=Action.BUY, ...)
    """

    # ชื่อ strategy — ต้องกำหนดใน subclass
    name: str = "base"
    
    # ไทม์เฟรมที่ใช้
    timeframe: str = "M5"
    
    # สภาวะตลาดที่เหมาะ
    suitable_regimes: list[RegimeType] = []

    @abstractmethod
    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
    ) -> Decision:
        """
        วิเคราะห์ข้อมูลและสร้าง Decision.
        
        Args:
            candles: DataFrame ของแท่งเทียน [time, open, high, low, close, volume]
            profile: ข้อมูลสัญลักษณ์
            regime: สภาวะตลาดปัจจุบัน
        
        Returns:
            Decision: คำสั่ง BUY/SELL/HOLD พร้อมเหตุผลและ SL/TP
        
        กฎ:
            - ถ้า BUY/SELL → ต้องมี stop_loss
            - ต้อง deterministic (ผลเหมือนเดิมถ้า input เหมือนเดิม)
            - ห้าม repaint (ห้ามมองอนาคต)
        """
        ...

    def create_hold(self, symbol: str, reason: str) -> Decision:
        """
        สร้าง HOLD Decision — ไม่ทำอะไร พร้อมเหตุผล.
        
        ใช้เมื่อ: สัญญาณไม่ชัด, ตลาด sideways, confidence ต่ำ.
        """
        return Decision(
            symbol=symbol,
            action=Action.HOLD,
            confidence=0.0,
            reason=reason,
            strategy_name=self.name,
            timeframe=self.timeframe,
        )
