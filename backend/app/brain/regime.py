"""
Regime Classifier — จำแนกสภาวะตลาด.

ใช้ข้อมูลจาก indicators เพื่อจัดกลุ่มตลาดเป็น:
    - TRENDING_UP / TRENDING_DOWN
    - RANGING (sideways)
    - HIGH_VOLATILITY / LOW_VOLATILITY

วิธีจำแนก:
    - ADX > 25 = trending
    - ADX < 20 = ranging
    - ATR สูงกว่า avg = high volatility
"""

import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import RegimeType

logger = get_logger(__name__)


def classify_regime(candles: pd.DataFrame) -> RegimeType:
    """
    จำแนกสภาวะตลาดจากแท่งเทียน.
    
    Args:
        candles: DataFrame ที่มี [open, high, low, close, volume]
    
    Returns:
        RegimeType: ประเภทสภาวะตลาด
    """
    if candles is None or len(candles) < 20:
        return RegimeType.UNKNOWN

    # TODO: Implement จริง:
    # 1. คำนวณ ADX → trending vs ranging
    # 2. คำนวณ ATR → volatility level
    # 3. ดู EMA slope → up vs down trend
    
    logger.debug("regime_classified", extra={"regime": "UNKNOWN", "bars": len(candles)})
    return RegimeType.UNKNOWN
