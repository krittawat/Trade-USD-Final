"""
Anti-Stop-Hunt SL Engine — วาง SL หลบเจ้ามือ.

3 Layers of Protection:
  1. Swing Structure — SL ใต้/เหนือ Swing Low/High จริง
  2. Round Number Dodge — หลบเลขกลม ($X.00, $X0.00)
  3. Buffer Randomization — เพิ่ม buffer สุ่มเล็กน้อย

Logic:
  BUY  → SL = min(ATR-based, Swing Low) - buffer
  SELL → SL = max(ATR-based, Swing High) + buffer

กฎเหล็ก:
  - SL ห้ามข้าม entry price
  - SL ต้อง >= MIN_SL_DISTANCE เสมอ
  - Lot size ไม่เพิ่ม เพราะ SL กว้างขึ้น → lot เล็กลงอัตโนมัติ
"""

import numpy as np
import pandas as pd
import logging
from typing import Optional

logger = logging.getLogger("AntiHuntSL")


# =============================================
# Layer 1: Swing Structure Detection
# =============================================

def find_swing_low(df: pd.DataFrame, lookback: int = 20, order: int = 3) -> Optional[float]:
    """
    หา Swing Low ล่าสุดจาก N แท่ง.
    
    Swing Low = จุดที่ low ต่ำกว่า `order` แท่งซ้าย-ขวา.
    
    Args:
        df: OHLCV DataFrame
        lookback: จำนวนแท่งย้อนหลังที่จะสแกน
        order: จำนวนแท่งข้างละกี่แท่งที่ low ต้องต่ำกว่า
    
    Returns:
        Swing Low price หรือ None ถ้าหาไม่เจอ
    """
    if df is None or len(df) < lookback:
        return None

    lows = df['low'].values[-lookback:]
    
    # สแกนหา fractal low: low[i] <= low[i-order..i+order]
    for i in range(len(lows) - 1 - order, order - 1, -1):
        is_swing = True
        for j in range(1, order + 1):
            if lows[i] > lows[i - j] or lows[i] > lows[i + j]:
                is_swing = False
                break
        if is_swing:
            return float(lows[i])
    
    # Fallback: ใช้ lowest low ทั้ง lookback
    return float(np.min(lows))


def find_swing_high(df: pd.DataFrame, lookback: int = 20, order: int = 3) -> Optional[float]:
    """
    หา Swing High ล่าสุดจาก N แท่ง.
    
    Swing High = จุดที่ high สูงกว่า `order` แท่งซ้าย-ขวา.
    """
    if df is None or len(df) < lookback:
        return None

    highs = df['high'].values[-lookback:]
    
    for i in range(len(highs) - 1 - order, order - 1, -1):
        is_swing = True
        for j in range(1, order + 1):
            if highs[i] < highs[i - j] or highs[i] < highs[i + j]:
                is_swing = False
                break
        if is_swing:
            return float(highs[i])
    
    return float(np.max(highs))


# =============================================
# Layer 2: Round Number Dodge
# =============================================

def dodge_round_numbers(sl_price: float, direction: str, dodge_distance: float = 1.5) -> float:
    """
    ถ้า SL อยู่ใกล้เลขกลม ให้ shift ออก.
    
    Round Numbers ที่ target:
      - $X0.00 (ทุก $10)  — strong magnet
      - $X5.00 (ทุก $5)   — medium magnet
      - $X.00  (ทุก $1)   — weak magnet (Gold specific)
    
    Args:
        sl_price: SL price ก่อน dodge
        direction: "BUY" หรือ "SELL"
        dodge_distance: ระยะ shift ออกจากเลขกลม ($)
    
    Returns:
        SL price หลัง dodge
    """
    # Check proximity to round numbers (strongest first)
    round_levels = [
        (10.0, dodge_distance * 1.5),   # $10 levels — strong dodge
        (5.0,  dodge_distance),           # $5 levels — medium dodge
        (1.0,  dodge_distance * 0.5),     # $1 levels — light dodge
    ]
    
    for interval, shift in round_levels:
        nearest_round = round(sl_price / interval) * interval
        distance_to_round = abs(sl_price - nearest_round)
        
        # ถ้าอยู่ใกล้เลขกลมเกินไป (< shift distance)
        if distance_to_round < shift:
            if direction == "BUY":
                # Shift SL ลงอีก (ห่างจากราคา entry มากขึ้น)
                sl_price = nearest_round - shift
            else:  # SELL
                # Shift SL ขึ้นอีก (ห่างจากราคา entry มากขึ้น)
                sl_price = nearest_round + shift
            
            logger.debug(f"[AntiHunt] Dodged round ${nearest_round:.2f} → SL ${sl_price:.2f}")
            break  # Dodge ครั้งเดียว ไม่ซ้อน
    
    return sl_price


# =============================================
# Layer 3: Buffer Randomization
# =============================================

def apply_buffer(sl_price: float, atr: float, direction: str, 
                 buffer_min: float = 0.3, buffer_max: float = 0.7) -> float:
    """
    เพิ่ม buffer สุ่มเล็กน้อยให้ SL ไม่อยู่จุดเดียวกับ retail.
    
    Buffer = ATR × random(0.3, 0.7) — เพิ่มตาม volatility
    
    Args:
        sl_price: SL price
        atr: ATR value
        direction: "BUY" หรือ "SELL"
        buffer_min: ATR multiplier minimum
        buffer_max: ATR multiplier maximum
    """
    # Deterministic "random" based on price level
    # (ใช้ fractional part ของ SL price เป็น seed — ไม่ต้อง random จริง 
    #  เพื่อให้ backtest reproducible)
    frac = abs(sl_price * 100) % 100 / 100  # 0.00 - 0.99
    buffer_mult = buffer_min + frac * (buffer_max - buffer_min)
    buffer = atr * buffer_mult
    
    if direction == "BUY":
        return sl_price - buffer  # กว้างออกอีก
    else:
        return sl_price + buffer  # กว้างออกอีก


# =============================================
# Main Entry Point
# =============================================

def apply_anti_hunt_sl(
    df: pd.DataFrame,
    close: float,
    atr: float,
    direction: str,
    atr_mult: float = 2.0,
    min_sl_distance: float = 3.0,
    swing_lookback: int = 20,
    swing_order: int = 3,
    enable_swing: bool = True,
    enable_round_dodge: bool = True,
    enable_buffer: bool = True,
) -> float:
    """
    Anti-Stop-Hunt SL Engine — วาง SL หลบเจ้ามือ.
    
    Pipeline:
      1. คำนวณ ATR-based SL (baseline)
      2. หา Swing Structure → ใช้จุดที่ให้ protection ดีกว่า
      3. Dodge round numbers
      4. Apply buffer
      5. Clamp ให้ >= min distance & ห้ามข้าม entry
    
    Args:
        df: OHLCV DataFrame
        close: Current close price
        atr: ATR value
        direction: "BUY" or "SELL"
        atr_mult: ATR multiplier for baseline SL
        min_sl_distance: Minimum SL distance ($)
        swing_lookback: Bars to look back for swing detection
        swing_order: Fractal order for swing detection
        enable_swing: Enable swing structure layer
        enable_round_dodge: Enable round number dodge layer
        enable_buffer: Enable buffer randomization layer
    
    Returns:
        Final anti-hunt SL price
    """
    # --- Step 1: Baseline ATR SL ---
    sl_dist = max(atr * atr_mult, min_sl_distance)
    
    if direction == "BUY":
        atr_sl = close - sl_dist
    else:
        atr_sl = close + sl_dist
    
    sl = atr_sl
    applied_layers = ["ATR"]
    
    # --- Step 2: Swing Structure ---
    if enable_swing and df is not None and len(df) >= swing_lookback:
        if direction == "BUY":
            swing_low = find_swing_low(df, lookback=swing_lookback, order=swing_order)
            if swing_low is not None:
                # ใช้ Swing Low ถ้ามัน "กว้างกว่า" ATR SL (ให้ protection ดีกว่า)
                # แต่ไม่กว้างจนเกินไป (max 2x ของ ATR SL distance)
                swing_sl = swing_low - (atr * 0.3)  # ใต้ swing อีกนิด
                max_allowed_dist = sl_dist * 2.0
                
                if swing_sl < close and (close - swing_sl) <= max_allowed_dist:
                    # Swing low ป้องกันดีกว่า → ใช้มัน
                    if swing_sl < sl:
                        sl = swing_sl
                        applied_layers.append("Swing")
                elif swing_sl < close:
                    # Swing ไกลเกินไป → ใช้ ATR SL แต่ reserve info
                    applied_layers.append("SwingTooFar")
        else:  # SELL
            swing_high = find_swing_high(df, lookback=swing_lookback, order=swing_order)
            if swing_high is not None:
                swing_sl = swing_high + (atr * 0.3)
                max_allowed_dist = sl_dist * 2.0
                
                if swing_sl > close and (swing_sl - close) <= max_allowed_dist:
                    if swing_sl > sl:
                        sl = swing_sl
                        applied_layers.append("Swing")
                elif swing_sl > close:
                    applied_layers.append("SwingTooFar")
    
    # --- Step 3: Round Number Dodge ---
    if enable_round_dodge:
        sl_before_dodge = sl
        sl = dodge_round_numbers(sl, direction, dodge_distance=1.5)
        if abs(sl - sl_before_dodge) > 0.01:
            applied_layers.append("RoundDodge")
    
    # --- Step 4: Buffer Randomization ---
    if enable_buffer:
        sl = apply_buffer(sl, atr, direction)
        applied_layers.append("Buffer")
    
    # --- Step 5: Safety Clamps ---
    # 5a. ห้าม SL ข้าม Entry Price
    if direction == "BUY" and sl >= close:
        sl = close - min_sl_distance
        applied_layers.append("ClampEntry")
    elif direction == "SELL" and sl <= close:
        sl = close + min_sl_distance
        applied_layers.append("ClampEntry")
    
    # 5b. Enforce minimum distance
    actual_dist = abs(close - sl)
    if actual_dist < min_sl_distance:
        if direction == "BUY":
            sl = close - min_sl_distance
        else:
            sl = close + min_sl_distance
        applied_layers.append("ClampMin")
    
    # Round to 2 decimal places (Gold standard)
    sl = round(sl, 2)
    
    layers_str = "+".join(applied_layers)
    logger.debug(
        f"[AntiHunt] {direction} | close={close:.2f} atr={atr:.2f} "
        f"| ATR_SL={atr_sl:.2f} → Final={sl:.2f} | Layers: {layers_str}"
    )
    
    return sl


# =============================================
# Trailing Anti-Hunt — ใช้ตอนย้าย SL (lightweight)
# =============================================

def apply_anti_hunt_trailing(
    sl_price: float,
    direction: str,
    dodge_distance: float = 1.5,
    point: float = 0.01,
    digits: int = 2,
    enable_round_dodge: bool = True,
    enable_buffer: bool = True,
) -> float:
    """
    Anti-Hunt สำหรับ Trailing SL — ย้าย SL หลบเลขกลม + buffer.

    ใช้ 2 layers (ไม่ scan swing — เพราะ trailing ย้ายบ่อย ต้องเร็ว):
      1. Round Number Dodge — หลบ $X0, $X5, $X.00
      2. Buffer — deterministic micro-shift

    Args:
        sl_price: SL price ที่คำนวณได้จาก trailing logic
        direction: "BUY" หรือ "SELL"
        dodge_distance: ระยะ dodge จากเลขกลม ($)
        point: minimum price tick (0.01 for Gold, 0.00001 for Forex)
        digits: decimal places สำหรับ rounding
        enable_round_dodge: เปิด/ปิด round number dodge
        enable_buffer: เปิด/ปิด buffer

    Returns:
        SL price หลัง anti-hunt adjustment
    """
    original_sl = sl_price

    # Layer 1: Round Number Dodge
    if enable_round_dodge:
        sl_price = dodge_round_numbers(sl_price, direction, dodge_distance)

    # Layer 2: Micro-buffer (deterministic — reproducible ใน backtest)
    if enable_buffer:
        # Buffer เล็ก = 3-7 ticks (deterministic จาก price level)
        frac = abs(sl_price * 100) % 100 / 100  # 0.00 - 0.99
        buffer_ticks = 3 + frac * 4  # 3-7 ticks
        buffer = buffer_ticks * point

        if direction == "BUY":
            sl_price -= buffer  # BUY: SL ต่ำลงอีกนิด (กว้างขึ้น)
        else:
            sl_price += buffer  # SELL: SL สูงขึ้นอีกนิด (กว้างขึ้น)

    sl_price = round(sl_price, digits)

    if abs(sl_price - original_sl) > 0.001:
        logger.debug(
            f"[AntiHuntTrail] {direction} | before={original_sl:.{digits}f} "
            f"→ after={sl_price:.{digits}f}"
        )

    return sl_price
