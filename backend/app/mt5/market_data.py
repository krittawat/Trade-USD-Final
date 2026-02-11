"""
Market Data — ดึงข้อมูลแท่งเทียนและ ticks จาก MT5.

กฎ RAM:
    - ใช้ windowed buffer — ไม่ load ข้อมูลทั้งหมดเข้า RAM
    - ข้อมูลเก่าถูก trim ออกเมื่อเกิน window size
    - ข้อมูลที่เก็บถาวรไปเข้า QuestDB แทน
"""

import pandas as pd
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# ขนาด window สูงสุด (จำนวนแท่งเทียน) — ป้องกันใช้ RAM เกิน
MAX_CANDLE_WINDOW = 500


def fetch_candles(
    symbol: str,
    timeframe: str = "M5",
    count: int = 200,
) -> Optional[pd.DataFrame]:
    """
    ดึงแท่งเทียนจาก MT5.
    
    Args:
        symbol: สัญลักษณ์เทรด เช่น XAUUSD
        timeframe: ไทม์เฟรม เช่น M1, M5, H1, D1
        count: จำนวนแท่ง (จำกัดไม่เกิน MAX_CANDLE_WINDOW)
    
    Returns:
        DataFrame: คอลัมน์ [time, open, high, low, close, volume]
        None: ถ้าดึงไม่ได้
    """
    # จำกัด count ไม่ให้เกิน window
    count = min(count, MAX_CANDLE_WINDOW)
    
    # TODO: mt5.copy_rates_from_pos(symbol, timeframe_map[timeframe], 0, count)
    logger.debug("fetch_candles", extra={
        "symbol": symbol,
        "timeframe": timeframe,
        "count": count,
    })
    return None


def fetch_ticks(
    symbol: str,
    count: int = 100,
) -> Optional[pd.DataFrame]:
    """
    ดึง tick data จาก MT5.
    
    ใช้สำหรับ: ดู spread ปัจจุบัน, คำนวณ micro-structure.
    จำกัดจำนวนเพื่อไม่ให้ใช้ RAM เยอะ.
    """
    count = min(count, 1000)  # จำกัด tick window
    
    # TODO: mt5.copy_ticks_from_pos(symbol, 0, count, mt5.COPY_TICKS_ALL)
    logger.debug("fetch_ticks", extra={"symbol": symbol, "count": count})
    return None
