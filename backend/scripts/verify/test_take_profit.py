"""
Test Take Profit Manager — ทดสอบ 3 modes ของ TakeProfitManager.

ตรวจ:
    - Partial TP: ปิดบางส่วนตาม R-target + ย้าย SL
    - Dynamic TP: ย้าย TP ตาม ATR
    - Trailing TP: TP ถอยมาเมื่อราคาใกล้แล้วย้อน
    - Volume calculation ถูกต้อง
    - SL auto-move after partial
"""

import pytest
from unittest.mock import MagicMock

# Prevent MT5 import errors
import sys
sys.modules["MetaTrader5"] = MagicMock()

from app.risk.trade_manager import TakeProfitManager, TPConfig, TPTier, TPState


# ─── Mock MT5 Client ───

class MockMT5:
    def __init__(self):
        self.partial_calls = []
        self.close_calls = []
        self.modify_calls = []

    def partial_close(self, ticket, volume, reason=""):
        self.partial_calls.append((ticket, volume, reason))
        return True

    def close_position(self, ticket, reason=""):
        self.close_calls.append((ticket, reason))
        return True

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
# Partial TP Tests
# ====================================================================

class TestPartialTP:
    def test_tier1_trigger_at_1r(self):
        """Tier 1: +1R → ปิด 30% + SL → BE."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)

        tiers = [
            TPTier(r_target=1.0, close_pct=0.3, move_sl_to="be"),
            TPTier(r_target=2.0, close_pct=0.3, move_sl_to="prev_tp"),
            TPTier(r_target=3.0, close_pct=1.0, move_sl_to=None),
        ]
        mgr.set_tp(1, TPConfig(mode="partial", tiers=tiers), volume=0.10)

        # +1R: entry=2000, SL=1990 → 1R=10, current=2010/2012 → ≥1R ✓
        pos = make_pos(current=2012.0, volume=0.10)
        actions = mgr.check_all([pos])

        assert len(actions) == 1
        assert actions[0]["action"] == "partial_tp_triggered"
        assert actions[0]["tier"] == 0
        assert actions[0]["r_target"] == 1.0

        # ตรวจ partial close: 30% ของ 0.10 = 0.03
        assert len(mt5.partial_calls) == 1
        assert mt5.partial_calls[0][1] == pytest.approx(0.03, abs=0.001)

        # ตรวจ SL moved to BE (entry=2000)
        assert "sl_moved_to" in actions[0]
        assert actions[0]["sl_moved_to"] == 2000.0

    def test_tier2_moves_sl_to_prev_r(self):
        """Tier 2: +2R → ปิด 30% + SL → +1R."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)

        tiers = [
            TPTier(r_target=1.0, close_pct=0.3, move_sl_to="be"),
            TPTier(r_target=2.0, close_pct=0.3, move_sl_to="prev_tp"),
            TPTier(r_target=3.0, close_pct=1.0, move_sl_to=None),
        ]
        mgr.set_tp(1, TPConfig(mode="partial", tiers=tiers), volume=0.10)

        # Skip to +2R by setting state
        state = mgr._states[1]
        state.highest_tier_hit = 0  # tier 0 already done
        state.remaining_volume = 0.07  # after 30% close

        # IMPORTANT: keep SL=1990 so sl_distance = |2000-1990| = 10
        pos = make_pos(current=2022.0, volume=0.07, sl=1990.0)
        actions = mgr.check_all([pos])

        assert len(actions) == 1
        assert actions[0]["tier"] == 1
        # SL → +1R = entry + 1.0×10 = 2010
        assert actions[0]["sl_moved_to"] == 2010.0

    def test_tier3_closes_all(self):
        """Tier 3: +3R → ปิดทั้งหมดที่เหลือ."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)

        tiers = [
            TPTier(r_target=3.0, close_pct=1.0, move_sl_to=None),
        ]
        mgr.set_tp(1, TPConfig(mode="partial", tiers=tiers), volume=0.10)

        # +3R: profit = 30
        pos = make_pos(current=2030.0, volume=0.10)
        actions = mgr.check_all([pos])

        assert len(actions) == 1
        # close_pct=1.0 → close all → uses close_position
        assert len(mt5.close_calls) == 1

    def test_no_trigger_below_threshold(self):
        """ไม่ trigger ถ้ากำไรยังไม่ถึง R target."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)
        mgr.set_tp(1, TPConfig(mode="partial"), volume=0.10)

        # profit=+5 < 1R(10)
        pos = make_pos(current=2005.0, volume=0.10)
        actions = mgr.check_all([pos])

        assert len(actions) == 0
        assert len(mt5.partial_calls) == 0

    def test_sell_partial_tp(self):
        """Partial TP for SELL position."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)

        tiers = [TPTier(r_target=1.0, close_pct=0.5, move_sl_to="be")]
        mgr.set_tp(1, TPConfig(mode="partial", tiers=tiers), volume=0.10)

        # SELL: entry=2000, SL=2010, current=1988 → profit=12, 1R=10
        pos = make_pos(_type="SELL", entry=2000.0, sl=2010.0, current=1988.0, volume=0.10)
        actions = mgr.check_all([pos])

        assert len(actions) == 1
        assert len(mt5.partial_calls) == 1
        assert mt5.partial_calls[0][1] == pytest.approx(0.05, abs=0.001)


# ====================================================================
# Dynamic TP Tests
# ====================================================================

class TestDynamicTP:
    def test_dynamic_tp_expands(self):
        """Dynamic TP: TP ขยายตาม ATR."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)

        mgr.set_tp(1, TPConfig(mode="dynamic", atr_tp_multiplier=3.0), volume=0.10)

        # entry=2000, current TP=2030, ATR=15 → new TP = 2000 + 15×3 = 2045
        pos = make_pos(current=2010.0, tp=2030.0, volume=0.10)
        atr = {"XAUUSDc": 15.0}
        actions = mgr.check_all([pos], atr)

        assert len(actions) == 1
        assert actions[0]["new_tp"] == 2045.0

    def test_dynamic_tp_no_retract(self):
        """Dynamic TP: TP ห้ามถอย."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)

        mgr.set_tp(1, TPConfig(mode="dynamic", atr_tp_multiplier=2.0), volume=0.10)

        # current TP=2030, ATR×2 = 10×2 = 2020 < 2030 → no move
        pos = make_pos(current=2010.0, tp=2030.0, volume=0.10)
        atr = {"XAUUSDc": 10.0}
        actions = mgr.check_all([pos], atr)

        assert len(actions) == 0


# ====================================================================
# Trailing TP Tests
# ====================================================================

class TestTrailingTP:
    def test_trailing_tp_retracts(self):
        """Trailing TP: TP ถอยมาเมื่อราคาใกล้แล้วย้อน."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)

        mgr.set_tp(1, TPConfig(
            mode="trailing_tp", trailing_tp_atr_distance=0.5,
        ), volume=0.10)

        # ราคาใกล้ TP → track closest
        pos1 = make_pos(current=2028.0, tp=2030.0, volume=0.10)
        atr = {"XAUUSDc": 4.0}
        mgr.check_all([pos1], atr)

        # closest_to_tp = 2030-2028 = 2
        state = mgr._states[1]
        assert state.closest_to_tp == 2.0

        # ราคาย้อนลง → distance = 2030-2022 = 8 > 2 + 4×0.5 = 4
        pos2 = make_pos(current=2022.0, tp=2030.0, volume=0.10)
        actions = mgr.check_all([pos2], atr)

        # TP ถอยมา: current + buffer = 2022 + 4×0.5 = 2024
        assert len(actions) == 1
        assert actions[0]["action"] == "trailing_tp_retracted"
        assert actions[0]["new_tp"] == 2024.0


# ====================================================================
# Edge Cases
# ====================================================================

class TestTPEdgeCases:
    def test_off_mode_skip(self):
        """Skip when mode is off."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)
        mgr.set_tp(1, TPConfig(mode="off"), volume=0.10)
        pos = make_pos()
        actions = mgr.check_all([pos])
        assert len(actions) == 0

    def test_no_sl_skip(self):
        """Skip positions without SL."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)
        mgr.set_tp(1, TPConfig(mode="partial"), volume=0.10)
        pos = make_pos(sl=0.0)
        actions = mgr.check_all([pos])
        assert len(actions) == 0

    def test_set_and_remove(self):
        """Set + remove TP config."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)
        mgr.set_tp(1, TPConfig(mode="partial"), volume=0.10)
        assert mgr.get_status(1)["active"] is True
        mgr.remove_tp(1)
        assert mgr.get_status(1)["active"] is False

    def test_reset_clears_all(self):
        """Reset clears everything."""
        mt5 = MockMT5()
        mgr = TakeProfitManager(mt5)
        mgr.set_tp(1, TPConfig(mode="partial"), volume=0.10)
        mgr.set_tp(2, TPConfig(mode="dynamic"), volume=0.20)
        mgr.reset()
        assert mgr.get_status(1)["active"] is False
        assert mgr.get_status(2)["active"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
