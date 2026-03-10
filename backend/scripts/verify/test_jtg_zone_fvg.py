"""
Test: JTG Zone FVG Strategy — Unit Tests.

Verifies:
    1. Strategy instantiation + attributes
    2. analyze() returns Decision with correct structure
    3. HOLD on insufficient data
    4. HOLD on ranging/low-vol regime
    5. FVG fill-depth calculation
    6. SL always present when BUY/SELL
"""

import numpy as np
import pandas as pd
import pytest

from app.domain.enums import Action, RegimeType
from app.domain.models import SymbolProfile
from app.strategy.templates.jtg_zone_fvg import JTGZoneFVGStrategy


@pytest.fixture
def strategy():
    return JTGZoneFVGStrategy()


@pytest.fixture
def profile():
    return SymbolProfile(
        symbol="XAUUSDc",
        digits=2,
        point=0.01,
        contract_size=100.0,
        spread_avg=20.0,
        spread_max=50.0,
        max_positions=2,
    )


def _make_candles(
    n=250,
    base_price=2000.0,
    trend="up",
    seed=42,
    add_fvg=False,
):
    """Generate synthetic M15 candle data for testing."""
    rng = np.random.RandomState(seed)
    prices = [base_price]
    trend_drift = 0.5 if trend == "up" else (-0.5 if trend == "down" else 0.0)

    for _ in range(n - 1):
        change = trend_drift + rng.randn() * 2.0
        prices.append(prices[-1] + change)

    prices = np.array(prices)
    noise = rng.rand(n) * 3.0

    high = prices + noise + 1.5
    low = prices - noise - 1.5
    open_ = prices + (rng.rand(n) - 0.5) * 2.0
    close = prices

    # Ensure OHLC consistency
    high = np.maximum(high, np.maximum(open_, close))
    low = np.minimum(low, np.minimum(open_, close))

    volume = rng.randint(100, 2000, size=n).astype(float)

    # Create London/NY session times
    start_time = pd.Timestamp("2025-01-15 10:00:00")
    times = pd.date_range(start=start_time, periods=n, freq="15min")

    df = pd.DataFrame({
        "time": times,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": volume,
    })

    if add_fvg:
        # Inject a bullish FVG near the end
        idx = n - 5
        df.loc[idx, "high"] = base_price - 10.0
        df.loc[idx + 1, "close"] = base_price - 5.0
        df.loc[idx + 1, "high"] = base_price - 3.0
        df.loc[idx + 2, "low"] = base_price - 8.0  # gap: candle[i].high < candle[i+2].low

    return df


# ─── Test 1: Strategy instantiation ───
class TestInstantiation:
    def test_name(self, strategy):
        assert strategy.name == "jtg_zone_fvg"

    def test_timeframe(self, strategy):
        assert strategy.timeframe == "M15"

    def test_regimes(self, strategy):
        assert RegimeType.TRENDING_UP in strategy.suitable_regimes
        assert RegimeType.TRENDING_DOWN in strategy.suitable_regimes

    def test_has_pattern_detector(self, strategy):
        from app.brain.pattern_detector import PatternDetector
        assert isinstance(strategy.pattern_detector, PatternDetector)


# ─── Test 2: HOLD on insufficient data ───
class TestDataChecks:
    def test_hold_on_none(self, strategy, profile):
        result = strategy.analyze(None, profile)
        assert result.action == Action.HOLD
        assert "insufficient" in result.reason.lower() or "Data" in result.reason

    def test_hold_on_small_df(self, strategy, profile):
        df = _make_candles(n=50)
        result = strategy.analyze(df, profile)
        assert result.action == Action.HOLD

    def test_hold_on_ranging_regime(self, strategy, profile):
        # Use London session time so session filter doesn't fire first
        df = _make_candles(n=250)
        df["time"] = pd.date_range(start="2025-01-15 10:00:00", periods=len(df), freq="15min")
        result = strategy.analyze(df, profile, regime=RegimeType.RANGING)
        assert result.action == Action.HOLD
        assert "Regime" in result.reason or "ranging" in result.reason.lower()

    def test_hold_on_low_vol_regime(self, strategy, profile):
        df = _make_candles(n=250)
        result = strategy.analyze(df, profile, regime=RegimeType.LOW_VOLATILITY)
        assert result.action == Action.HOLD


# ─── Test 3: Decision structure ───
class TestDecisionStructure:
    def test_returns_decision(self, strategy, profile):
        df = _make_candles(n=250, trend="up")
        from app.domain.models import Decision
        result = strategy.analyze(df, profile, regime=RegimeType.TRENDING_UP)
        assert isinstance(result, Decision)
        assert result.strategy_name == "jtg_zone_fvg"
        assert result.symbol == "XAUUSDc"

    def test_sl_always_set_on_entry(self, strategy, profile):
        """SL must always be present when action is BUY or SELL."""
        df = _make_candles(n=250, trend="up")
        result = strategy.analyze(df, profile, regime=RegimeType.TRENDING_UP)
        if result.action in (Action.BUY, Action.SELL):
            assert result.stop_loss is not None
            assert result.stop_loss > 0
            assert result.take_profit is not None
            assert result.take_profit > 0


# ─── Test 4: FVG fill depth calculation ───
class TestFVGFillDepth:
    def test_fvg_helper_returns_dict(self, strategy):
        df = _make_candles(n=250)
        result = strategy._find_fvg_with_depth(df, atr=5.0, current_price=2050.0)
        assert "bull_fvg" in result
        assert "bear_fvg" in result

    def test_fill_pct_in_range(self, strategy):
        df = _make_candles(n=250)
        result = strategy._find_fvg_with_depth(df, atr=5.0, current_price=2050.0)
        for key in ("bull_fvg", "bear_fvg"):
            if result[key]:
                assert 0.0 <= result[key]["fill_pct"] <= 1.0


# ─── Test 5: S/R zones ───
class TestSRZones:
    def test_sr_zones_structure(self, strategy):
        df = _make_candles(n=250)
        zones = strategy._find_sr_zones(df, lookback=30, atr=5.0)
        assert "swing_high" in zones
        assert "swing_low" in zones
        assert "support_zone" in zones
        assert "resistance_zone" in zones
        assert zones["swing_high"] > zones["swing_low"]
        assert len(zones["support_zone"]) == 2
        assert len(zones["resistance_zone"]) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
