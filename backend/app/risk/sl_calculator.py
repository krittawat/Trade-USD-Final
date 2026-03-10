"""
Smart SL Calculator — Centralized SL computation with Anti-Hunt protection.

รวมศูนย์การคำนวณ SL สำหรับทุก strategy เพื่อ:
1. Regime-Adaptive ATR Multiplier (ADX-based)
2. H1 ATR Smoothing (reduce M5 noise)
3. Anti-Hunt Integration (Swing + Round Dodge + Buffer)
4. Minimum SL distance enforcement
5. Structure-aware SL (Swing High/Low)

ทุก strategy เรียก calculate_smart_sl() แทนการ hardcode inline.
"""

import pandas as pd
import numpy as np
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════

def calculate_smart_sl(
    close: float,
    atr: float,
    direction: str,
    candles: pd.DataFrame,
    sl_atr_mult: float = 1.5,
    sl_buffer_atr: float = 0.3,
    swing_lookback: int = 15,
    min_sl_distance: float = 0.0,
    adx: Optional[float] = None,
    h1_atr: Optional[float] = None,
    symbol: str = "",
    enable_anti_hunt: bool = True,
    enable_regime_adaptive: bool = True,
    enable_h1_smoothing: bool = True,
) -> float:
    """
    Smart SL Calculator — คำนวณ SL ที่ปรับตาม regime + smooth + anti-hunt.

    Pipeline:
        1. Regime-Adaptive ATR mult (ADX → scale factor)
        2. H1 ATR Smoothing (blend M5 + H1)
        3. Structure-aware: Swing High/Low + buffer
        4. ATR-based SL
        5. เลือกจุดที่ปลอดภัยกว่า (wider = safer)
        6. Anti-Hunt: Swing detection + Round Dodge + Buffer
        7. Min SL distance enforcement

    Args:
        close: Current close price
        atr: M5 ATR value (14-period)
        direction: "BUY" or "SELL"
        candles: M5 OHLCV DataFrame
        sl_atr_mult: Base ATR multiplier for SL (strategy default)
        sl_buffer_atr: ATR multiplier for buffer beyond Swing point
        swing_lookback: Bars to look back for swing detection
        min_sl_distance: Minimum SL distance (absolute price units)
        adx: Current ADX value (for regime-adaptive adjustment)
        h1_atr: H1 ATR value (for smoothing M5 ATR noise)
        symbol: Symbol name (for anti-hunt round level detection)
        enable_anti_hunt: Enable anti-hunt 3-layer protection
        enable_regime_adaptive: Enable ADX-based ATR mult scaling
        enable_h1_smoothing: Enable H1 ATR blending

    Returns:
        Final SL price with all protections applied.
    """
    # ─── Step 1: Regime-Adaptive ATR Multiplier ───
    effective_mult = sl_atr_mult
    if enable_regime_adaptive and adx is not None:
        regime_scale = _get_regime_scale(adx)
        effective_mult = sl_atr_mult * regime_scale

    # ─── Step 1.5: ATR Force Expansion (Volatility Stretch) ───
    # If current ATR is significantly higher than historical baseline, stretch the SL
    stretch_factor = 1.0
    baseline_atr_period = 48  # Approx 4 hours on M5
    if candles is not None and len(candles) >= baseline_atr_period + 14:
        # Calculate a baseline ATR (SMA of the last 48 ATR values)
        # We approximate ATR as High - Low for speed here, or use actual TR if needed
        # simpler approximation for baseline: average candle size
        highs = candles["high"].astype(float)
        lows = candles["low"].astype(float)
        closes = candles["close"].astype(float)
        tr = pd.concat([
            highs - lows,
            (highs - closes.shift()).abs(),
            (lows - closes.shift()).abs(),
        ], axis=1).max(axis=1)
        baseline_atr = float(tr.rolling(baseline_atr_period).mean().iloc[-1])
        
        if baseline_atr > 0.0:
            atr_force_ratio = atr / baseline_atr
            if atr_force_ratio > 1.2:  # Volatility increased by > 20% compared to 4h baseline
                # Cap the stretch at 1.5x
                stretch_factor = min(atr_force_ratio, 1.5)
                effective_mult = effective_mult * stretch_factor

    # ─── Step 2: H1 ATR Smoothing ───
    effective_atr = atr
    if enable_h1_smoothing and h1_atr is not None and h1_atr > 0:
        # Blend: 70% M5 ATR + 30% H1 ATR (scaled to M5)
        # H1 ATR ÷ 12 ≈ per-M5-bar equivalent (12 M5 bars = 1 H1 bar)
        h1_atr_m5_equiv = h1_atr / np.sqrt(12)  # volatility scales with sqrt of time
        effective_atr = 0.70 * atr + 0.30 * h1_atr_m5_equiv

    # ─── Step 3: Structure-aware SL (Swing + buffer) ───
    sl_dist = effective_atr * effective_mult

    swing_high = None
    swing_low = None
    if candles is not None and len(candles) >= swing_lookback + 1:
        sw = candles.iloc[-(swing_lookback + 1):-1]
        swing_high = float(sw["high"].max())
        swing_low = float(sw["low"].min())

    if direction == "BUY":
        atr_sl = close - sl_dist
        if swing_low is not None:
            struct_sl = swing_low - effective_atr * sl_buffer_atr
            sl_price = min(struct_sl, atr_sl)  # wider = safer
        else:
            sl_price = atr_sl

        # Guard: SL ไม่ใกล้เกินไป (min 0.5 ATR)
        if (close - sl_price) < effective_atr * 0.5:
            sl_price = close - effective_atr * 1.0

    else:  # SELL
        atr_sl = close + sl_dist
        if swing_high is not None:
            struct_sl = swing_high + effective_atr * sl_buffer_atr
            sl_price = max(struct_sl, atr_sl)  # wider = safer
        else:
            sl_price = atr_sl

        if (sl_price - close) < effective_atr * 0.5:
            sl_price = close + effective_atr * 1.0

    # ─── Step 4: Min SL distance enforcement ───
    actual_dist = abs(close - sl_price)
    if min_sl_distance > 0 and actual_dist < min_sl_distance:
        if direction == "BUY":
            sl_price = close - min_sl_distance
        else:
            sl_price = close + min_sl_distance

    # ─── Step 5: Anti-Hunt 3-Layer Protection ───
    if enable_anti_hunt and candles is not None and len(candles) >= 20:
        try:
            from app.risk.anti_hunt_sl import apply_anti_hunt_sl
            sl_price = apply_anti_hunt_sl(
                df=candles,
                close=close,
                atr=effective_atr,
                direction=direction,
                atr_mult=effective_mult,
                min_sl_distance=max(min_sl_distance, effective_atr * 0.5),
                swing_lookback=swing_lookback,
                swing_order=3,
                enable_swing=True,
                enable_round_dodge=True,
                enable_buffer=True,
            )
        except Exception as e:
            logger.debug("anti_hunt_sl_fallback", extra={
                "error": str(e), "symbol": symbol,
            })
            # Fallback: ใช้ SL ที่คำนวณจาก Step 3

    # ─── Step 6: Final safety clamp ───
    # SL ห้ามข้าม entry price
    if direction == "BUY" and sl_price >= close:
        sl_price = close - max(min_sl_distance, effective_atr * 0.5)
    elif direction == "SELL" and sl_price <= close:
        sl_price = close + max(min_sl_distance, effective_atr * 0.5)

    logger.debug("smart_sl_calculated", extra={
        "symbol": symbol, "direction": direction,
        "close": round(close, 2), "sl": round(sl_price, 2),
        "sl_dist": round(abs(close - sl_price), 4),
        "atr_m5": round(atr, 4), "effective_atr": round(effective_atr, 4),
        "effective_mult": round(effective_mult, 2),
        "stretch_factor": round(stretch_factor, 2),
        "adx": round(adx, 1) if adx else None,
        "regime_adaptive": enable_regime_adaptive,
        "h1_smoothing": enable_h1_smoothing,
        "anti_hunt": enable_anti_hunt,
    })

    return sl_price


# ═══════════════════════════════════════════════
# REGIME-ADAPTIVE SCALING
# ═══════════════════════════════════════════════

def _get_regime_scale(adx: float) -> float:
    """
    ปรับ ATR multiplier ตาม regime (ADX-based).

    Logic:
        ADX >= 40 → Strong Trend  → SL tighter (0.85×) — trend จะวิ่งต่อ
        ADX 30-40 → Moderate      → SL normal (1.0×)
        ADX 20-30 → Weak/Ranging  → SL wider (1.1×) — whipsaw risk
        ADX < 20  → Choppy        → SL widest (1.2×) — max protection

    Returns:
        Scale factor to multiply with base sl_atr_mult.
    """
    if adx >= 40:
        return 0.85   # Strong trend: SL tighter (trend follow)
    elif adx >= 30:
        return 1.0    # Moderate: default
    elif adx >= 20:
        return 1.10   # Weak/ranging: wider buffer
    else:
        return 1.20   # Choppy: widest protection


# ═══════════════════════════════════════════════
# H1 ATR HELPER
# ═══════════════════════════════════════════════

def compute_h1_atr(h1_candles: pd.DataFrame, period: int = 14) -> Optional[float]:
    """
    คำนวณ H1 ATR จาก H1 candles.

    Args:
        h1_candles: H1 OHLCV DataFrame
        period: ATR period (default 14)

    Returns:
        H1 ATR value or None if insufficient data.
    """
    if h1_candles is None or len(h1_candles) < period + 2:
        return None

    high = h1_candles["high"].astype(float)
    low = h1_candles["low"].astype(float)
    close = h1_candles["close"].astype(float)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr_series = tr.rolling(period).mean()
    atr_val = float(atr_series.iloc[-1])

    if pd.isna(atr_val) or atr_val <= 0:
        return None

    return atr_val


# ═══════════════════════════════════════════════
# BREAK-EVEN ANTI-HUNT DODGE
# ═══════════════════════════════════════════════

def calculate_be_with_dodge(
    entry_price: float,
    direction: str,
    symbol: str = "",
    dodge_buffer: float = 0.50,
) -> float:
    """
    คำนวณจุด Break-Even ที่หลบ Stop Hunt.

    แทนที่จะย้าย SL มาที่ entry price ตรงๆ (ซึ่งเป็นเป้า stop hunt)
    จะขยับไปทิศ profit เล็กน้อยเพื่อ lock micro-profit:
        BUY  → entry + dodge_buffer (SL สูงกว่า entry)
        SELL → entry - dodge_buffer (SL ต่ำกว่า entry)

    จากนั้น apply round number dodge ถ้ามันไม่ดัน SL ผิดทาง.

    Args:
        entry_price: ราคาเข้า
        direction: "BUY" or "SELL"
        symbol: Symbol name
        dodge_buffer: ระยะขยับจาก entry ($)

    Returns:
        BE SL price ที่ dodge แล้ว.
    """
    # Base: ขยับไปทิศทางกำไรเล็กน้อย (lock micro-profit)
    if direction == "BUY":
        be_sl = entry_price + dodge_buffer
    else:
        be_sl = entry_price - dodge_buffer

    # Apply round number dodge — but only if it keeps SL on correct side
    try:
        from app.risk.anti_hunt_sl import dodge_round_numbers
        dodged = dodge_round_numbers(be_sl, direction, dodge_distance=dodge_buffer)

        # Safety: dodge must keep SL on profit side of entry
        if direction == "BUY" and dodged >= entry_price:
            be_sl = dodged
        elif direction == "SELL" and dodged <= entry_price:
            be_sl = dodged
        # else: dodge would push past entry, skip it
    except Exception:
        pass

    # Final safety clamp: always on profit side
    if direction == "BUY" and be_sl < entry_price:
        be_sl = entry_price + dodge_buffer
    elif direction == "SELL" and be_sl > entry_price:
        be_sl = entry_price - dodge_buffer

    # Determine digits
    s = symbol.upper()
    if "XAU" in s or "GOLD" in s:
        digits = 2
    elif "XAG" in s or "SILVER" in s:
        digits = 3
    elif "BTC" in s:
        digits = 1
    elif "JPY" in s:
        digits = 3
    else:
        digits = 5

    return round(be_sl, digits)
