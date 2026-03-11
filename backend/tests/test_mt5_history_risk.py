import math
import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from backend.trader.risk.mt5_history import (
    combined_realized_and_floating_pnl,
    currency_loss_limit_to_dd_pct,
    is_currency_loss_breached,
    quantize_currency,
    remaining_additional_loss_budget,
    summarize_closed_trade_deals,
)


def _deal(**overrides):
    base = {
        "entry": 1,
        "type": 0,
        "profit": 0.0,
        "commission": 0.0,
        "swap": 0.0,
        "fee": 0.0,
        "time": 0,
        "time_msc": 0,
        "ticket": 0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_summarize_closed_trade_deals_uses_net_realized_pnl_and_filters_entries():
    deals = [
        _deal(entry=0, type=0, profit=999.0, time=0, ticket=10),
        _deal(type=1, profit=10.0, commission=-1.0, swap=-0.5, fee=-0.25, time=1, ticket=20),
        _deal(type=0, profit=-3.0, commission=-0.5, time=2, ticket=30),
    ]

    summary = summarize_closed_trade_deals(deals)

    assert summary["closed_deal_count"] == 2
    assert summary["realized_pnl"] == 4.75
    assert summary["consecutive_losses"] == 1


def test_remaining_additional_loss_budget_tracks_profit_and_loss_cushion():
    assert remaining_additional_loss_budget(-4.15, 12.0) == 7.85
    assert remaining_additional_loss_budget(13.0, 12.0) == 25.0
    assert remaining_additional_loss_budget(-20.01, 12.0) == 0.0
    assert math.isinf(remaining_additional_loss_budget(0.0, 0.0))
    assert remaining_additional_loss_budget(-4.149999999, 12.0) == 7.85


def test_combined_realized_and_floating_pnl_matches_projected_session_loss():
    assert combined_realized_and_floating_pnl(0.0, -11.9) == -11.9
    assert combined_realized_and_floating_pnl(-4.0, -8.5) == -12.5
    assert combined_realized_and_floating_pnl(-8.71, -3.29) == -12.0


def test_currency_quantization_and_breach_checks_use_cent_precision():
    assert quantize_currency(7.849999999) == 7.85
    assert is_currency_loss_breached(-11.994, 12.0) is False
    assert is_currency_loss_breached(-12.0, 12.0) is True


def test_currency_loss_limit_to_dd_pct_clamps_panic_exit_to_cash_limit():
    assert round(currency_loss_limit_to_dd_pct(12.0, 182.27, 15.0), 2) == 6.58
    assert currency_loss_limit_to_dd_pct(0.0, 182.27, 15.0) == 15.0
