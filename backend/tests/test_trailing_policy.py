import sys
from datetime import datetime, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from backend.trader.execution.trailing_policy import build_trailing_config, compute_trailing_stop


def test_break_even_lock_moves_sl_above_entry():
    cfg = build_trailing_config(
        {
            "break_even_activation_r": 0.8,
            "break_even_buffer_r": 0.1,
            "atr_trail_activation_r": 9.0,
            "lock_tiers": [],
        }
    )

    result = compute_trailing_stop(
        entry=100.0,
        current_sl=95.0,
        current_price=104.5,
        side="BUY",
        atr=1.0,
        standard_symbol="XAUUSD",
        floating_dd_pct=0.0,
        cfg=cfg,
        now_utc=datetime(2026, 3, 11, 10, 0, tzinfo=timezone.utc),
    )

    assert result["new_sl"] == 100.5
    assert "Break-Even Lock" in result["reason"]


def test_profit_lock_tier_locks_sell_profit():
    cfg = build_trailing_config(
        {
            "break_even_activation_r": 9.0,
            "atr_trail_activation_r": 9.0,
            "lock_tiers": [
                {"trigger_r": 1.5, "lock_r_multiple": 0.75},
            ],
        }
    )

    result = compute_trailing_stop(
        entry=100.0,
        current_sl=104.0,
        current_price=94.0,
        side="SELL",
        atr=1.0,
        standard_symbol="XAUUSD",
        floating_dd_pct=0.0,
        cfg=cfg,
        now_utc=datetime(2026, 3, 11, 10, 0, tzinfo=timezone.utc),
    )

    assert result["new_sl"] == 97.0
    assert result["reason"] == "Profit Lock +1.50R"


def test_atr_trail_can_override_profit_lock_when_it_is_tighter():
    cfg = build_trailing_config(
        {
            "break_even_activation_r": 0.8,
            "break_even_buffer_r": 0.05,
            "atr_trail_activation_r": 1.4,
            "lock_tiers": [
                {"trigger_r": 2.0, "lock_r_multiple": 1.0},
            ],
        }
    )

    result = compute_trailing_stop(
        entry=100.0,
        current_sl=100.15,
        current_price=108.0,
        side="BUY",
        atr=1.0,
        standard_symbol="XAUUSD",
        floating_dd_pct=0.0,
        cfg=cfg,
        now_utc=datetime(2026, 3, 11, 10, 0, tzinfo=timezone.utc),
    )

    assert result["new_sl"] == 105.5
    assert result["reason"] == "ATR Trail (2.50x ATR)"
