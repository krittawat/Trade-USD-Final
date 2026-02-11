"""
Trend Rider Strategy — Template สำหรับเทรดตามเทรนด์.

ลักษณะ:
    - ไทม์เฟรม: H1-H4
    - เป้าหมาย: ride the trend, RR 1:3 - 1:5
    - เหมาะกับ: ตลาด trending ยาว
    - ใช้ trailing stop เพื่อรักษากำไร

TODO: Implement logic: EMA 200, ADX filter, swing high/low
"""

import pandas as pd

from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy


class TrendRiderStrategy(BaseStrategy):
    """
    Trend Rider Strategy — ขี่เทรนด์ยาว.
    
    เทรดน้อย ถือนาน ใช้ trailing stop.
    """

    name = "trend_rider"
    timeframe = "H1"
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
        วิเคราะห์สัญญาณ trend riding.
        
        TODO: Implement:
            - EMA 200 filter (trend direction)
            - ADX > 25 (trend strength)
            - Pullback to moving average
            - Trailing stop logic
        """
        return self.create_hold(
            symbol=profile.symbol,
            reason="Trend Rider strategy ยังไม่ได้ implement — รอ Phase 2",
        )
