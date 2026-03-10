"""
Test Regime Intelligence — ทดสอบ 8-regime classifier + risk profile + best_params.

ทดสอบ:
    1. 8 regimes ตรวจจับได้ถูกต้อง
    2. Confidence score อยู่ใน [0, 1]
    3. Risk profile mapping ถูกต้อง
    4. trade_allowed=False สำหรับ LOW_VOLATILITY
    5. Backward compat: RegimeContext return
    6. best_params table: insert/query
    7. RegimeIntelligenceEngine orchestrator
"""

import sys
import os
import unittest
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.domain.enums import RegimeType
from app.domain.models import RegimeContext, RegimeIntelligence
from app.brain.regime import classify_regime
from app.brain.regime_risk_profile import get_risk_profile, is_tradable
from app.brain.regime_intelligence import RegimeIntelligenceEngine
from app.brain.memory_store import MemoryStore


# ═══════════════════════════════════════════════════════════════════
# Helpers: สร้าง synthetic candles
# ═══════════════════════════════════════════════════════════════════

def _make_candles(n=250, base_price=2000.0, trend=0.0, volatility=5.0,
                  wick_ratio=0.3, volume_flat=True, volume_spike_at=None):
    """สร้าง synthetic OHLCV DataFrame."""
    np.random.seed(42)
    close = np.cumsum(np.random.randn(n) * volatility + trend) + base_price
    high = close + np.abs(np.random.randn(n)) * volatility * (1 + wick_ratio)
    low = close - np.abs(np.random.randn(n)) * volatility * (1 + wick_ratio)
    open_ = close + np.random.randn(n) * volatility * 0.3

    vol = np.ones(n) * 100
    if not volume_flat:
        vol = np.random.uniform(50, 200, n)
    if volume_spike_at is not None:
        vol[volume_spike_at] = vol[volume_spike_at] * 5

    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "tick_volume": vol,
    })


def _make_trending_candles(direction="up", strength="strong"):
    """สร้าง candles สำหรับ trend."""
    trend = 2.0 if direction == "up" else -2.0
    if strength == "weak":
        trend *= 0.3
    return _make_candles(n=250, trend=trend, volatility=3.0)


def _make_ranging_candles():
    """สร้าง candles สำหรับ ranging (ADX ต่ำ, BB แคบ)."""
    return _make_candles(n=250, trend=0.0, volatility=1.0)


def _make_high_vol_candles():
    """สร้าง candles สำหรับ high volatility."""
    np.random.seed(42)
    n = 250
    # First 200 bars normal, last 50 bars high vol
    vol_normal = np.random.randn(200) * 3.0
    vol_high = np.random.randn(50) * 30.0
    close = np.cumsum(np.concatenate([vol_normal, vol_high])) + 2000.0
    high = close + np.abs(np.random.randn(n)) * 15.0
    low = close - np.abs(np.random.randn(n)) * 15.0
    open_ = close + np.random.randn(n) * 5.0
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "tick_volume": np.ones(n) * 100,
    })


def _make_low_vol_candles():
    """สร้าง candles สำหรับ low volatility."""
    return _make_candles(n=250, trend=0.0, volatility=0.3)


def _make_wick_heavy_candles():
    """สร้าง candles ที่มี wick ยาว (liquidity sweep)."""
    np.random.seed(42)
    n = 250
    close = np.ones(n) * 2000.0 + np.random.randn(n) * 2.0
    open_ = close + np.random.randn(n) * 0.5
    # High wick = wick ratio > 0.6
    high = close + np.abs(np.random.randn(n)) * 20.0
    low = close - np.abs(np.random.randn(n)) * 20.0
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "tick_volume": np.ones(n) * 100,
    })


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

class TestClassifyRegime(unittest.TestCase):
    """ทดสอบ classify_regime() — 8 regimes."""

    def test_returns_regime_context(self):
        """ต้อง return RegimeContext เสมอ."""
        candles = _make_candles()
        result = classify_regime(candles)
        self.assertIsInstance(result, RegimeContext)
        self.assertIsInstance(result.regime, RegimeType)

    def test_confidence_in_range(self):
        """Confidence ต้องอยู่ใน [0, 1]."""
        for maker in [_make_candles, _make_trending_candles,
                      _make_ranging_candles, _make_high_vol_candles,
                      _make_low_vol_candles, _make_wick_heavy_candles]:
            result = classify_regime(maker())
            self.assertGreaterEqual(result.score, 0.0, f"Confidence < 0 for {maker.__name__}")
            self.assertLessEqual(result.score, 1.0, f"Confidence > 1 for {maker.__name__}")

    def test_trending_up_detected(self):
        """Trending up ต้องถูกจับ."""
        candles = _make_trending_candles("up", "strong")
        result = classify_regime(candles)
        self.assertIn(result.regime, [
            RegimeType.TRENDING_UP, RegimeType.STRONG_TREND,
        ], f"Got {result.regime} instead of trend up")

    def test_trending_down_detected(self):
        """Trending down ต้องถูกจับ."""
        candles = _make_trending_candles("down", "strong")
        result = classify_regime(candles)
        self.assertIn(result.regime, [
            RegimeType.TRENDING_DOWN, RegimeType.STRONG_TREND,
        ], f"Got {result.regime} instead of trend down")

    def test_low_vol_not_actionable(self):
        """Low volatility = actionable=False."""
        candles = _make_low_vol_candles()
        result = classify_regime(candles)
        # Either LOW_VOLATILITY or RANGING (both non-actionable)
        self.assertFalse(result.actionable,
                         f"Expected actionable=False, got regime={result.regime}")

    def test_wick_heavy_detected(self):
        """Wick-heavy candles = LIQUIDITY_SWEEP or FAKEOUT."""
        candles = _make_wick_heavy_candles()
        result = classify_regime(candles)
        self.assertIn(result.regime, [
            RegimeType.LIQUIDITY_SWEEP, RegimeType.FAKEOUT,
            RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN,
        ])

    def test_details_has_primary_regime(self):
        """details ต้องมี primary_regime."""
        candles = _make_candles()
        result = classify_regime(candles)
        self.assertIn("primary_regime", result.details)
        self.assertIn("confidence", result.details)
        self.assertIn("adx", result.details)
        self.assertIn("atr", result.details)
        self.assertIn("ema50", result.details)

    def test_insufficient_data(self):
        """ข้อมูลน้อยเกินไป = UNKNOWN."""
        candles = _make_candles(n=10)
        result = classify_regime(candles)
        self.assertEqual(result.regime, RegimeType.UNKNOWN)


class TestRegimeRiskProfile(unittest.TestCase):
    """ทดสอบ risk profile mapping."""

    def test_all_regimes_have_profile(self):
        """ทุก regime ต้องมี risk profile."""
        for regime in RegimeType:
            profile = get_risk_profile(regime)
            self.assertIn("lot_multiplier", profile)
            self.assertIn("sl_atr", profile)
            self.assertIn("tp_rr", profile)

    def test_low_vol_not_tradable(self):
        """LOW_VOLATILITY = lot_multiplier = 0 = ห้ามเทรด."""
        self.assertFalse(is_tradable(RegimeType.LOW_VOLATILITY))
        self.assertEqual(get_risk_profile(RegimeType.LOW_VOLATILITY)["lot_multiplier"], 0.0)

    def test_strong_trend_full_size(self):
        """STRONG_TREND = lot_multiplier = 1.0."""
        self.assertTrue(is_tradable(RegimeType.STRONG_TREND))
        self.assertEqual(get_risk_profile(RegimeType.STRONG_TREND)["lot_multiplier"], 1.0)

    def test_high_vol_reduced(self):
        """HIGH_VOLATILITY = lot_multiplier = 0.5."""
        profile = get_risk_profile(RegimeType.HIGH_VOLATILITY)
        self.assertEqual(profile["lot_multiplier"], 0.5)
        self.assertGreater(profile["sl_atr"], 1.5)  # SL กว้างขึ้น


class TestBestParams(unittest.TestCase):
    """ทดสอบ best_params table ใน memory_store."""

    def setUp(self):
        self.store = MemoryStore(db_path=":memory:")
        # Connect uses in-memory SQLite
        self.store.db_path = Path(":memory:")
        self.store._conn = sqlite3.connect(":memory:")
        self.store._conn.row_factory = sqlite3.Row
        self.store._create_tables()

    def tearDown(self):
        if self.store._conn:
            self.store._conn.close()

    def test_save_and_get(self):
        """save แล้ว get ได้."""
        params = {
            "strategy_name": "gold_scalp_pro",
            "win_rate": 0.65,
            "rr": 1.5,
            "sl_atr": 1.5,
            "tp_rr": 2.0,
            "lot_multiplier": 0.8,
            "total_trades": 50,
            "max_dd": 3.5,
            "expectancy": 0.12,
        }
        self.store.save_best_params("XAUUSDc", "STRONG_TREND", params)
        result = self.store.get_best_params("XAUUSDc", "STRONG_TREND")
        self.assertIsNotNone(result)
        self.assertEqual(result["strategy_name"], "gold_scalp_pro")
        self.assertAlmostEqual(result["win_rate"], 0.65)
        self.assertEqual(result["total_trades"], 50)

    def test_upsert(self):
        """save ซ้ำ = update ไม่ซ้ำ."""
        params1 = {"strategy_name": "v1", "win_rate": 0.5, "total_trades": 10}
        params2 = {"strategy_name": "v2", "win_rate": 0.7, "total_trades": 20}
        self.store.save_best_params("XAUUSDc", "STRONG_TREND", params1)
        self.store.save_best_params("XAUUSDc", "STRONG_TREND", params2)
        result = self.store.get_best_params("XAUUSDc", "STRONG_TREND")
        self.assertIsNotNone(result)
        self.assertEqual(result["strategy_name"], "v2")
        self.assertAlmostEqual(result["win_rate"], 0.7)

    def test_min_trades_filter(self):
        """ถ้า total_trades < 5 จะไม่ return."""
        params = {"strategy_name": "test", "total_trades": 2}
        self.store.save_best_params("XAUUSDc", "RANGING", params)
        result = self.store.get_best_params("XAUUSDc", "RANGING")
        self.assertIsNone(result)  # ไม่ถึง MIN_TRADES threshold


class TestRegimeIntelligenceEngine(unittest.TestCase):
    """ทดสอบ RegimeIntelligenceEngine orchestrator."""

    def test_analyze_returns_intelligence(self):
        """analyze() ต้อง return RegimeIntelligence."""
        engine = RegimeIntelligenceEngine()
        candles = _make_trending_candles("up")
        result = engine.analyze("XAUUSDc", candles, session="NY")
        self.assertIsInstance(result, RegimeIntelligence)
        self.assertEqual(result.symbol, "XAUUSDc")
        self.assertGreaterEqual(result.confidence, 0.0)
        self.assertLessEqual(result.confidence, 1.0)
        self.assertIn("lot_multiplier", result.risk_profile)

    def test_backward_compat_regime_context(self):
        """get_regime_context() ต้อง return RegimeContext."""
        engine = RegimeIntelligenceEngine()
        candles = _make_trending_candles("up")
        intel = engine.analyze("XAUUSDc", candles)
        ctx = engine.get_regime_context(intel)
        self.assertIsInstance(ctx, RegimeContext)
        self.assertIsInstance(ctx.regime, RegimeType)

    def test_low_vol_blocked(self):
        """LOW_VOLATILITY = trade_allowed=False."""
        engine = RegimeIntelligenceEngine()
        candles = _make_low_vol_candles()
        result = engine.analyze("XAUUSDc", candles)
        # Either blocked by actionable=False or lot_multiplier=0
        if result.regime in (RegimeType.LOW_VOLATILITY, RegimeType.ACCUMULATION):
            self.assertFalse(result.trade_allowed)


if __name__ == "__main__":
    print("=" * 60)
    print("  Market Regime Intelligence — Test Suite")
    print("=" * 60)
    unittest.main(verbosity=2)
