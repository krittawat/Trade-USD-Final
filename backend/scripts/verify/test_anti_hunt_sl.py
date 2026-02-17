"""
Test Anti-Stop-Hunt SL Engine.

ตรวจสอบว่า:
  1. Swing detection ทำงาน
  2. Round number dodge ย้ายออก
  3. SL >= minimum distance เสมอ
  4. SL ไม่ข้าม entry price
  5. Buffer ถูก apply
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pandas as pd
import numpy as np
import pytest

from app.risk.anti_hunt_sl import (
    find_swing_low,
    find_swing_high,
    dodge_round_numbers,
    apply_buffer,
    apply_anti_hunt_sl,
)


def _make_df(prices: list[float], spread: float = 2.0) -> pd.DataFrame:
    """Helper: สร้าง OHLCV DataFrame จากรายการ close prices."""
    n = len(prices)
    df = pd.DataFrame({
        'open':  [p - 0.5 for p in prices],
        'high':  [p + spread / 2 for p in prices],
        'low':   [p - spread / 2 for p in prices],
        'close': prices,
        'tick_volume': [100] * n,
    })
    return df


def _make_df_with_swing() -> pd.DataFrame:
    """สร้าง df ที่มี Swing Low ชัดเจนที่ $2880."""
    # สร้างข้อมูลที่มี swing low ที่ bar 10
    prices = [2900, 2895, 2890, 2888, 2885, 2883, 2882, 2881, 2880.5, 2880.0,
              2880.0, 2881, 2883, 2885, 2888, 2890, 2893, 2895, 2898, 2900]
    
    lows = [p - 1.0 for p in prices]
    lows[9] = 2878.0   # Swing Low ที่แท้จริง
    lows[10] = 2878.0
    
    highs = [p + 1.0 for p in prices]
    highs[9] = 2881.0
    
    df = pd.DataFrame({
        'open':  [p - 0.3 for p in prices],
        'high':  highs,
        'low':   lows,
        'close': prices,
        'tick_volume': [500] * len(prices),
    })
    return df


# =============================================
# Test Layer 1: Swing Detection
# =============================================

class TestSwingDetection:
    def test_swing_low_found(self):
        df = _make_df_with_swing()
        sl = find_swing_low(df, lookback=20, order=3)
        assert sl is not None
        assert sl <= 2880.0  # Should find the swing low area

    def test_swing_high_found(self):
        df = _make_df_with_swing()
        sh = find_swing_high(df, lookback=20, order=3)
        assert sh is not None
        assert sh >= 2898.0  # Should find swing high area

    def test_insufficient_data(self):
        df = _make_df([100.0, 101.0, 102.0])
        sl = find_swing_low(df, lookback=20)
        assert sl is None  # Not enough data

    def test_fallback_to_lowest(self):
        """ถ้าไม่มี fractal swing → ใช้ lowest low."""
        prices = list(range(100, 120))
        df = _make_df([float(p) for p in prices])
        sl = find_swing_low(df, lookback=20, order=3)
        assert sl is not None


# =============================================
# Test Layer 2: Round Number Dodge
# =============================================

class TestRoundNumberDodge:
    def test_dodge_ten_dollar(self):
        """SL ใกล้ $2890.00 → shift ออก."""
        sl = dodge_round_numbers(2889.5, "BUY", dodge_distance=1.5)
        assert sl < 2889.5  # Shifted further away
        assert abs(sl - 2890.0) > 1.0  # Not near $2890

    def test_dodge_five_dollar(self):
        """SL ใกล้ $2885.00 → shift ออก."""
        sl = dodge_round_numbers(2884.8, "BUY", dodge_distance=1.5)
        assert sl < 2884.8

    def test_no_dodge_needed(self):
        """SL ไม่ใกล้เลขกลม → ไม่ shift."""
        sl = dodge_round_numbers(2887.33, "BUY", dodge_distance=1.5)
        # Should still be close to original (may shift slightly for $1 level)
        assert abs(sl - 2887.33) < 2.0

    def test_sell_dodge_up(self):
        """SELL → dodge ขึ้น."""
        sl = dodge_round_numbers(2900.3, "SELL", dodge_distance=1.5)
        assert sl > 2900.3  # Shifted further away (up)


# =============================================
# Test Layer 3: Buffer
# =============================================

class TestBuffer:
    def test_buy_buffer_widens(self):
        """BUY → buffer ขยาย SL ลง."""
        sl = apply_buffer(2890.0, atr=3.0, direction="BUY")
        assert sl < 2890.0

    def test_sell_buffer_widens(self):
        """SELL → buffer ขยาย SL ขึ้น."""
        sl = apply_buffer(2910.0, atr=3.0, direction="SELL")
        assert sl > 2910.0

    def test_buffer_is_deterministic(self):
        """Same inputs → same output (reproducible)."""
        sl1 = apply_buffer(2890.0, atr=3.0, direction="BUY")
        sl2 = apply_buffer(2890.0, atr=3.0, direction="BUY")
        assert sl1 == sl2


# =============================================
# Test Full Pipeline
# =============================================

class TestAntiHuntSL:
    def test_buy_sl_below_entry(self):
        """BUY SL ต้องต่ำกว่า close เสมอ."""
        df = _make_df_with_swing()
        sl = apply_anti_hunt_sl(df, close=2900.0, atr=3.0, direction="BUY")
        assert sl < 2900.0

    def test_sell_sl_above_entry(self):
        """SELL SL ต้องสูงกว่า close เสมอ."""
        df = _make_df_with_swing()
        sl = apply_anti_hunt_sl(df, close=2880.0, atr=3.0, direction="SELL")
        assert sl > 2880.0

    def test_minimum_distance_enforced(self):
        """SL distance >= min_sl_distance."""
        df = _make_df_with_swing()
        min_dist = 5.0
        sl = apply_anti_hunt_sl(df, close=2900.0, atr=0.5, direction="BUY",
                                atr_mult=1.0, min_sl_distance=min_dist)
        assert abs(2900.0 - sl) >= min_dist

    def test_not_on_round_number(self):
        """SL ไม่ควรอยู่ตรงเลขกลม $10."""
        df = _make_df([float(p) for p in range(2880, 2920)] * 2)
        sl = apply_anti_hunt_sl(df, close=2906.0, atr=3.0, direction="BUY",
                                atr_mult=2.0)
        # SL should not be exactly on a $10 round number
        assert abs(sl % 10.0) > 0.5, f"SL {sl:.2f} too close to round $10"

    def test_layers_can_be_disabled(self):
        """Disable all layers → ทำงานเหมือน ATR SL ปกติ."""
        df = _make_df_with_swing()
        sl = apply_anti_hunt_sl(
            df, close=2900.0, atr=3.0, direction="BUY",
            atr_mult=2.0, min_sl_distance=3.0,
            enable_swing=False, enable_round_dodge=False, enable_buffer=False
        )
        expected = 2900.0 - max(3.0 * 2.0, 3.0)
        assert abs(sl - expected) < 0.01


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
