"""
Market Data — ดึงข้อมูลแท่งเทียนและ ticks จาก MT5 จริง.

กฎ RAM:
    - ใช้ windowed buffer — ไม่ load ข้อมูลทั้งหมดเข้า RAM
    - ข้อมูลเก่าถูก trim ออกเมื่อเกิน window size
"""

import pandas as pd
import time
from typing import Optional

import MetaTrader5 as mt5

from app.core.logging import get_logger

logger = get_logger(__name__)

# ขนาด window สูงสุด (จำนวนแท่งเทียน) — ปรองกันการใช้ RAM เกิน (100k bars ≈ 8MB)
MAX_CANDLE_WINDOW = 100000

# ─── Cycle-level terminal cache ───
# ป้องกัน mt5.terminal_info() ถูกเรียกซ้ำ 20+ ครั้ง/cycle
_terminal_cache_cycle: int = -1
_terminal_cache_ok: bool = False


def reset_terminal_cache(cycle: int) -> None:
    """Reset terminal check cache — เรียก 1 ครั้ง/cycle จาก MasterLoop."""
    global _terminal_cache_cycle, _terminal_cache_ok
    _terminal_cache_cycle = cycle
    _terminal_cache_ok = False  # จะถูก set ใน fetch_candles ครั้งแรก


def _is_terminal_ready(cycle: int) -> bool:
    """ตรวจ terminal_info() แค่ครั้งเดียวต่อ cycle."""
    global _terminal_cache_cycle, _terminal_cache_ok
    if cycle == _terminal_cache_cycle and _terminal_cache_ok:
        return True  # ใช้ cache
    terminal_info = mt5.terminal_info()
    if terminal_info is None:
        _terminal_cache_ok = False
        return False
    _terminal_cache_ok = True
    _terminal_cache_cycle = cycle
    return True

# Timeframe mapping
TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4,
    "M5": mt5.TIMEFRAME_M5,
    "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10,
    "M12": mt5.TIMEFRAME_M12,
    "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
    "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4,
    "H6": mt5.TIMEFRAME_H6,
    "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


def fetch_candles(
    symbol: str,
    timeframe: str = "M5",
    count: int = 200,
    cycle: int = -1,
) -> Optional[pd.DataFrame]:
    """
    ดึงแท่งเทียนจาก MT5 จริง.

    Args:
        symbol: สัญลักษณ์เทรด เช่น XAUUSDc
        timeframe: ไทม์เฟรม เช่น M1, M5, H1, D1
        count: จำนวนแท่ง (จำกัดไม่เกิน MAX_CANDLE_WINDOW)
        cycle: cycle number สำหรับ terminal cache

    Returns:
        DataFrame: คอลัมน์ [time, open, high, low, close, volume]
            - มี DatetimeIndex สำหรับ pandas_ta VWAP
        None: ถ้าดึงไม่ได้
    """
    # จำกัด count ไม่ให้เกิน window
    count = min(count, MAX_CANDLE_WINDOW)

    # ตรวจ MT5 ผ่าน cache (1 call/cycle แทน N calls)
    if not _is_terminal_ready(cycle):
        if not getattr(fetch_candles, '_warned', False):
            logger.warning("mt5_not_initialized", extra={
                "symbol": symbol, "detail": "MT5 not initialized — cannot fetch candles",
            })
            fetch_candles._warned = True  # type: ignore
        return None


    # แปลง timeframe string → MT5 enum
    tf = TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        logger.error("invalid_timeframe", extra={"timeframe": timeframe, "symbol": symbol})
        return None

    # Ensure symbol is selected / visible in Market Watch
    if not mt5.symbol_select(symbol, True):
        logger.error("symbol_select_failed", extra={"symbol": symbol})
        return None

    # ดึง candles จริงจาก MT5 ด้วย retry logic สำหรับ "Terminal: Call failed" (-1)
    rates = None
    for attempt in range(3):
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is not None and len(rates) > 0:
            break
        
        err = mt5.last_error()
        if attempt < 2:
            logger.warning("fetch_retry", extra={
                "symbol": symbol, "timeframe": timeframe, 
                "attempt": attempt + 1, "error": str(err)
            })
            time.sleep(0.5)

    if rates is None or len(rates) == 0:
        logger.error("NO_CANDLE_DATA", extra={
            "symbol": symbol,
            "timeframe": timeframe,
            "count": count,
            "error": str(mt5.last_error()),
        })
        return None

    # สร้าง DataFrame
    df = pd.DataFrame(rates)

    # แปลง time → DatetimeIndex (สำคัญสำหรับ pandas_ta VWAP)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("time", inplace=True)
    df.sort_index(inplace=True)

    # rename columns ให้ตรงกับ standard OHLCV
    # เก็บทั้ง volume + tick_volume เพื่อ backward compatibility กับทุก strategy
    if "tick_volume" in df.columns:
        df["volume"] = df["tick_volume"]  # standard OHLCV name
        # tick_volume ยังอยู่ — strategies เก่ายังใช้ได้

    # ลบ columns ที่ไม่จำเป็น
    for col in ["spread", "real_volume"]:
        if col in df.columns:
            df.drop(columns=[col], inplace=True)

    logger.debug("candles_fetched", extra={
        "symbol": symbol,
        "timeframe": timeframe,
        "bars": len(df),
        "last_close": float(df["close"].iloc[-1]) if len(df) > 0 else 0,
    })

    return df


def fetch_ticks(
    symbol: str,
    count: int = 100,
) -> Optional[pd.DataFrame]:
    """
    ดึง tick data จาก MT5 จริง.

    ใช้สำหรับ: ดู spread ปัจจุบัน, คำนวณ micro-structure.
    จำกัดจำนวนเพื่อไม่ให้ใช้ RAM เยอะ.
    """
    count = min(count, 1000)  # จำกัด tick window

    ticks = mt5.copy_ticks_from(symbol, 0, count, mt5.COPY_TICKS_ALL)

    if ticks is None or len(ticks) == 0:
        logger.debug("no_tick_data", extra={"symbol": symbol})
        return None

    df = pd.DataFrame(ticks)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)

    logger.debug("ticks_fetched", extra={"symbol": symbol, "count": len(df)})
    return df


def fetch_ticks_since(
    symbol: str, 
    from_time_ms: int
) -> list[dict]:
    """
    Fetch ticks starting from a specific timestamp (milliseconds).
    Used for incremental ingestion to QuestDB.
    
    Args:
        symbol: Symbol name
        from_time_ms: Start timestamp in milliseconds
        
    Returns:
        List of dicts ready for ingestion
    """
    # MT5 copy_ticks_from args: symbol, from_date (datetime or int ms), count, flags
    ticks = mt5.copy_ticks_from(symbol, from_time_ms + 1, 5000, mt5.COPY_TICKS_ALL)
    
    if ticks is None or len(ticks) == 0:
        return []

    # Convert to list of dicts directly (faster than DataFrame for simple iteration)
    # ticks is a numpy record array
    result = []
    for t in ticks:
        result.append({
            "time": float(t['time']),  # seconds
            "bid": float(t['bid']),
            "ask": float(t['ask']),
            "last": float(t['last']),
            "volume": int(t['volume']),
            "flags": int(t['flags']),
        })
        
    return result
