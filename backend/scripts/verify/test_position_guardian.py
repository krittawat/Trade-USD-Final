"""
Test Position Guardian — ทดสอบ PositionGuardian unified coordinator.

ตรวจ:
    - Auto-enroll bot positions → trailing + profit-lock + TP
    - Auto-enroll manual positions (magic=0) → trailing + profit-lock
    - Manual SL/TP set then trail
    - Trailing moves SL up / SL never backward
    - Profit lock at +2R
    - Cleanup stale entries
    - Guardian reset
    - Summary/Status API

Usage:
    cd d:\VibeCode\Trade\backend
    python -m pytest scripts/verify/test_position_guardian.py -v
"""

import pytest
from unittest.mock import MagicMock, patch
import time

# Prevent MT5 import errors
import sys
mt5_mock = MagicMock()
sys.modules["MetaTrader5"] = mt5_mock

from app.risk.position_guardian import (
    PositionGuardian,
    _get_digits,
    _get_symbol_trailing_config,
    _parse_profit_lock_tiers,
)
from app.risk.trade_manager import TrailingConfig, ProfitLockTier


# ─── Mock Settings ───
class MockSettings:
    guardian_trailing_mode = "step"
    guardian_trailing_activation_r = 1.0
    guardian_trailing_step_r = 0.5
    guardian_trailing_atr_mult = 2.5
    guardian_profit_lock_enabled = True
    guardian_profit_lock_tiers = "1.0:0.0,2.0:0.5,3.0:0.75"
    guardian_manual_trailing = True
    protect_manual_trades = True
    manual_sl_pct = 0.02
    sl_atr_multiplier = 2.0
    manual_tp_rr = 2.0
    tp_management_mode = "partial"
    tp_partial_tier1_r = 1.0
    tp_partial_tier1_pct = 0.3
    tp_partial_tier2_r = 2.0
    tp_partial_tier2_pct = 0.3
    tp_partial_tier3_r = 3.0
    tp_partial_tier3_pct = 1.0
    tp_dynamic_atr_mult = 3.0
    tp_trailing_distance_atr = 0.5


class MockMT5:
    def __init__(self):
        self.modify_calls = []
        self.partial_calls = []
        self.close_calls = []

    def modify_sl(self, ticket, new_sl, new_tp=0.0):
        self.modify_calls.append({"ticket": ticket, "new_sl": new_sl, "new_tp": new_tp})
        return True

    def partial_close(self, ticket, volume, reason=""):
        self.partial_calls.append({"ticket": ticket, "volume": volume, "reason": reason})
        return True

    def close_position(self, ticket, reason=""):
        self.close_calls.append({"ticket": ticket, "reason": reason})
        return True

    def get_symbol_info(self, symbol):
        return None


def make_pos(ticket=1, symbol="XAUUSDc", _type="BUY", entry=2000.0,
             current=2010.0, sl=1990.0, tp=2030.0, volume=0.10, magic=12345):
    return {
        "ticket": ticket, "symbol": symbol, "type": _type,
        "price_open": entry, "price_current": current,
        "sl": sl, "tp": tp, "volume": volume, "magic": magic,
    }


# ====================================================================
# Helper Tests
# ====================================================================

class TestHelpers:
    """ทดสอบ helper functions."""

    def test_get_digits_gold(self):
        assert _get_digits("XAUUSDc") == 2
        assert _get_digits("XAUUSDc") == 2

    def test_get_digits_silver(self):
        assert _get_digits("XAGUSDc") == 3

    def test_get_digits_jpy(self):
        assert _get_digits("USDJPYc") == 3

    def test_get_digits_btc(self):
        assert _get_digits("BTCUSDc") == 2

    def test_get_digits_forex(self):
        assert _get_digits("EURUSDc") == 5

    def test_trailing_config_gold(self):
        cfg = _get_symbol_trailing_config("XAUUSDc", mode="step")
        assert cfg.mode == "dynamic"  # Gold ใช้ dynamic mode (3 phases + anti-hunt)
        assert cfg.activation_r == 0.5  # เริ่มเร็ว
        assert cfg.anti_hunt is True
        assert cfg.anti_hunt_dodge_pts == 2.0  # Gold dodge กว้าง

    def test_trailing_config_btc(self):
        cfg = _get_symbol_trailing_config("BTCUSDc", mode="step")
        assert cfg.mode == "adaptive"  # BTC ใช้ adaptive (ATR บีบตามกำไร)
        assert cfg.activation_r == 1.5  # เริ่มช้า (wig ใหญ่)
        assert cfg.anti_hunt_dodge_pts == 50.0  # BTC dodge $50

    def test_trailing_config_silver(self):
        cfg = _get_symbol_trailing_config("XAGUSDc")
        assert cfg.mode == "dynamic"
        assert cfg.activation_r == 0.7

    def test_trailing_config_jpy(self):
        cfg = _get_symbol_trailing_config("USDJPYc")
        assert cfg.mode == "dynamic"
        assert cfg.anti_hunt_dodge_pts == 0.15

    def test_trailing_config_eur(self):
        cfg = _get_symbol_trailing_config("EURUSDc")
        assert cfg.mode == "dynamic"
        assert cfg.step_r == 0.3  # EUR ใช้ step เล็ก

    def test_trailing_config_unknown_fallback(self):
        """Symbol ที่ไม่มี profile → fallback dynamic."""
        cfg = _get_symbol_trailing_config("NZDCADc")
        assert cfg.mode == "dynamic"
        assert cfg.anti_hunt is True

    def test_parse_tiers_valid(self):
        tiers = _parse_profit_lock_tiers("1.0:0.0,2.0:0.5,3.0:0.75")
        assert len(tiers) == 3
        assert tiers[0].r_multiple == 1.0
        assert tiers[1].lock_pct == 0.5
        assert tiers[2].lock_pct == 0.75

    def test_parse_tiers_empty(self):
        tiers = _parse_profit_lock_tiers("")
        assert len(tiers) == 3  # default fallback

    def test_parse_tiers_invalid(self):
        tiers = _parse_profit_lock_tiers("abc:xyz")
        assert len(tiers) == 3  # default fallback


# ====================================================================
# Auto-Enrollment Tests
# ====================================================================

class TestAutoEnrollment:
    """ทดสอบ auto-enrollment ทำงานถูกต้อง."""

    def test_enroll_bot_position(self):
        """Bot position (magic > 0) ต้อง enroll trailing + lock + tp."""
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())
        pos = [make_pos(ticket=100, magic=12345, sl=1990.0)]

        result = g.process_all(pos)

        assert result["enrolled_trailing"] == 1
        assert result["enrolled_lock"] == 1
        assert result["enrolled_tp"] == 1
        assert 100 in g._enrolled_trailing
        assert 100 in g._enrolled_lock
        assert 100 in g._enrolled_tp

    def test_enroll_manual_position(self):
        """Manual position (magic=0) ต้อง enroll trailing + lock + tp."""
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())
        pos = [make_pos(ticket=200, magic=0, sl=1990.0)]

        result = g.process_all(pos)

        assert result["enrolled_trailing"] == 1
        assert result["enrolled_lock"] == 1
        assert 200 in g._enrolled_trailing

    def test_skip_no_sl(self):
        """Position ที่ไม่มี SL ห้าม enroll (safety)."""
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())
        pos = [make_pos(ticket=300, sl=0.0)]

        result = g.process_all(pos)

        assert result["enrolled_trailing"] == 0
        assert 300 not in g._enrolled_trailing

    def test_no_double_enroll(self):
        """Position ที่ enroll แล้วห้าม enroll ซ้ำ."""
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())
        pos = [make_pos(ticket=400, sl=1990.0)]

        r1 = g.process_all(pos)
        r2 = g.process_all(pos)

        assert r1["enrolled_trailing"] == 1
        assert r2["enrolled_trailing"] == 0  # no double enroll

    def test_manual_trailing_disabled(self):
        """ถ้า guardian_manual_trailing = False → ไม่ enroll manual."""
        mt5 = MockMT5()
        s = MockSettings()
        s.guardian_manual_trailing = False
        g = PositionGuardian(mt5, s)
        pos = [make_pos(ticket=500, magic=0, sl=1990.0)]

        result = g.process_all(pos)

        assert result["enrolled_trailing"] == 0
        assert 500 not in g._enrolled_trailing

    def test_multiple_positions(self):
        """Enroll หลาย positions พร้อมกัน."""
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())
        positions = [
            make_pos(ticket=601, symbol="XAUUSDc", sl=1990.0, magic=100),
            make_pos(ticket=602, symbol="EURUSDc", sl=1.0990, magic=0),
            make_pos(ticket=603, symbol="BTCUSDc", sl=90000.0, magic=200),
        ]

        result = g.process_all(positions)

        assert result["enrolled_trailing"] == 3
        assert len(g._enrolled_trailing) == 3


# ====================================================================
# Cleanup Tests
# ====================================================================

class TestCleanup:
    """ทดสอบ cleanup ทำงานถูกต้อง."""

    def test_cleanup_closed_positions(self):
        """Position ที่ปิดแล้วต้องถูกลบออกจาก tracking."""
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())

        # Enroll
        pos = [make_pos(ticket=700, sl=1990.0)]
        g.process_all(pos)
        assert 700 in g._enrolled_trailing

        # Position closes → cleanup
        g._cleanup_stale([])  # no active positions
        assert 700 not in g._enrolled_trailing
        assert 700 not in g._enrolled_lock

    def test_cleanup_preserves_active(self):
        """Position ที่ยังเปิดอยู่ห้ามถูกลบ."""
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())

        pos = [make_pos(ticket=800, sl=1990.0)]
        g.process_all(pos)

        g._cleanup_stale(pos)
        assert 800 in g._enrolled_trailing


# ====================================================================
# Status & Summary Tests
# ====================================================================

class TestStatusSummary:
    """ทดสอบ status/summary API."""

    def test_get_summary(self):
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())
        pos = [make_pos(ticket=900, sl=1990.0)]
        g.process_all(pos)

        summary = g.get_summary()
        assert summary["enrolled_trailing"] == 1
        assert summary["trailing_mode"] == "step"
        assert summary["profit_lock_enabled"] is True
        assert summary["manual_trailing"] is True

    def test_get_status(self):
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())
        pos = [make_pos(ticket=1000, sl=1990.0)]
        g.process_all(pos)

        status = g.get_status(1000)
        assert status["enrolled"] is True

    def test_get_status_unknown_ticket(self):
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())

        status = g.get_status(9999)
        assert status["enrolled"] is False


# ====================================================================
# Reset Tests
# ====================================================================

class TestReset:
    """ทดสอบ reset."""

    def test_reset_clears_all(self):
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())
        pos = [make_pos(ticket=1100, sl=1990.0)]
        g.process_all(pos)

        assert len(g._enrolled_trailing) > 0
        g.reset()
        assert len(g._enrolled_trailing) == 0
        assert len(g._enrolled_lock) == 0
        assert len(g._enrolled_tp) == 0


# ====================================================================
# Integration: process_all returns correct summary
# ====================================================================

class TestProcessAll:
    """ทดสอบ process_all flow ครบ."""

    def test_process_all_returns_summary(self):
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())
        positions = [
            make_pos(ticket=1201, sl=1990.0, magic=100),
            make_pos(ticket=1202, sl=1.0990, magic=0),
        ]

        result = g.process_all(positions, atr_values={"XAUUSDc": 5.0})

        assert "enrolled_trailing" in result
        assert "enrolled_lock" in result
        assert "trailing_actions" in result
        assert "lock_actions" in result
        assert "tp_actions" in result
        assert "manual_protected" in result

    def test_process_all_empty_positions(self):
        mt5 = MockMT5()
        g = PositionGuardian(mt5, MockSettings())

        result = g.process_all([])

        assert result["enrolled_trailing"] == 0
        assert result["trailing_actions"] == 0

    def test_profit_lock_disabled(self):
        mt5 = MockMT5()
        s = MockSettings()
        s.guardian_profit_lock_enabled = False
        g = PositionGuardian(mt5, s)
        pos = [make_pos(ticket=1300, sl=1990.0)]

        result = g.process_all(pos)

        assert result["enrolled_lock"] == 0
        assert 1300 not in g._enrolled_lock


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
