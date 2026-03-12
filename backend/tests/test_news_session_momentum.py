import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.trader.brain.quality_filter import quality_filter
from backend.trader.engine.config import EngineConfig
from backend.trader.engine.models import MarketSnapshot, PerformanceSnapshot, PortfolioSnapshot, StrategySignal
from backend.trader.engine.risk import RiskManager
from backend.trader.services.news_filter import NewsFilter
from backend.trader.strategy import selector
from backend.trader.strategy.news_session_momentum import signal_news_session_momentum


def _make_news_df() -> pd.DataFrame:
    times = pd.date_range("2026-03-10 11:55:00", periods=20, freq="5min", tz="UTC")
    rows = []
    for idx, ts in enumerate(times):
        if ts < pd.Timestamp("2026-03-10 13:30:00", tz="UTC"):
            open_price = 100.00 + ((idx % 3) * 0.01)
            close_price = 100.02 + ((idx % 2) * 0.01)
            high = 100.20
            low = 99.80
            tick_volume = 100.0
            buy_pressure = 0.56
            sell_pressure = 0.44
            body_ratio = 0.34
            upper_wick_ratio = 0.18
            lower_wick_ratio = 0.20
        else:
            open_price = 100.12
            close_price = 100.46
            high = 100.50
            low = 100.05
            tick_volume = 240.0
            buy_pressure = 0.86
            sell_pressure = 0.14
            body_ratio = 0.78
            upper_wick_ratio = 0.12
            lower_wick_ratio = 0.08

        rows.append(
            {
                "time": ts,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "tick_volume": tick_volume,
                "atr": 0.40,
                "body_ratio": body_ratio,
                "upper_wick_ratio": upper_wick_ratio,
                "lower_wick_ratio": lower_wick_ratio,
                "buy_pressure": buy_pressure,
                "sell_pressure": sell_pressure,
            }
        )
    return pd.DataFrame(rows)


def _news_context() -> dict:
    event_time = datetime(2026, 3, 10, 13, 30, tzinfo=timezone.utc)
    return {
        "general_block_active": True,
        "trade_window_active": True,
        "minutes_to_event": -5.0,
        "minutes_since_event": 5.0,
        "active_event": {
            "title": "US CPI y/y",
            "currency": "USD",
            "impact": "HIGH",
            "time": event_time,
        },
    }


def test_news_filter_context_marks_general_and_trade_window():
    news_filter = NewsFilter()
    news_filter.set_events(
        [
            {
                "title": "US CPI y/y",
                "currency": "USD",
                "impact": "HIGH",
                "time": "2026-03-10T13:30:00+00:00",
            }
        ]
    )

    context = news_filter.get_event_context(
        "XAUUSDm",
        now=datetime(2026, 3, 10, 13, 35, tzinfo=timezone.utc),
        trade_minutes_after=20,
    )

    assert context["general_block_active"] is True
    assert context["trade_window_active"] is True
    assert context["active_event"]["currency"] == "USD"
    assert news_filter.is_safe("XAUUSDm", now=datetime(2026, 3, 10, 13, 35, tzinfo=timezone.utc)) is False


def test_news_session_momentum_generates_buy_signal():
    df = _make_news_df()

    signal = signal_news_session_momentum(
        df,
        {
            "symbol": "XAUUSD",
            "timeframe": "M5",
            "current_time": datetime(2026, 3, 10, 13, 35, tzinfo=timezone.utc),
            "news_context": _news_context(),
            "market_state": {"spread": 120.0},
        },
    )

    assert signal is not None
    assert signal["model"] == "NEWS_SESSION_MOMENTUM"
    assert signal["side"] == "BUY"
    assert signal["sl"] < signal["entry_price"] < signal["tp1"]
    assert signal["risk_profile"]["use_signal_levels"] is True
    assert signal["execution_guard"]["max_spread_points"] >= 160.0


def test_selector_prioritizes_news_session_model_during_news_window(monkeypatch):
    df = _make_news_df().copy()
    df["vol_ratio"] = 1.6
    df["plus_di"] = 25.0
    df["minus_di"] = 12.0
    df["obv_bullish"] = True
    df["vol_dryup"] = False
    df["displacement_up"] = True
    df["displacement_down"] = False
    df["structure"] = "HH"

    monkeypatch.setattr(selector, "_resolve_asset_profile", lambda symbol: {"cooldown_bars": 1, "min_rr": 1.1, "min_confidence": 0.6})
    monkeypatch.setattr(selector, "is_market_choppy", lambda frame: (False, ""))
    monkeypatch.setattr(
        selector,
        "_is_model_enabled",
        lambda context, model_name, enabled_by_config: str(model_name).upper() in {"NEWS_SESSION_MOMENTUM", "ALPHA_V7_ICT"},
    )
    monkeypatch.setattr(
        selector,
        "signal_news_session_momentum",
        lambda frame, context: {
            "symbol": "XAUUSD",
            "side": "BUY",
            "entry_price": 100.90,
            "sl": 100.10,
            "tp1": 102.20,
            "confidence": 0.74,
            "model": "NEWS_SESSION_MOMENTUM",
            "rationale": ["news breakout"],
        },
    )
    monkeypatch.setattr(
        selector,
        "signal_alpha_v7_ict",
        lambda frame, context: {
            "symbol": "XAUUSD",
            "side": "BUY",
            "entry_price": 100.90,
            "sl": 100.30,
            "tp1": 102.10,
            "confidence": 0.91,
            "model": "ALPHA_V7_ICT",
            "rationale": ["other signal"],
        },
    )
    monkeypatch.setattr(selector, "analyze_patterns", lambda frame: {"bullish_qml": False, "bearish_qml": False})
    monkeypatch.setattr(selector, "calculate_trade_probability", lambda frame, sig, pattern_state: 80.0)
    monkeypatch.setattr(selector, "apply_professional_guard", lambda signals, context: signals)
    monkeypatch.setattr(quality_filter, "is_quality_signal", lambda sig, context: True)
    monkeypatch.setattr(
        selector,
        "evaluate_tick_volume",
        lambda frame, sig, context=None: SimpleNamespace(
            allowed=True,
            confidence_delta=0.0,
            state="PASS",
            reason="",
            metrics={"vol_ratio": 1.8, "directional_side": "BUY"},
        ),
    )

    signal = selector.select_and_generate_signal(
        df,
        {
            "symbol": "XAUUSD",
            "timeframe": "M5",
            "regime_result": {"regime": "Strong Trend (Up)"},
            "htf_ema_align": "BULLISH",
            "backtest_mode": True,
            "current_time": datetime(2026, 3, 10, 13, 35, tzinfo=timezone.utc),
            "news_context": _news_context(),
            "strategy_whitelist": ["NEWS_SESSION_MOMENTUM", "ALPHA_V7_ICT"],
            "force_enabled_models": ["NEWS_SESSION_MOMENTUM", "ALPHA_V7_ICT"],
            "strategy_eval_mode": True,
            "strategy_eval_min_confidence": 0.6,
        },
        events=[],
        current_bar=100,
    )

    assert signal is not None
    assert signal["model"] == "NEWS_SESSION_MOMENTUM"


def test_engine_risk_manager_honors_signal_levels_and_tighter_exec_guards():
    manager = RiskManager(
        EngineConfig(
            base_risk_pct=0.5,
            max_open_risk_pct=2.0,
            max_daily_loss_currency=50.0,
            max_drawdown_pct=15.0,
            max_spread_points={"XAUUSD": 300.0},
            max_slippage_points={"XAUUSD": 120.0},
            base_stop_atr_multiplier=1.4,
            min_stop_atr_multiplier={"XAUUSD": 1.0},
            max_stop_atr_multiplier={"XAUUSD": 2.0},
            rr_levels=(1.5, 2.0, 2.5),
            min_rr=1.2,
            min_expectancy_r=0.1,
            session_allowlist={"XAUUSD": {"LONDON", "NY"}},
        )
    )
    signal = StrategySignal(
        symbol="XAUUSDm",
        standard_symbol="XAUUSD",
        timeframe="M5",
        side="BUY",
        entry_price=2000.0,
        confidence=0.8,
        model="NEWS_SESSION_MOMENTUM",
        raw={
            "entry_price": 2000.0,
            "side": "BUY",
            "model": "NEWS_SESSION_MOMENTUM",
            "sl": 1995.0,
            "tp1": 2010.0,
            "tp2": 2015.0,
            "tp3": 2020.0,
            "engine_use_signal_levels": True,
            "risk_profile": {"use_signal_levels": True, "risk_pct": 0.25, "min_stop_atr": 0.9, "max_stop_atr": 2.0},
            "execution_guard": {"max_spread_points": 80.0, "max_slippage_points": 40.0},
        },
    )
    market = MarketSnapshot(
        symbol="XAUUSDm",
        standard_symbol="XAUUSD",
        timeframe="M5",
        session="LONDON",
        bid=2000.0,
        ask=2000.2,
        spread_points=70.0,
        point_size=0.1,
        tick_value=0.01,
        tick_size=0.1,
        volume_min=0.1,
        volume_max=10.0,
        volume_step=0.1,
        atr=4.0,
        atr_deviation=1.1,
    )
    portfolio = PortfolioSnapshot(
        balance=1000.0,
        equity=1000.0,
        daily_pnl=0.0,
        floating_pnl=0.0,
        combined_pnl=0.0,
        margin=0.0,
        margin_free=1000.0,
        peak_equity=1000.0,
        drawdown_pct=0.0,
        open_risk_usd=0.0,
        exposures=[],
    )

    plan, reason = manager.build_order_plan(signal, market, portfolio)

    assert reason == ""
    assert plan is not None
    assert plan.risk_pct == 0.25
    assert plan.stop_loss == 1995.2

    blocked = manager.assess_plan(
        signal,
        MarketSnapshot(
            symbol=market.symbol,
            standard_symbol=market.standard_symbol,
            timeframe=market.timeframe,
            session=market.session,
            bid=market.bid,
            ask=market.ask,
            spread_points=90.0,
            point_size=market.point_size,
            tick_value=market.tick_value,
            tick_size=market.tick_size,
            volume_min=market.volume_min,
            volume_max=market.volume_max,
            volume_step=market.volume_step,
            atr=market.atr,
            atr_deviation=market.atr_deviation,
        ),
        portfolio,
        plan,
        PerformanceSnapshot(combined_expectancy_r=0.4),
    )

    assert any("spread_filter" in reason for reason in blocked)
