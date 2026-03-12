import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.enums import Action
from app.domain.models import Decision, SymbolProfile
from backend.trader.brain.quality_filter import quality_filter
from backend.trader.scripts.run_backtest import STRATEGY_PRESETS
from backend.trader.strategy import alpha_v7_ict_live
from backend.trader.strategy import selector


def _make_df(n: int = 260) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    base = 2000.0 + np.cumsum(rng.normal(0.15, 0.45, n))
    open_ = base + rng.normal(0.0, 0.12, n)
    close = base + rng.normal(0.0, 0.12, n)
    high = np.maximum(open_, close) + rng.uniform(0.05, 0.35, n)
    low = np.minimum(open_, close) - rng.uniform(0.05, 0.35, n)
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-03-10 08:00:00", periods=n, freq="5min", tz="UTC"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": rng.integers(100, 2000, n),
        }
    )


def test_alpha_v7_param_resolver_applies_symbol_and_timeframe_overrides():
    params = alpha_v7_ict_live._resolve_strategy_params(
        "BTCUSDm",
        "M5",
        {"symbol": "BTCUSDm", "timeframe": "M5", "session": "LONDON"},
    )

    assert params["min_rr"] >= 2.1
    assert params["min_adx"] >= 19.0
    assert params["min_displacement_atr"] >= 0.70
    assert params["max_reentry_distance_atr"] >= 0.60
    assert params["allowed_timeframes"] == ["M5", "M15", "H1"]


def test_alpha_v7_live_wrapper_formats_trade_payload(monkeypatch):
    def fake_analyze(self, df, profile: SymbolProfile, regime=None, **kwargs):
        _ = (self, df, profile, regime, kwargs)
        entry = float(df["close"].iloc[-1])
        return Decision(
            symbol=profile.symbol,
            action=Action.BUY,
            confidence=0.83,
            reason="ICT BUY: test retest",
            stop_loss=entry - 2.0,
            take_profit=entry + 4.0,
            strategy_name="alpha_v7_ict",
            tags=["ICT", "FVG"],
        )

    monkeypatch.setattr(alpha_v7_ict_live.AlphaV7ICTStrategy, "analyze", fake_analyze)

    signal = alpha_v7_ict_live.signal_alpha_v7_ict(
        _make_df(),
        {"symbol": "XAUUSD", "timeframe": "M5", "session": "LONDON"},
    )

    assert signal is not None
    assert signal["model"] == "ALPHA_V7_ICT"
    assert signal["side"] == "BUY"
    assert signal["tp3"] > signal["tp2"] > signal["tp1"] > signal["entry_price"]
    assert "ICT BUY: test retest" in signal["rationale"][0]


def test_alpha_v7_backtest_preset_registered():
    preset = STRATEGY_PRESETS["alpha_v7_ict"]
    assert preset["whitelist"] == ["ALPHA_V7_ICT"]
    assert preset["force_enabled_models"] == ["ALPHA_V7_ICT"]
    assert preset["min_confidence"] >= 0.70


def test_selector_bypasses_antichop_for_alpha_v7_focus(monkeypatch):
    df = _make_df(260)
    df["atr"] = 8.0
    df["body_ratio"] = 0.62
    df["vol_ratio"] = 1.6
    df["plus_di"] = 25.0
    df["minus_di"] = 15.0
    df["obv_bullish"] = True
    df["vol_dryup"] = False
    df["displacement_up"] = True
    df["displacement_down"] = False
    df["structure"] = "HH"

    signal = {
        "symbol": "XAUUSD",
        "side": "BUY",
        "entry_price": float(df["close"].iloc[-1]),
        "sl": float(df["close"].iloc[-1] - 5.0),
        "tp1": float(df["close"].iloc[-1] + 10.0),
        "confidence": 0.88,
        "model": "ALPHA_V7_ICT",
        "rationale": ["ICT BUY", "FVG"],
    }

    monkeypatch.setattr(selector, "is_market_choppy", lambda frame: (True, "forced chop"))
    monkeypatch.setattr(selector, "_resolve_asset_profile", lambda symbol: {"cooldown_bars": 1, "min_rr": 1.1, "min_confidence": 0.6})
    monkeypatch.setattr(selector, "signal_alpha_v7_ict", lambda frame, context: dict(signal))
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
            metrics={"vol_ratio": 1.6, "directional_side": "BUY"},
        ),
    )

    base_context = {
        "symbol": "XAUUSD",
        "timeframe": "M5",
        "regime_result": {"regime": "Weak Trend (Up)"},
        "htf_ema_align": "BULLISH",
        "backtest_mode": True,
        "current_time": pd.Timestamp("2026-03-10 10:00:00", tz="UTC"),
    }

    blocked = selector.select_and_generate_signal(df, dict(base_context), events=[], current_bar=100)
    allowed = selector.select_and_generate_signal(
        df,
        {
            **base_context,
            "strategy_profile": "alpha_v7_ict",
            "force_enabled_models": ["ALPHA_V7_ICT"],
            "strategy_whitelist": ["ALPHA_V7_ICT"],
            "strategy_eval_mode": True,
            "strategy_eval_min_confidence": 0.6,
        },
        events=[],
        current_bar=101,
    )

    assert blocked is None
    assert allowed is not None
    assert allowed["model"] == "ALPHA_V7_ICT"
