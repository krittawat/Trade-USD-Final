"""
Scalping Strategy — Template สำหรับ scalping.

ลักษณะ:
    - ไทม์เฟรม: M1-M5
    - เป้าหมาย: กำไรเร็ว, RR 1:1 - 1:2
    - เหมาะกับ: ตลาดที่มีสภาพคล่องสูง, spread แคบ
    - เซสชัน: London, NY, Overlap

TODO: Implement indicators (EMA, RSI, VWAP) เมื่อพร้อม
"""

import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)


class ScalpingStrategy(BaseStrategy):
    """
    Scalping Strategy — เทรดสั้น กำไรเร็ว.
    
    เหมาะกับตลาดที่มี volatility ปานกลาง-สูง
    และ spread แคบ.
    """

    name = "scalping"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
    ]

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
    ) -> Decision:
        """
        วิเคราะห์สัญญาณ scalping.
        
        TODO: Implement logic จริง:
            - EMA crossover (8/21)
            - RSI filter
            - VWAP reference
            - Volume confirmation
        """
        # Stub: return HOLD จนกว่าจะ implement indicators
        return self.create_hold(
            symbol=profile.symbol,
            reason="Scalping strategy ยังไม่ได้ implement indicators — รอ Phase 2",
        )
