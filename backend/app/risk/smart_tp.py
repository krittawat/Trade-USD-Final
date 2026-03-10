"""
Smart Take-Profit Engine — ตั้ง TP อัจฉริยะ (เพื่อน anti_hunt_sl.py).

4 Layers of TP Intelligence:
  1. ATR Projection — TP = entry ± ATR × multiplier (baseline)
  2. Swing Target — TP ที่ Swing High/Low ลำดับถัดไป
  3. Round Number Magnet — TP ที่เลขกลม (ปิดก่อนถูก reject)
  4. Dynamic RR Optimization — ปรับ RR ตาม volatility regime

กฎ:
  - TP ต้องอยู่ฝั่งกำไร (ห้ามข้าม entry)
  - TP >= MIN_RR × SL distance เสมอ (min 1.5:1 RR)
  - TP ที่ดีที่สุด = confluence ของ swing + round + ATR
"""

import numpy as np
import pandas as pd
import logging
from typing import Optional

logger = logging.getLogger("SmartTP")


# =============================================
# Layer 1: ATR-Based TP Projection
# =============================================

def atr_based_tp(
    entry: float,
    atr: float,
    direction: str,
    sl_distance: float,
    min_rr: float = 1.5,
    target_rr: float = 2.0,
) -> float:
    """
    คำนวณ TP จาก ATR projection.

    Pipeline:
      1. Base TP = entry ± ATR × target_rr (ใช้ SL distance เป็นฐาน)
      2. Clamp ให้ >= min_rr × SL distance

    Args:
        entry: ราคาเข้า
        atr: ATR value
        direction: "BUY" or "SELL"
        sl_distance: ระยะ SL จาก entry (absolute)
        min_rr: Minimum Risk-Reward ratio
        target_rr: Target Risk-Reward ratio
    """
    # ใช้ SL distance เป็นฐาน (1R) × target RR
    tp_dist = max(sl_distance * target_rr, atr * target_rr)

    # Enforce minimum RR
    min_tp_dist = sl_distance * min_rr
    tp_dist = max(tp_dist, min_tp_dist)

    if direction == "BUY":
        return entry + tp_dist
    else:
        return entry - tp_dist


# =============================================
# Layer 2: Swing Target Detection
# =============================================

def find_tp_swing_target(
    df: pd.DataFrame,
    entry: float,
    direction: str,
    lookback: int = 50,
    order: int = 3,
) -> Optional[float]:
    """
    หา Swing High/Low ที่อยู่ฝั่งกำไร — ใช้เป็น TP target.

    BUY  → หา Swing High เหนือ entry (resistance = TP)
    SELL → หา Swing Low ใต้ entry (support = TP)

    Returns:
        TP price ที่ swing level หรือ None ถ้าหาไม่เจอ
    """
    if df is None or len(df) < lookback:
        return None

    if direction == "BUY":
        # หา resistance levels (Swing Highs) เหนือ entry
        highs = df['high'].values[-lookback:]
        candidates = []

        for i in range(len(highs) - 1 - order, order - 1, -1):
            is_swing = True
            for j in range(1, order + 1):
                if highs[i] < highs[i - j] or highs[i] < highs[i + j]:
                    is_swing = False
                    break
            if is_swing and highs[i] > entry:
                candidates.append(float(highs[i]))

        if candidates:
            # ใช้ swing high ที่ใกล้ entry ที่สุด (conservative)
            return min(candidates)

    else:  # SELL
        # หา support levels (Swing Lows) ใต้ entry
        lows = df['low'].values[-lookback:]
        candidates = []

        for i in range(len(lows) - 1 - order, order - 1, -1):
            is_swing = True
            for j in range(1, order + 1):
                if lows[i] > lows[i - j] or lows[i] > lows[i + j]:
                    is_swing = False
                    break
            if is_swing and lows[i] < entry:
                candidates.append(float(lows[i]))

        if candidates:
            # ใช้ swing low ที่ใกล้ entry ที่สุด (conservative)
            return max(candidates)

    return None


# =============================================
# Layer 3: Round Number Magnet
# =============================================

def find_round_number_tp(
    entry: float,
    direction: str,
    sl_distance: float,
    min_rr: float = 1.5,
) -> Optional[float]:
    """
    หา TP ที่เลขกลม (round number) ในฝั่งกำไร.

    เลขกลมเป็น psychological level ที่ราคามักถูก reject
    → ปิดก่อนถูก reject = เพิ่มโอกาสถึง TP

    Round levels ที่ scan:
      - $X0.00 (ทุก $10)  — strongest
      - $X5.00 (ทุก $5)   — strong
      - $X.00  (ทุก $1)   — moderate (Forex pairs)
    """
    min_tp_dist = sl_distance * min_rr

    if direction == "BUY":
        # Scan เลขกลมเหนือ entry
        search_start = entry + min_tp_dist
        search_end = entry + sl_distance * 5  # max scan 5R

        best = None
        for interval in [10.0, 5.0, 1.0]:
            # หาเลขกลมที่ใกล้ที่สุดที่อยู่เหนือ search_start
            nearest = np.ceil(search_start / interval) * interval
            if search_start <= nearest <= search_end:
                if best is None or nearest < best:
                    best = nearest

        return best

    else:  # SELL
        search_start = entry - min_tp_dist
        search_end = entry - sl_distance * 5

        best = None
        for interval in [10.0, 5.0, 1.0]:
            nearest = np.floor(search_start / interval) * interval
            if search_end <= nearest <= search_start:
                if best is None or nearest > best:
                    best = nearest

        return best


# =============================================
# Layer 4: Dynamic RR Optimizer
# =============================================

def optimize_rr(
    atr: float,
    entry: float,
    candles: pd.DataFrame | None = None,
) -> float:
    """
    ปรับ RR ratio ตาม volatility regime.

    Volatile market → RR สูงขึ้น (ราคาวิ่งไกลกว่า)
    Low vol market → RR ต่ำลง (กิน realistic profit)

    Returns:
        Optimized RR ratio (1.5 - 3.0)
    """
    if atr <= 0 or entry <= 0:
        return 2.0  # default

    # ATR as percentage of price
    atr_pct = (atr / entry) * 100

    # คำนวณ recent volatility trend
    vol_trend = 1.0
    if candles is not None and len(candles) >= 30:
        recent_range = candles['high'].tail(10).values - candles['low'].tail(10).values
        older_range = candles['high'].iloc[-30:-10].values - candles['low'].iloc[-30:-10].values

        if len(older_range) > 0 and np.mean(older_range) > 0:
            vol_trend = np.mean(recent_range) / np.mean(older_range)

    # Gold (XAUUSD): ATR ~15-30 points → atr_pct ~0.5-1.5%
    # Forex major: ATR ~50-100 pips → atr_pct ~0.3-0.8%
    if atr_pct > 1.0:
        # High volatility → wider TP (more room to run)
        base_rr = 2.5
    elif atr_pct > 0.5:
        # Normal volatility
        base_rr = 2.0
    else:
        # Low volatility → tighter TP (realistic)
        base_rr = 1.5

    # Adjust by volatility trend
    if vol_trend > 1.3:
        base_rr = min(base_rr + 0.5, 3.0)  # Expanding vol → bigger TP
    elif vol_trend < 0.7:
        base_rr = max(base_rr - 0.3, 1.5)  # Contracting vol → smaller TP

    return round(base_rr, 1)


# =============================================
# Main Entry Point
# =============================================

def apply_smart_tp(
    df: pd.DataFrame | None,
    entry: float,
    sl_price: float,
    atr: float,
    direction: str,
    min_rr: float = 1.5,
    target_rr: float = 2.0,
    auto_optimize_rr: bool = True,
    swing_lookback: int = 50,
    swing_order: int = 3,
    enable_swing: bool = True,
    enable_round_magnet: bool = True,
) -> float:
    """
    Smart Take-Profit Engine — วาง TP อัจฉริยะ.

    Pipeline:
      1. คำนวณ SL distance จริง
      2. Optimize RR ตาม volatility (optional)
      3. คำนวณ ATR TP (baseline)
      4. หา Swing Target (resistance/support)
      5. หา Round Number Magnet
      6. เลือก TP ที่ confluence สูงสุด
      7. Enforce minimum RR

    Args:
        df: OHLCV DataFrame (None OK → fallback to ATR only)
        entry: ราคาเข้า
        sl_price: SL price
        atr: ATR value
        direction: "BUY" or "SELL"
        min_rr: Minimum RR ratio (default 1.5:1)
        target_rr: Target RR ratio (default 2.0:1)
        auto_optimize_rr: ปรับ RR ตาม volatility อัตโนมัติ
        swing_lookback: Bars for swing detection
        swing_order: Fractal order
        enable_swing: Enable swing target layer
        enable_round_magnet: Enable round number magnet layer

    Returns:
        Smart TP price
    """
    sl_distance = abs(entry - sl_price)
    if sl_distance <= 0:
        # Fallback: ใช้ ATR เป็น SL distance
        sl_distance = atr * 2.0 if atr > 0 else entry * 0.02

    applied_layers = []

    # --- Step 1: Dynamic RR Optimization ---
    rr = target_rr
    if auto_optimize_rr and atr > 0:
        rr = optimize_rr(atr, entry, df)
        if rr != target_rr:
            applied_layers.append(f"RR_opt({rr})")

    # --- Step 2: ATR Baseline TP ---
    atr_tp = atr_based_tp(entry, atr, direction, sl_distance, min_rr=min_rr, target_rr=rr)
    applied_layers.append("ATR")

    # --- Step 3: Swing Target ---
    swing_tp = None
    if enable_swing and df is not None and len(df) >= swing_lookback:
        swing_tp = find_tp_swing_target(df, entry, direction, swing_lookback, swing_order)
        if swing_tp is not None:
            # Validate RR ของ swing target
            swing_dist = abs(swing_tp - entry)
            swing_rr = swing_dist / sl_distance if sl_distance > 0 else 0
            if swing_rr < min_rr:
                swing_tp = None  # Swing ใกล้เกินไป (RR ต่ำ) → ข้าม
            else:
                applied_layers.append(f"Swing(RR={swing_rr:.1f})")

    # --- Step 4: Round Number Magnet ---
    round_tp = None
    if enable_round_magnet:
        round_tp = find_round_number_tp(entry, direction, sl_distance, min_rr=min_rr)
        if round_tp is not None:
            round_dist = abs(round_tp - entry)
            round_rr = round_dist / sl_distance if sl_distance > 0 else 0
            if round_rr < min_rr:
                round_tp = None
            else:
                applied_layers.append(f"Round(RR={round_rr:.1f})")

    # --- Step 5: Choose Best TP (Confluence Selection) ---
    # Strategy: ถ้า swing และ round อยู่ใกล้กัน → ใช้จุดที่ conservative กว่า
    #           ถ้ามีแค่ 1 target → ใช้มัน (ถ้า RR ดีพอ)
    #           ถ้าไม่มีเลย → ใช้ ATR TP

    candidates = [("atr", atr_tp)]

    if swing_tp is not None:
        candidates.append(("swing", swing_tp))

    if round_tp is not None:
        candidates.append(("round", round_tp))

    # ตรวจ confluence: swing ≈ round → strong signal
    if swing_tp and round_tp:
        confluence_gap = abs(swing_tp - round_tp)
        if confluence_gap <= atr * 1.5:
            # Swing + Round ใกล้กัน → ใช้ตัวที่ conservative (ใกล้ entry) กว่า
            if direction == "BUY":
                best_tp = min(swing_tp, round_tp)
            else:
                best_tp = max(swing_tp, round_tp)
            applied_layers.append("Confluence")
            tp = best_tp
        else:
            # ห่างกัน → เลือก swing (structure-based น่าเชื่อถือกว่า)
            tp = swing_tp
    elif swing_tp:
        tp = swing_tp
    elif round_tp:
        # Round number proximity check — ใช้ round ถ้า RR ดี
        round_dist = abs(round_tp - entry)
        atr_dist = abs(atr_tp - entry)
        if round_dist >= min_rr * sl_distance and round_dist <= atr_dist * 1.2:
            tp = round_tp
        else:
            tp = atr_tp
    else:
        tp = atr_tp

    # --- Step 6: Enforce Minimum RR ---
    final_dist = abs(tp - entry)
    min_tp_dist = sl_distance * min_rr

    if final_dist < min_tp_dist:
        if direction == "BUY":
            tp = entry + min_tp_dist
        else:
            tp = entry - min_tp_dist
        applied_layers.append("ClampMinRR")

    # --- Step 7: Safety — TP ต้องอยู่ฝั่งกำไร ---
    if direction == "BUY" and tp <= entry:
        tp = entry + min_tp_dist
        applied_layers.append("ClampEntry")
    elif direction == "SELL" and tp >= entry:
        tp = entry - min_tp_dist
        applied_layers.append("ClampEntry")

    # Round to 2 decimal places
    tp = round(tp, 2)

    layers_str = "+".join(applied_layers)
    logger.debug(
        f"[SmartTP] {direction} | entry={entry:.2f} sl={sl_price:.2f} atr={atr:.2f} "
        f"| ATR_TP={atr_tp:.2f} swing_TP={swing_tp} round_TP={round_tp} "
        f"→ Final={tp:.2f} | RR={abs(tp - entry) / sl_distance:.1f}:1 "
        f"| Layers: {layers_str}"
    )


    return tp
