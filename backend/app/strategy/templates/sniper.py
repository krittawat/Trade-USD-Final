"""
Sniper Strategy — Template สำหรับ sniper (high-precision entry).

ลักษณะ:
    - ไทม์เฟรม: M15-H1
    - เป้าหมาย: entry แม่นยำ, RR 1:3+
    - เหมาะกับ: ตลาด trending ชัดเจน
    - เทรดน้อย แต่ accuracy สูง

TODO: Implement logic: structure break, order block, FVG
"""

import pandas as pd

from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy


class SniperStrategy(BaseStrategy):
    """
    Sniper Strategy — เข้าแม่น ออก RR สูง.
    
    เน้นคุณภาพ entry มากกว่าจำนวนเทรด.
    """

    name = "sniper"
    timeframe = "M15"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
    ]

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
    ) -> Decision:
        """
        วิเคราะห์สัญญาณ sniper.
        
        TODO: Implement:
            - Market structure (HH/HL/LH/LL)
            - Order block detection
            - Fair Value Gap (FVG)
            - Confluence scoring
        """
        return self.create_hold(
            symbol=profile.symbol,
            reason="Sniper strategy ยังไม่ได้ implement — รอ Phase 2",
        )
