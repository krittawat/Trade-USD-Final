import asyncio

import numpy as np
import pandas as pd

from app.brain.practice_engine import PracticeEngine
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.invoker import analyze_with_fallback, normalize_strategy_params


class _StrictStrategy:
    """Does not accept arbitrary kwargs."""

    def analyze(self, candles, profile, regime=RegimeType.UNKNOWN):
        return Decision(
            symbol=profile.symbol,
            action=Action.HOLD,
            confidence=0.0,
            reason="strict",
            strategy_name="strict",
            timeframe="M5",
        )


class _ParamAwareStrategy:
    """Accepts kwargs used by training/evolution."""

    name = "param_aware"
    timeframe = "M5"

    def analyze(
        self,
        candles,
        profile,
        regime=RegimeType.UNKNOWN,
        rr_target: float = 2.0,
        **kwargs,
    ):
        entry = float(candles["close"].iloc[-1])
        return Decision(
            symbol=profile.symbol,
            action=Action.BUY,
            confidence=0.8,
            reason="param_test",
            stop_loss=entry - 1.0,
            take_profit=entry + float(rr_target),
            strategy_name=self.name,
            timeframe=self.timeframe,
        )


class _Factory:
    def __init__(self, strategies: dict):
        self._strategies = strategies


def _make_candles(n: int = 90) -> pd.DataFrame:
    close = np.linspace(100.0, 109.0, n)
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=n, freq="5min"),
            "open": close - 0.05,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(n, 100.0),
        }
    )


def test_normalize_strategy_params_maps_and_coerces():
    raw = {
        "atr_multiplier": "2.5",
        "rr_ratio": 3,
        "use_volume_filter": "true",
        "unknown_key": 99,
    }
    normalized = normalize_strategy_params(raw)

    assert normalized["sl_atr_mult"] == 2.5
    assert normalized["rr_target"] == 3.0
    assert normalized["use_volume_filter"] is True
    assert "unknown_key" not in normalized


def test_analyze_with_fallback_ignores_unsupported_kwargs():
    strategy = _StrictStrategy()
    profile = SymbolProfile(symbol="TEST")
    candles = _make_candles(60)

    decision = analyze_with_fallback(
        strategy=strategy,
        candles=candles,
        profile=profile,
        regime=RegimeType.UNKNOWN,
        extra_kwargs={"session": "LONDON", "rr_target": 2.0},
    )

    assert decision.action == Action.HOLD
    assert decision.reason == "strict"


def test_practice_engine_uses_normalized_params_in_analyze():
    strategy = _ParamAwareStrategy()
    engine = PracticeEngine(factory=_Factory({"param_aware": strategy}))
    profile = SymbolProfile(symbol="TEST")
    candles = _make_candles()

    low_rr = asyncio.run(
        engine.run_practice(
            symbol="TEST",
            strategy_name="param_aware",
            candles=candles,
            profile=profile,
            params={"rr_ratio": 1.0},
        )
    )
    high_rr = asyncio.run(
        engine.run_practice(
            symbol="TEST",
            strategy_name="param_aware",
            candles=candles,
            profile=profile,
            params={"rr_ratio": 4.0},
        )
    )

    assert low_rr.params["rr_target"] == 1.0
    assert high_rr.params["rr_target"] == 4.0
    assert low_rr.score > high_rr.score
