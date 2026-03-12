import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.trader.engine.config import EngineConfig
from backend.trader.engine.metrics import PerformanceMetricsService
from backend.trader.engine.models import (
    MarketSnapshot,
    OpenExposure,
    PerformanceSnapshot,
    PortfolioSnapshot,
    StrategySignal,
)
from backend.trader.engine.risk import RiskManager
from backend.trader.engine.service import ProfessionalTradingEngine


class DummyDb:
    def __init__(self, live=None, shadow=None):
        self.live = live or {}
        self.shadow = shadow or {}

    def get_closed_trade_model_performance(self, **kwargs):
        _ = kwargs
        return dict(self.live)

    def get_shadow_model_performance(self, **kwargs):
        _ = kwargs
        return dict(self.shadow)


class DummyMt5:
    def __init__(self, positions=None):
        self._positions = positions or []

    def positions_get(self):
        return list(self._positions)

    def symbol_info(self, _symbol):
        return None


class DummyExecutor:
    def __init__(self):
        self.calls = []

    def place_order(self, signal, lot_size=0.01):
        self.calls.append((signal, lot_size))
        return {
            "status": "ok",
            "trade": {
                "ticket": 12345,
                "entry_price": signal["entry_price"],
            },
        }


def make_config() -> EngineConfig:
    return EngineConfig(
        base_risk_pct=1.0,
        max_open_risk_pct=2.0,
        max_daily_loss_currency=50.0,
        max_drawdown_pct=10.0,
        max_spread_points={"XAUUSD": 1000.0},
        max_slippage_points={"XAUUSD": 100.0},
        base_stop_atr_multiplier=1.5,
        min_stop_atr_multiplier={"XAUUSD": 1.0},
        max_stop_atr_multiplier={"XAUUSD": 2.0},
        rr_levels=(2.0, 3.0, 4.0),
        min_rr=1.5,
        min_expectancy_r=0.10,
        min_live_trades=6,
        min_shadow_trades=6,
        performance_lookback_days=90,
        correlation_groups={"METALS": {"XAUUSD", "XAGUSD"}},
        same_direction_correlation_limit=1,
        max_group_risk_pct=1.5,
        session_allowlist={"XAUUSD": {"LONDON", "NY"}},
    )


def make_signal(confidence=0.75) -> StrategySignal:
    return StrategySignal(
        symbol="XAUUSDm",
        standard_symbol="XAUUSD",
        timeframe="M15",
        side="BUY",
        entry_price=2000.0,
        confidence=confidence,
        model="ALPHA_V6_INSTITUTIONAL",
        raw={"entry_price": 2000.0, "side": "BUY", "model": "ALPHA_V6_INSTITUTIONAL"},
    )


def make_market() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="XAUUSDm",
        standard_symbol="XAUUSD",
        timeframe="M15",
        session="LONDON",
        bid=2000.0,
        ask=2000.5,
        spread_points=80.0,
        point_size=0.1,
        tick_value=0.01,
        tick_size=0.1,
        volume_min=0.1,
        volume_max=10.0,
        volume_step=0.1,
        atr=10.0,
        atr_deviation=1.1,
    )


def make_portfolio(exposures=None, open_risk=0.0, daily_pnl=0.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        balance=1000.0,
        equity=1000.0,
        daily_pnl=daily_pnl,
        floating_pnl=0.0,
        combined_pnl=daily_pnl,
        margin=0.0,
        margin_free=1000.0,
        peak_equity=1000.0,
        drawdown_pct=0.0,
        open_risk_usd=open_risk,
        exposures=exposures or [],
    )


def test_build_order_plan_uses_fixed_fractional_risk_and_atr_targets():
    manager = RiskManager(make_config())
    plan, reason = manager.build_order_plan(make_signal(), make_market(), make_portfolio())

    assert reason == ""
    assert plan is not None
    assert plan.stop_distance == pytest.approx(15.0)
    assert plan.take_profit == pytest.approx(2030.5)
    assert plan.lot_size == pytest.approx(6.6, abs=0.01)
    assert plan.risk_usd == pytest.approx(9.9, abs=0.2)


def test_assess_plan_blocks_negative_expectancy():
    manager = RiskManager(make_config())
    signal = make_signal(confidence=0.55)
    market = make_market()
    portfolio = make_portfolio()
    plan, reason = manager.build_order_plan(signal, market, portfolio)

    assert reason == ""
    reasons = manager.assess_plan(
        signal,
        market,
        portfolio,
        plan,
        PerformanceSnapshot(combined_expectancy_r=-0.25),
    )

    assert any("expectancy_guard" in item for item in reasons)


def test_assess_plan_blocks_correlated_exposure():
    manager = RiskManager(make_config())
    signal = make_signal()
    market = make_market()
    portfolio = make_portfolio(
        exposures=[
            OpenExposure(
                symbol="XAGUSDm",
                standard_symbol="XAGUSD",
                group="METALS",
                side="BUY",
                volume=1.0,
                risk_usd=3.0,
            )
        ]
    )
    plan, reason = manager.build_order_plan(signal, market, portfolio)

    assert reason == ""
    reasons = manager.assess_plan(
        signal,
        market,
        portfolio,
        plan,
        PerformanceSnapshot(combined_expectancy_r=0.5),
    )

    assert any("correlation_guard" in item for item in reasons)


def test_professional_engine_simulates_and_journals_trade(tmp_path):
    engine = ProfessionalTradingEngine(
        db_store=DummyDb(),
        journal_db_path=tmp_path / "engine.db",
    )
    engine.config = make_config()
    engine.portfolio.config = engine.config
    engine.risk.config = engine.config
    engine.metrics = PerformanceMetricsService(DummyDb(), engine.config)
    engine.observe_account_state({"equity": 1000.0, "balance": 1000.0})

    executor = DummyExecutor()
    result = engine.process_signal(
        raw_signal={
            "entry_price": 2000.0,
            "side": "BUY",
            "model": "ALPHA_V6_INSTITUTIONAL",
            "confidence": 0.78,
            "rationale": ["trend", "liquidity"],
        },
        symbol="XAUUSDm",
        timeframe="M15",
        session="LONDON",
        market_state={
            "bid": 2000.0,
            "ask": 2000.5,
            "spread": 80.0,
            "point": 0.1,
            "tick_value": 0.01,
            "tick_size": 0.1,
            "volume_min": 0.1,
            "volume_max": 10.0,
            "volume_step": 0.1,
            "atr_deviation": 1.1,
        },
        account_state={
            "balance": 1000.0,
            "equity": 1000.0,
            "daily_pnl": 0.0,
            "margin": 0.0,
            "margin_free": 1000.0,
        },
        executor=executor,
        mode="dry_run",
        mt5_module=DummyMt5(),
        atr=10.0,
    )

    assert result.status == "simulated"
    assert executor.calls
    assert result.plan is not None

    conn = sqlite3.connect(tmp_path / "engine.db")
    rows = conn.execute(
        "SELECT status, symbol, model FROM trade_engine_journal ORDER BY id DESC"
    ).fetchall()
    assert rows[0] == ("SIMULATED", "XAUUSDm", "ALPHA_V6_INSTITUTIONAL")
