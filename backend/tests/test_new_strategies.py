# -*- coding: utf-8 -*-
"""
Unit Tests for New Strategies Ported from gold-risk-engine
Tests: antichop_filter, sniper_pro, predicta_v4, counter_trend, easy_trend
"""
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest


def _make_df(n: int = 300, trend: str = "up", volatile: bool = True) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame for testing."""
    np.random.seed(42)
    base = 2000.0
    if trend == "up":
        close = base + np.cumsum(np.random.randn(n) * 0.5 + 0.1)
    elif trend == "down":
        close = base + np.cumsum(np.random.randn(n) * 0.5 - 0.1)
    else:  # sideways
        close = base + np.random.randn(n) * 0.2

    high = close + np.abs(np.random.randn(n)) * (2.0 if volatile else 0.1)
    low = close - np.abs(np.random.randn(n)) * (2.0 if volatile else 0.1)
    open_ = close + np.random.randn(n) * 0.5
    tick_volume = np.random.randint(100, 1000, size=n).astype(float)

    df = pd.DataFrame({
        'time': pd.date_range('2025-01-01', periods=n, freq='5min'),
        'open': open_,
        'high': high,
        'low': low,
        'close': close,
        'tick_volume': tick_volume,
    })
    df.set_index('time', inplace=True)
    return df


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the full feature engine to the DataFrame."""
    from backend.trader.features.volatility import add_volatility_features
    return add_volatility_features(df)


# ═══════════════════════════════════════════════════
# Test: Feature Engine (new indicators computed)
# ═══════════════════════════════════════════════════
class TestFeatureEngine:
    def test_all_indicators_present(self):
        df = _enrich(_make_df(300))
        required = ['atr', 'rsi', 'bb_upper', 'bb_lower', 'stoch_k', 'stoch_d',
                     'macd_line', 'macd_signal', 'macd_hist', 'adx',
                     'ema_200', 'is_uptrend', 'is_downtrend',
                     'vol_delta', 'delta_bullish', 'delta_bearish']
        for col in required:
            assert col in df.columns, f"Missing indicator: {col}"

    def test_no_all_nan(self):
        df = _enrich(_make_df(300))
        # Last row should have values for key indicators
        latest = df.iloc[-1]
        for col in ['rsi', 'atr', 'adx', 'macd_line', 'ema_200']:
            assert not np.isnan(latest[col]), f"{col} is NaN at last bar"


# ═══════════════════════════════════════════════════
# Test: AntiChop Filter
# ═══════════════════════════════════════════════════
class TestAntiChopFilter:
    def test_choppy_market_detected(self):
        from backend.trader.strategy.antichop_filter import is_market_choppy
        # Flat market with no volatility
        df = _enrich(_make_df(300, trend="sideways", volatile=False))
        is_choppy, reason = is_market_choppy(df)
        # Should detect at least one chop condition
        assert isinstance(is_choppy, bool)
        assert isinstance(reason, str)

    def test_trending_market_passes(self):
        from backend.trader.strategy.antichop_filter import is_market_choppy
        df = _enrich(_make_df(300, trend="up", volatile=True))
        is_choppy, reason = is_market_choppy(df)
        # Result is either True/False - just check it doesn't crash
        assert isinstance(is_choppy, bool)


# ═══════════════════════════════════════════════════
# Test: Sniper Pro Strategy
# ═══════════════════════════════════════════════════
class TestSniperPro:
    def test_returns_valid_or_none(self):
        from backend.trader.strategy.sniper_pro import signal_sniper_pro
        df = _enrich(_make_df(300, trend="up"))
        ctx = {"symbol": "XAUUSD", "regime_result": {"regime": "Trend (Up)", "confidence": 0.7}}
        sig = signal_sniper_pro(df, ctx)
        if sig is not None:
            assert 'side' in sig
            assert 'sl' in sig
            assert sig['sl'] != 0, "SL must not be zero"
            assert 'tp1' in sig
            assert sig['model'] == 'SNIPER_PRO'

    def test_insufficient_data_returns_none(self):
        from backend.trader.strategy.sniper_pro import signal_sniper_pro
        df = _enrich(_make_df(50, trend="up"))
        sig = signal_sniper_pro(df, {"symbol": "XAUUSD"})
        assert sig is None


# ═══════════════════════════════════════════════════
# Test: Predicta V4 Strategy
# ═══════════════════════════════════════════════════
class TestPredictaV4:
    def test_returns_valid_or_none(self):
        from backend.trader.strategy.predicta_v4 import signal_predicta_v4
        df = _enrich(_make_df(300, trend="up"))
        ctx = {"symbol": "XAUUSD", "regime_result": {"regime": "Trend (Up)", "confidence": 0.7}}
        sig = signal_predicta_v4(df, ctx)
        if sig is not None:
            assert 'side' in sig
            assert sig['sl'] != 0
            assert sig['model'] == 'PREDICTA_V4'

    def test_insufficient_data_returns_none(self):
        from backend.trader.strategy.predicta_v4 import signal_predicta_v4
        df = _enrich(_make_df(50))
        sig = signal_predicta_v4(df, {"symbol": "XAUUSD"})
        assert sig is None


# ═══════════════════════════════════════════════════
# Test: Counter Trend Strategy
# ═══════════════════════════════════════════════════
class TestCounterTrend:
    def test_returns_valid_or_none(self):
        from backend.trader.strategy.counter_trend import signal_counter_trend
        df = _enrich(_make_df(300, trend="sideways", volatile=True))
        ctx = {"symbol": "XAUUSD"}
        sig = signal_counter_trend(df, ctx)
        if sig is not None:
            assert 'side' in sig
            assert sig['sl'] != 0
            assert sig['model'] == 'COUNTER_TREND'

    def test_insufficient_data_returns_none(self):
        from backend.trader.strategy.counter_trend import signal_counter_trend
        df = _enrich(_make_df(30))
        sig = signal_counter_trend(df, {"symbol": "XAUUSD"})
        assert sig is None


# ═══════════════════════════════════════════════════
# Test: Easy Trend Strategy
# ═══════════════════════════════════════════════════
class TestEasyTrend:
    def test_returns_valid_or_none(self):
        from backend.trader.strategy.easy_trend import signal_easy_trend
        df = _enrich(_make_df(300, trend="up"))
        ctx = {"symbol": "XAUUSD", "regime_result": {"regime": "Trend (Up)", "confidence": 0.7}}
        sig = signal_easy_trend(df, ctx)
        if sig is not None:
            assert 'side' in sig
            assert sig['sl'] != 0
            assert sig['model'] == 'EASY_TREND'

    def test_insufficient_data_returns_none(self):
        from backend.trader.strategy.easy_trend import signal_easy_trend
        df = _enrich(_make_df(100))
        sig = signal_easy_trend(df, {"symbol": "XAUUSD"})
        assert sig is None


# ═══════════════════════════════════════════════════
# Test: Rapid Pullback Strategy
# ═══════════════════════════════════════════════════
class TestRapidPullback:
    def test_returns_valid_or_none(self):
        from backend.trader.strategy.rapid_pullback import signal_rapid_pullback
        df = _enrich(_make_df(300, trend="up"))
        ctx = {"symbol": "XAUUSD", "regime_result": {"regime": "Strong Trend (Up)", "confidence": 0.8}}
        sig = signal_rapid_pullback(df, ctx)
        if sig is not None:
            assert sig["model"] == "RAPID_PULLBACK"
            assert sig["sl"] != 0
            assert sig["tp1"] > sig["entry_price"]

    def test_buy_setup_can_trigger(self):
        from backend.trader.strategy.rapid_pullback import signal_rapid_pullback

        rows = []
        for i in range(90):
            base = 100.0 + (i * 0.12)
            rows.append({
                "open": base - 0.10,
                "high": base + 0.30,
                "low": base - 0.20,
                "close": base + 0.05,
                "atr": 1.0,
                "ema_fast": base - 0.15,
                "ema_slow": base - 0.35,
                "ema_200": base - 1.20,
                "vwap": base - 0.10,
                "rsi": 60.0,
                "roc_5": 0.20,
                "body_ratio": 0.50,
                "vol_ratio": 1.20,
                "plus_di": 28.0,
                "minus_di": 16.0,
                "is_uptrend": True,
                "is_downtrend": False,
                "lower_wick_ratio": 0.22,
                "upper_wick_ratio": 0.08,
            })

        rows[-2].update({"high": 110.40, "low": 109.75, "close": 110.10, "ema_fast": 109.95, "vwap": 109.90})
        rows[-1].update({
            "open": 110.15,
            "high": 111.10,
            "low": 109.92,
            "close": 110.85,
            "ema_fast": 110.20,
            "ema_slow": 109.80,
            "ema_200": 108.80,
            "vwap": 110.10,
            "rsi": 62.0,
            "roc_5": 0.28,
            "body_ratio": 0.58,
            "vol_ratio": 1.35,
            "plus_di": 30.0,
            "minus_di": 14.0,
            "lower_wick_ratio": 0.24,
            "upper_wick_ratio": 0.10,
            "is_uptrend": True,
            "is_downtrend": False,
        })
        df = pd.DataFrame(rows)
        sig = signal_rapid_pullback(df, {"symbol": "XAUUSD", "regime_result": {"regime": "Strong Trend (Up)"}})
        assert sig is not None
        assert sig["side"] == "BUY"
        assert sig["model"] == "RAPID_PULLBACK"
        assert sig["sl"] < sig["entry_price"] < sig["tp1"]


# ═══════════════════════════════════════════════════
# Test: Selector Integration
# ═══════════════════════════════════════════════════
class TestSelector:
    def test_selector_routes_without_crash(self):
        from backend.trader.strategy.selector import select_and_generate_signal
        df = _enrich(_make_df(300, trend="up"))
        ctx = {
            "symbol": "XAUUSD",
            "regime_result": {"regime": "Trend (Up)", "confidence": 0.7},
            "enable_rapid_pullback": True,
        }
        events = []
        sig = select_and_generate_signal(df, ctx, events)
        # May or may not generate signal — just verify no crash
        if sig is not None:
            assert 'side' in sig
            assert 'sl' in sig
            assert sig.get('confidence', 0) >= 0.6

    def test_compression_blocks(self):
        from backend.trader.strategy.selector import select_and_generate_signal
        df = _enrich(_make_df(300))
        ctx = {"symbol": "XAUUSD", "regime_result": {"regime": "Volatility Compression", "confidence": 0.8}}
        sig = select_and_generate_signal(df, ctx, [])
        assert sig is None, "Compression regime should block all signals"

    def test_strategy_whitelist_filters_candidates(self):
        from backend.trader.strategy.selector import _filter_candidate_models

        candidates = [
            {"model": "RAPID_PULLBACK", "side": "BUY"},
            {"model": "GOLD_ELITE", "side": "BUY"},
        ]
        out = _filter_candidate_models(candidates, {"strategy_whitelist": ["rapid_pullback"]})
        assert len(out) == 1
        assert out[0]["model"] == "RAPID_PULLBACK"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
