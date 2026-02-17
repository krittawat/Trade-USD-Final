"""
Regime Classifier — จำแนกสภาวะตลาดจริงด้วย ADX + ATR + EMA.

วิธีจำแนก:
    1. ADX > 25 = trending → ดู EMA slope เพื่อแยก UP/DOWN
    2. ADX < 20 = ranging/sideways
    3. ATR > 1.5x avg = high volatility
    4. ATR < 0.5x avg = low volatility

ใช้ใน:
    - Factory: เลือก strategy ที่เหมาะมั่ regime
    - Gate: ป้องกัน overtrading ใน sideways
    - Brain: track performance ต่อ regime
"""

import pandas as pd
import pandas_ta as ta

from app.core.logging import get_logger
from app.domain.enums import RegimeType

logger = get_logger(__name__)

# --- Parameters ---
ADX_PERIOD = 14
ADX_TRENDING = 25     # ADX > 25 = trending
ADX_RANGING = 20      # ADX < 20 = ranging
ATR_PERIOD = 14
ATR_VOL_MULT = 1.5    # ATR > 1.5x avg = high vol
ATR_LOW_MULT = 0.5    # ATR < 0.5x avg = low vol
EMA_SLOPE_PERIOD = 10  # bars สำหรับหา EMA slope direction


def classify_regime(candles: pd.DataFrame) -> RegimeType:
    """
    จำแนกสภาวะตลาดจากแท่งเทียน (real logic).

    Args:
        candles: DataFrame ที่มี [open, high, low, close, volume]

    Returns:
        RegimeType: ประเภทสภาวะตลาด
    """
    if candles is None or len(candles) < ADX_PERIOD + 10:
        return RegimeType.UNKNOWN

    # --- คำนวณ ADX ---
    adx_df = ta.adx(candles["high"], candles["low"], candles["close"], length=ADX_PERIOD)
    if adx_df is None:
        return RegimeType.UNKNOWN

    adx_col = f"ADX_{ADX_PERIOD}"
    if adx_col not in adx_df.columns:
        return RegimeType.UNKNOWN

    adx_val = adx_df[adx_col].iloc[-1]
    if pd.isna(adx_val):
        return RegimeType.UNKNOWN

    # --- คำนวณ ATR (volatility) ---
    atr = ta.atr(candles["high"], candles["low"], candles["close"], length=ATR_PERIOD)
    if atr is None or pd.isna(atr.iloc[-1]):
        atr_ratio = 1.0
    else:
        atr_current = atr.iloc[-1]
        atr_avg = atr.iloc[-ATR_PERIOD * 3:].mean()  # avg ของช่วงที่ยาวกว่า
        atr_ratio = atr_current / atr_avg if atr_avg > 0 else 1.0

    # --- ตรวจ Volatility สุดโต่ง ---
    if atr_ratio > ATR_VOL_MULT:
        regime = RegimeType.HIGH_VOLATILITY
        logger.debug("regime_classified", extra={
            "regime": regime.value,
            "adx": round(adx_val, 1),
            "atr_ratio": round(atr_ratio, 2),
        })
        return regime

    if atr_ratio < ATR_LOW_MULT:
        regime = RegimeType.LOW_VOLATILITY
        logger.debug("regime_classified", extra={
            "regime": regime.value,
            "adx": round(adx_val, 1),
            "atr_ratio": round(atr_ratio, 2),
        })
        return regime

    # --- Trending vs Ranging ---
    if adx_val > ADX_TRENDING:
        # ดู EMA slope สำหรับทิศทาง
        ema = ta.ema(candles["close"], length=20)
        if ema is not None and len(ema) >= EMA_SLOPE_PERIOD:
            slope = ema.iloc[-1] - ema.iloc[-EMA_SLOPE_PERIOD]
            regime = RegimeType.TRENDING_UP if slope > 0 else RegimeType.TRENDING_DOWN
        else:
            regime = RegimeType.TRENDING_UP  # default trending up
    elif adx_val < ADX_RANGING:
        regime = RegimeType.RANGING
    else:
        # ADX 20-25 = อยู่กลางๆ → ดู context เพิ่ม
        ema = ta.ema(candles["close"], length=20)
        if ema is not None and len(ema) >= EMA_SLOPE_PERIOD:
            slope = ema.iloc[-1] - ema.iloc[-EMA_SLOPE_PERIOD]
            if abs(slope) > atr.iloc[-1] * 0.3 if atr is not None and not pd.isna(atr.iloc[-1]) else 0:
                regime = RegimeType.TRENDING_UP if slope > 0 else RegimeType.TRENDING_DOWN
            else:
                regime = RegimeType.RANGING
        else:
            regime = RegimeType.UNKNOWN

    logger.debug("regime_classified", extra={
        "regime": regime.value,
        "adx": round(adx_val, 1),
        "atr_ratio": round(atr_ratio, 2),
        "bars": len(candles),
    })

    return regime
