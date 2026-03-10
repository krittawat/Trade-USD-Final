"""
Test Trailing Stop — ทดสอบ 6 modes + Anti-Hunt + Profit Lock Floor.

ตรวจ:
    - ATR mode: SL = peak − ATR × mult
    - Fixed mode: SL = peak − points
    - Step mode: ย้าย SL ทีละ step_r
    - Chandelier mode: HH/LL based
    - Adaptive mode: ATR mult ลดตามกำไร
    - Dynamic mode: 3 phases (BE → Step → ATR tightening)
    - Anti-Hunt: SL หลบเลขกลม + buffer ทุกครั้งที่ย้าย
    - Profit Lock Floor: SL ห้ามต่ำกว่า locked tier
    - SL ห้ามถอย (monotonic)
    - Activation threshold ทำงาน
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

# ป้องกัน MT5 import error ใน test environment
import sys
mt5_mock = MagicMock()
sys.modules["MetaTrader5"] = mt5_mock

from app.risk.trade_manager import (
    TrailingStopManager, TrailingConfig, TrailingState,
    ProfitLockManager, ProfitLockConfig, ProfitLockTier,
)


# ─── Helper: สร้าง mock MT5 client ───

class MockMT5:
    def __init__(self):
        self.modify_calls = []

    def modify_sl(self, ticket, new_sl, new_tp=0.0):
        self.modify_calls.append((ticket, new_sl, new_tp))
        return True


def make_pos(ticket=1, symbol="XAUUSDc", _type="BUY", entry=2000.0,
             current=2010.0, sl=1990.0, tp=2030.0, volume=0.10):
    return {
        "ticket": ticket, "symbol": symbol, "type": _type,
        "price_open": entry, "price_current": current,
        "sl": sl, "tp": tp, "volume": volume,
        "digits": 2, "magic": 888888,
    }


# ====================================================================
# ATR Mode Tests
# ====================================================================

class TestATRMode:
    def test_trail_up_on_profit(self):
        """ATR mode: SL ย้ายขึ้นเมื่อราคาขึ้นเกิน activation."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(mode="atr", atr_multiplier=2.0, activation_r=1.0, anti_hunt=False))

        pos = make_pos(current=2015.0)
        atr_values = {"XAUUSDc": 5.0}

        actions = mgr.update_all([pos], atr_values)

        # new_sl = peak(2015) - ATR(5) × 2 = 2005
        assert len(actions) == 1
        assert actions[0]["new_sl"] == 2005.0
        assert actions[0]["old_sl"] == 1990.0
        assert actions[0]["mode"] == "atr"

    def test_no_trail_below_activation(self):
        """ไม่ trail ถ้ายังไม่ถึง activation."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(mode="atr", activation_r=2.0, anti_hunt=False))

        pos = make_pos(current=2005.0)
        actions = mgr.update_all([pos])
        assert len(actions) == 0

    def test_sl_never_goes_backward(self):
        """SL ห้ามถอย — monotonic increase for BUY."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(mode="atr", atr_multiplier=1.0, activation_r=0.5, anti_hunt=False))

        pos1 = make_pos(current=2015.0)
        actions1 = mgr.update_all([pos1], {"XAUUSDc": 5.0})
        assert len(actions1) == 1
        first_sl = actions1[0]["new_sl"]

        pos2 = make_pos(current=2012.0, sl=first_sl)
        actions2 = mgr.update_all([pos2], {"XAUUSDc": 5.0})
        assert len(actions2) == 0

    def test_sell_position(self):
        """ATR mode for SELL: SL ย้ายลง."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(mode="atr", atr_multiplier=2.0, activation_r=1.0, anti_hunt=False))

        pos = make_pos(_type="SELL", entry=2000.0, sl=2010.0, current=1985.0)
        actions = mgr.update_all([pos], {"XAUUSDc": 5.0})

        assert len(actions) == 1
        assert actions[0]["new_sl"] == 1995.0


# ====================================================================
# Fixed Mode Tests
# ====================================================================

class TestFixedMode:
    def test_fixed_trail(self):
        """Fixed mode: SL = peak − N points."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(mode="fixed", fixed_points=8.0, activation_r=1.0, anti_hunt=False))

        pos = make_pos(current=2015.0)
        actions = mgr.update_all([pos])

        assert len(actions) == 1
        assert actions[0]["new_sl"] == 2007.0


# ====================================================================
# Step Mode Tests
# ====================================================================

class TestStepMode:
    def test_step_trail_first_threshold(self):
        """Step mode: ย้าย SL ที่ +1R (step_r=0.5 → first trigger at +0.5R)."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="step", step_r=0.5, activation_r=0.5, anti_hunt=False,
        ))

        pos = make_pos(current=2007.0)
        actions = mgr.update_all([pos])

        assert len(actions) == 1
        assert actions[0]["new_sl"] == 2000.0

    def test_step_second_threshold(self):
        """Step mode: หลัง first step ผ่าน → trigger second step."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="step", step_r=0.5, activation_r=0.5, anti_hunt=False,
        ))

        pos1 = make_pos(current=2007.0)
        mgr.update_all([pos1])

        pos2 = make_pos(current=2012.0, sl=1990.0)
        actions = mgr.update_all([pos2])

        assert len(actions) == 1
        assert actions[0]["new_sl"] == 2005.0


# ====================================================================
# Chandelier Mode Tests
# ====================================================================

class TestChandelierMode:
    def test_chandelier_with_candles(self):
        """Chandelier: SL = Highest High − ATR × mult."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="chandelier", atr_multiplier=2.0,
            chandelier_period=5, activation_r=1.0, anti_hunt=False,
        ))

        candles = pd.DataFrame({
            "high": [2015, 2016, 2018, 2020, 2019, 2017, 2016, 2015, 2014, 2013, 2012, 2011, 2010, 2009, 2008],
            "low":  [2010, 2011, 2013, 2015, 2014, 2012, 2011, 2010, 2009, 2008, 2007, 2006, 2005, 2004, 2003],
            "close":[2013, 2014, 2016, 2018, 2017, 2015, 2014, 2013, 2012, 2011, 2010, 2009, 2008, 2007, 2006],
        })

        pos = make_pos(current=2018.0)
        atr = {"XAUUSDc": 4.0}
        candles_cache = {"XAUUSDc": candles}

        actions = mgr.update_all([pos], atr, candles_cache)

        assert len(actions) == 1
        assert actions[0]["new_sl"] == 2004.0

    def test_chandelier_fallback_no_candles(self):
        """Chandelier ไม่มี candles → fallback เป็น ATR mode."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="chandelier", atr_multiplier=2.0, activation_r=1.0, anti_hunt=False,
        ))

        pos = make_pos(current=2015.0)
        actions = mgr.update_all([pos], {"XAUUSDc": 5.0})

        assert len(actions) == 1
        assert actions[0]["new_sl"] == 2005.0


# ====================================================================
# Adaptive Mode Tests
# ====================================================================

class TestAdaptiveMode:
    def test_adaptive_starts_wide(self):
        """Adaptive: กำไรน้อย → ใช้ max_mult → SL กว้าง."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="adaptive", activation_r=1.0,
            adaptive_min_mult=1.0, adaptive_max_mult=3.0, adaptive_ramp_r=3.0,
            anti_hunt=False,
        ))

        pos = make_pos(current=2010.0)
        actions = mgr.update_all([pos], {"XAUUSDc": 5.0})

        assert len(actions) == 1
        assert actions[0]["new_sl"] == pytest.approx(1998.33, abs=0.01)

    def test_adaptive_tightens_at_max_profit(self):
        """Adaptive: กำไรมาก → ใช้ min_mult → SL แน่น."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="adaptive", activation_r=1.0,
            adaptive_min_mult=1.0, adaptive_max_mult=3.0, adaptive_ramp_r=3.0,
            anti_hunt=False,
        ))

        pos = make_pos(current=2030.0)
        actions = mgr.update_all([pos], {"XAUUSDc": 5.0})

        assert len(actions) == 1
        assert actions[0]["new_sl"] == 2025.0


# ====================================================================
# Dynamic Mode Tests (ใหม่)
# ====================================================================

class TestDynamicMode:
    def test_phase1_be_plus_buffer(self):
        """Dynamic Phase 1: กำไร < phase2_r → SL ไป BE + buffer."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="dynamic", activation_r=0.5,
            dynamic_phase2_r=1.0, dynamic_phase3_r=2.0,
            anti_hunt=False,
        ))

        # entry=2000, SL=1990, sl_dist=10, activation=+5, current=2008 (+0.8R)
        pos = make_pos(current=2008.0)
        actions = mgr.update_all([pos])

        assert len(actions) == 1
        # Phase 1: SL = entry + buffer (5% ของ sl_dist=10 = 0.5)
        assert actions[0]["new_sl"] == 2000.5

    def test_phase2_step_trailing(self):
        """Dynamic Phase 2: กำไร >= phase2_r → Step trailing ทีละ step_r."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="dynamic", activation_r=0.5, step_r=0.5,
            dynamic_phase2_r=1.0, dynamic_phase3_r=2.0,
            anti_hunt=False,
        ))

        # ครั้งแรก: Phase 1 ที่ +0.8R
        pos1 = make_pos(current=2008.0)
        mgr.update_all([pos1])

        # ครั้งสอง: Phase 2 ที่ +1.2R → first step trigger
        pos2 = make_pos(current=2012.0, sl=1990.0)
        actions = mgr.update_all([pos2])

        assert len(actions) == 1
        # Step: SL = entry + (last_step_r=0 × sl_dist) = 2000 (BE)
        assert actions[0]["new_sl"] == 2000.0

    def test_phase3_atr_tightening(self):
        """Dynamic Phase 3: กำไร >= phase3_r → ATR trailing บีบแน่นขึ้น."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="dynamic", activation_r=0.5, step_r=0.5,
            dynamic_phase2_r=1.0, dynamic_phase3_r=2.0,
            adaptive_min_mult=1.0, adaptive_max_mult=3.0, adaptive_ramp_r=5.0,
            anti_hunt=False,
        ))

        # กำไร +2.5R (phase 3) → ATR trailing
        pos = make_pos(current=2025.0)
        actions = mgr.update_all([pos], {"XAUUSDc": 5.0})

        assert len(actions) == 1
        # ramp_start=2.0, ramp_end=5.0, current_r=2.5
        # progress = (2.5-2.0)/(5.0-2.0) = 0.167
        # mult = 3.0 - (3.0-1.0) × 0.167 = 2.667
        # SL = peak(2025) - 5.0 × 2.667 = 2025 - 13.33 = 2011.67
        assert actions[0]["new_sl"] == pytest.approx(2011.67, abs=0.01)

    def test_phase_transition_1_to_2(self):
        """Dynamic: transition จาก Phase 1 → Phase 2 ถูกต้อง."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="dynamic", activation_r=0.5, step_r=0.5,
            dynamic_phase2_r=1.0, dynamic_phase3_r=2.0,
            anti_hunt=False,
        ))

        # Phase 1: +0.6R → BE + buffer
        pos1 = make_pos(current=2006.0)
        a1 = mgr.update_all([pos1])
        assert len(a1) == 1
        assert a1[0]["new_sl"] == 2000.5  # BE + 5% buffer

        # Phase 2: +1.3R → Step trailing (trigger step 0.5)
        pos2 = make_pos(current=2013.0, sl=2000.5)
        a2 = mgr.update_all([pos2])
        assert len(a2) == 1
        # SL = entry + (last_step_r=0 × 10) = 2000
        # แต่ 2000 < current SL (2000.5) → monotonic ไม่ให้ถอย
        # ดังนั้น skip (SL ห้ามถอย)
        # หมายเหตุ: กรณีนี้ step ถัดไป last_step_r=0.5 แล้ว lock_dist=0
        # SL ใหม่ = entry + 0 = 2000.0 → ต่ำกว่า 2000.5 → skip

    def test_sell_dynamic_phase1(self):
        """Dynamic Phase 1 for SELL."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="dynamic", activation_r=0.5,
            dynamic_phase2_r=1.0, dynamic_phase3_r=2.0,
            anti_hunt=False,
        ))

        # SELL: entry=2000, SL=2010, current=1992 → profit=8, 0.8R
        pos = make_pos(_type="SELL", entry=2000.0, sl=2010.0, current=1992.0)
        actions = mgr.update_all([pos])

        assert len(actions) == 1
        # Phase 1 SELL: SL = entry - buffer = 2000 - 0.5 = 1999.5
        assert actions[0]["new_sl"] == 1999.5


# ====================================================================
# Anti-Hunt Trailing Tests (ใหม่)
# ====================================================================

class TestAntiHuntTrailing:
    def test_anti_hunt_dodges_round_number(self):
        """Anti-Hunt: SL ที่ใกล้ $2010.00 → ต้อง dodge ออก."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="atr", atr_multiplier=1.0, activation_r=1.0,
            anti_hunt=True, anti_hunt_dodge_pts=1.5,
        ))

        # ตั้งให้ ATR trailing คำนวณได้ SL = 2010.0 (เลขกลม!)
        # entry=2000, SL=1990, current=2020, ATR=10 → peak=2020, SL=2020-10=2010
        pos = make_pos(current=2020.0)
        actions = mgr.update_all([pos], {"XAUUSDc": 10.0})

        assert len(actions) == 1
        # SL ต้องไม่เท่ากับ 2010.0 (เลขกลม) — ต้อง dodge ออก
        assert actions[0]["new_sl"] != 2010.0
        # SL ต้อง < 2010 สำหรับ BUY (dodge ลงออกจากเลขกลม)
        assert actions[0]["new_sl"] < 2010.0
        assert actions[0]["new_sl"] > 1990.0  # ต้องดีกว่า SL เดิม

    def test_anti_hunt_off_no_dodge(self):
        """anti_hunt=False → SL ไม่ dodge."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="atr", atr_multiplier=1.0, activation_r=1.0,
            anti_hunt=False,
        ))

        pos = make_pos(current=2020.0)
        actions = mgr.update_all([pos], {"XAUUSDc": 10.0})

        assert len(actions) == 1
        assert actions[0]["new_sl"] == 2010.0  # ไม่ dodge → เลขกลมตรงๆ

    def test_anti_hunt_sell_direction(self):
        """Anti-Hunt สำหรับ SELL: SL ต้อง dodge ขึ้น."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="atr", atr_multiplier=1.0, activation_r=1.0,
            anti_hunt=True, anti_hunt_dodge_pts=1.5,
        ))

        # SELL: entry=2000, SL=2010, current=1985, ATR=5
        # peak=1985, SL=1985+5=1990 (เลขกลม $10 level!)
        pos = make_pos(_type="SELL", entry=2000.0, sl=2010.0, current=1985.0)
        actions = mgr.update_all([pos], {"XAUUSDc": 5.0})

        assert len(actions) == 1
        # SL ต้องไม่เท่ากับ 1990.0 — ต้อง dodge
        assert actions[0]["new_sl"] != 1990.0
        # SELL: SL ต้อง > 1990 (dodge ขึ้นออกจากเลขกลม)
        assert actions[0]["new_sl"] > 1990.0
        assert actions[0]["new_sl"] < 2010.0  # ต้องดีกว่า SL เดิม


# ====================================================================
# Profit Lock Floor Tests (ใหม่)
# ====================================================================

class TestProfitLockFloor:
    def test_sl_cannot_go_below_locked_tier(self):
        """Trailing SL ห้ามต่ำกว่า tier ที่ lock ไว้."""
        mt5 = MockMT5()
        lock_mgr = ProfitLockManager(mt5)
        mgr = TrailingStopManager(mt5, profit_lock_mgr=lock_mgr)

        # ตั้ง trailing + profit lock
        mgr.set_trailing(1, TrailingConfig(
            mode="atr", atr_multiplier=4.0, activation_r=1.0, anti_hunt=False,
        ))
        lock_mgr.set_profit_lock(1, ProfitLockConfig(tiers=[
            ProfitLockTier(r_multiple=1.0, lock_pct=0.0),    # +1R → BE
            ProfitLockTier(r_multiple=2.0, lock_pct=0.5),    # +2R → lock 50%
        ]))
        # จำลอง: tier 1 ถูก trigger แล้ว (+2R, lock 50%)
        lock_mgr._current_tier[1] = 1

        # ATR trailing: peak=2025, ATR=5, mult=4 → SL=2025-20=2005
        # แต่ locked tier (+2R, 50%) → SL floor = entry + (2R×sl_dist × 50%)
        # = 2000 + (20 × 0.5) = 2010
        # SL=2005 < floor=2010 → ต้องดัน SL ขึ้นเป็น 2010
        pos = make_pos(current=2025.0)
        actions = mgr.update_all([pos], {"XAUUSDc": 5.0})

        assert len(actions) == 1
        assert actions[0]["new_sl"] == 2010.0  # profit lock floor

    def test_no_floor_without_profit_lock(self):
        """ไม่มี profit lock → trailing ทำงานปกติ (ไม่มี floor)."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)  # ไม่ส่ง profit_lock_mgr

        mgr.set_trailing(1, TrailingConfig(
            mode="atr", atr_multiplier=4.0, activation_r=1.0, anti_hunt=False,
        ))

        pos = make_pos(current=2025.0)
        actions = mgr.update_all([pos], {"XAUUSDc": 5.0})

        assert len(actions) == 1
        assert actions[0]["new_sl"] == 2005.0  # ไม่มี floor → ATR ปกติ

    def test_floor_sell_direction(self):
        """Profit Lock Floor for SELL: SL ห้ามสูงกว่า locked tier."""
        mt5 = MockMT5()
        lock_mgr = ProfitLockManager(mt5)
        mgr = TrailingStopManager(mt5, profit_lock_mgr=lock_mgr)

        mgr.set_trailing(1, TrailingConfig(
            mode="atr", atr_multiplier=4.0, activation_r=1.0, anti_hunt=False,
        ))
        lock_mgr.set_profit_lock(1, ProfitLockConfig(tiers=[
            ProfitLockTier(r_multiple=1.0, lock_pct=0.0),
            ProfitLockTier(r_multiple=2.0, lock_pct=0.5),
        ]))
        lock_mgr._current_tier[1] = 1

        # SELL: entry=2000, SL=2010, current=1975
        # ATR trailing: peak=1975, SL=1975+20=1995
        # Locked floor SELL: entry - (2R×10 × 0.5) = 2000 - 10 = 1990
        # 1995 > 1990 → ต้องดัน SL ลงเป็น 1990
        pos = make_pos(_type="SELL", entry=2000.0, sl=2010.0, current=1975.0)
        actions = mgr.update_all([pos], {"XAUUSDc": 5.0})

        assert len(actions) == 1
        assert actions[0]["new_sl"] == 1990.0  # profit lock floor


# ====================================================================
# Edge Cases
# ====================================================================

class TestEdgeCases:
    def test_no_sl_skip(self):
        """ข้าม position ที่ไม่มี SL."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(mode="atr", anti_hunt=False))
        pos = make_pos(sl=0.0)
        actions = mgr.update_all([pos])
        assert len(actions) == 0

    def test_no_config_skip(self):
        """ข้าม position ที่ไม่มี trailing config."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        pos = make_pos()
        actions = mgr.update_all([pos])
        assert len(actions) == 0

    def test_off_mode_skip(self):
        """mode=off → ข้าม."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(mode="off"))
        pos = make_pos(current=2015.0)
        actions = mgr.update_all([pos])
        assert len(actions) == 0

    def test_cleanup_stale(self):
        """ลบ entries เก่ากว่า 24 ชม."""
        import time
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        for i in range(5010):
            cfg = TrailingConfig(mode="atr")
            cfg.created_at = time.time() - 100000
            mgr._configs[i] = cfg

        mgr.update_all([], {})
        assert len(mgr._configs) == 0

    def test_dynamic_mode_with_no_step_r(self):
        """Dynamic Phase 2 กับ step_r=0 → ข้าม Phase 2."""
        mt5 = MockMT5()
        mgr = TrailingStopManager(mt5)
        mgr.set_trailing(1, TrailingConfig(
            mode="dynamic", activation_r=0.5, step_r=0,
            dynamic_phase2_r=1.0, dynamic_phase3_r=2.0,
            anti_hunt=False,
        ))

        # +1.5R → Phase 2 แต่ step_r=0 → ไม่มี action
        pos = make_pos(current=2015.0)
        actions = mgr.update_all([pos])
        assert len(actions) == 0


# ====================================================================
# Anti-Hunt Trailing Function Unit Tests (ใหม่)
# ====================================================================

class TestAntiHuntTrailingFunction:
    def test_dodge_round_10(self):
        """apply_anti_hunt_trailing: SL ใกล้ $2010 → dodge ออก."""
        from app.risk.anti_hunt_sl import apply_anti_hunt_trailing

        result = apply_anti_hunt_trailing(
            sl_price=2010.0, direction="BUY",
            dodge_distance=1.5, point=0.01, digits=2,
        )
        # ต้องไม่เท่ากับ 2010.0 — dodge ออกแล้ว
        assert result != 2010.0
        # BUY: dodge ลงออกจากเลขกลม
        assert result < 2010.0

    def test_no_dodge_far_from_round(self):
        """SL ที่อยู่ไกลจากเลขกลม → dodge น้อย (แค่ buffer)."""
        from app.risk.anti_hunt_sl import apply_anti_hunt_trailing

        result = apply_anti_hunt_trailing(
            sl_price=2013.7, direction="BUY",
            dodge_distance=1.5, point=0.01, digits=2,
            enable_round_dodge=False,  # ปิด dodge เพื่อทดสอบ buffer เท่านั้น
        )
        # buffer เล็กน้อย (3-7 ticks × 0.01 = 0.03-0.07)
        diff = abs(2013.7 - result)
        assert diff < 0.10  # buffer ต้องเล็ก
        assert diff > 0.01  # แต่ต้องมี buffer

    def test_disabled_returns_original(self):
        """ปิดทุก layer → return ค่าเดิม."""
        from app.risk.anti_hunt_sl import apply_anti_hunt_trailing

        result = apply_anti_hunt_trailing(
            sl_price=2010.0, direction="BUY",
            enable_round_dodge=False, enable_buffer=False,
        )
        assert result == 2010.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
