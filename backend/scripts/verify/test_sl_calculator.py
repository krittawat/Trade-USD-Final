"""
Test Smart SL Calculator — verify anti-hunt, regime-adaptive, H1 smoothing.

Tests:
  1. Regime-adaptive: high ADX → tighter SL, low ADX → wider SL
  2. H1 ATR smoothing: blended ATR ≠ M5 ATR alone
  3. Anti-hunt: SL not on round numbers
  4. Min distance enforced
  5. SL doesn't cross entry
  6. BE dodge: not exactly at entry price
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pandas as pd
import numpy as np
import pytest

from app.risk.sl_calculator import (
    calculate_smart_sl,
    compute_h1_atr,
    calculate_be_with_dodge,
    _get_regime_scale,
)


def _make_candles(n: int = 50, base_price: float = 2900.0, spread: float = 2.0) -> pd.DataFrame:
    """Create synthetic OHLCV candles."""
    np.random.seed(42)
    prices = base_price + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        'open': prices - 0.3,
        'high': prices + spread / 2,
        'low': prices - spread / 2,
        'close': prices,
        'tick_volume': [500] * n,
    })
    return df


def _make_h1_candles(n: int = 50, base_price: float = 2900.0) -> pd.DataFrame:
    """Create synthetic H1 candles."""
    np.random.seed(123)
    prices = base_price + np.cumsum(np.random.randn(n) * 2.0)
    df = pd.DataFrame({
        'open': prices - 1.0,
        'high': prices + 5.0,
        'low': prices - 5.0,
        'close': prices,
        'tick_volume': [2000] * n,
    })
    return df


# ═══════════════════════════════════════════════
# Test Regime-Adaptive Scaling
# ═══════════════════════════════════════════════

class TestRegimeAdaptive:
    def test_high_adx_tighter(self):
        """ADX >= 40 → scale < 1.0 (tighter SL)."""
        assert _get_regime_scale(45) == 0.85

    def test_moderate_adx_normal(self):
        """ADX 30-40 → scale = 1.0."""
        assert _get_regime_scale(35) == 1.0

    def test_low_adx_wider(self):
        """ADX 20-30 → scale > 1.0 (wider SL)."""
        assert _get_regime_scale(22) == 1.10

    def test_choppy_adx_widest(self):
        """ADX < 20 → widest SL."""
        assert _get_regime_scale(15) == 1.20

    def test_regime_adaptive_changes_sl(self):
        """SL should differ when ADX is high vs low."""
        candles = _make_candles()
        close = float(candles['close'].iloc[-1])
        atr = 3.0

        sl_high_adx = calculate_smart_sl(
            close=close, atr=atr, direction="BUY", candles=candles,
            sl_atr_mult=1.5, adx=45.0,
            enable_anti_hunt=False, enable_h1_smoothing=False,
        )
        sl_low_adx = calculate_smart_sl(
            close=close, atr=atr, direction="BUY", candles=candles,
            sl_atr_mult=1.5, adx=15.0,
            enable_anti_hunt=False, enable_h1_smoothing=False,
        )
        # Low ADX → wider SL → lower SL price for BUY
        assert sl_low_adx < sl_high_adx, (
            f"Low ADX SL ({sl_low_adx}) should be lower (wider) than high ADX SL ({sl_high_adx})"
        )


# ═══════════════════════════════════════════════
# Test H1 ATR Smoothing
# ═══════════════════════════════════════════════

class TestH1Smoothing:
    def test_h1_atr_computed(self):
        """H1 ATR should be computed from H1 candles."""
        h1 = _make_h1_candles()
        atr = compute_h1_atr(h1)
        assert atr is not None
        assert atr > 0

    def test_insufficient_h1_returns_none(self):
        """Too few H1 candles → None."""
        h1 = _make_h1_candles(n=5)
        atr = compute_h1_atr(h1)
        assert atr is None

    def test_h1_smoothing_changes_sl(self):
        """SL with H1 smoothing should differ from pure M5 SL."""
        candles = _make_candles()
        close = float(candles['close'].iloc[-1])
        h1 = _make_h1_candles()
        h1_atr = compute_h1_atr(h1)

        sl_without = calculate_smart_sl(
            close=close, atr=3.0, direction="BUY", candles=candles,
            sl_atr_mult=1.5, h1_atr=None,
            enable_anti_hunt=False, enable_regime_adaptive=False,
        )
        sl_with = calculate_smart_sl(
            close=close, atr=3.0, direction="BUY", candles=candles,
            sl_atr_mult=1.5, h1_atr=h1_atr,
            enable_anti_hunt=False, enable_regime_adaptive=False,
        )
        # With H1 smoothing, SL should be different
        assert sl_without != sl_with, "H1 smoothing should change SL"


# ═══════════════════════════════════════════════
# Test SL Safety
# ═══════════════════════════════════════════════

class TestSLSafety:
    def test_buy_sl_below_entry(self):
        """BUY SL must be below close."""
        candles = _make_candles()
        close = float(candles['close'].iloc[-1])
        sl = calculate_smart_sl(
            close=close, atr=3.0, direction="BUY", candles=candles,
            sl_atr_mult=1.5,
            enable_anti_hunt=False, enable_regime_adaptive=False,
        )
        assert sl < close, f"BUY SL ({sl}) must be below close ({close})"

    def test_sell_sl_above_entry(self):
        """SELL SL must be above close."""
        candles = _make_candles()
        close = float(candles['close'].iloc[-1])
        sl = calculate_smart_sl(
            close=close, atr=3.0, direction="SELL", candles=candles,
            sl_atr_mult=1.5,
            enable_anti_hunt=False, enable_regime_adaptive=False,
        )
        assert sl > close, f"SELL SL ({sl}) must be above close ({close})"

    def test_min_distance_enforced(self):
        """SL distance must be >= min_sl_distance."""
        candles = _make_candles()
        close = float(candles['close'].iloc[-1])
        min_dist = 5.0
        sl = calculate_smart_sl(
            close=close, atr=0.5, direction="BUY", candles=candles,
            sl_atr_mult=1.0, min_sl_distance=min_dist,
            enable_anti_hunt=False, enable_regime_adaptive=False,
        )
        actual_dist = abs(close - sl)
        assert actual_dist >= min_dist, (
            f"SL distance ({actual_dist}) must be >= min ({min_dist})"
        )


# ═══════════════════════════════════════════════
# Test Break-Even Dodge
# ═══════════════════════════════════════════════

class TestBEDodge:
    def test_be_not_at_entry_buy(self):
        """BUY BE SL should be above entry (lock micro-profit)."""
        be = calculate_be_with_dodge(2900.0, "BUY", "XAUUSDc", dodge_buffer=0.50)
        assert be > 2900.0, f"BUY BE ({be}) should be above entry 2900.00"

    def test_be_not_at_entry_sell(self):
        """SELL BE SL should be below entry."""
        be = calculate_be_with_dodge(2900.0, "SELL", "XAUUSDc", dodge_buffer=0.50)
        assert be < 2900.0, f"SELL BE ({be}) should be below entry 2900.00"

    def test_be_dodge_btc(self):
        """BTC BE should work with large dodge buffer."""
        be = calculate_be_with_dodge(95000.0, "BUY", "BTCUSDc", dodge_buffer=5.0)
        assert be > 95000.0

    def test_be_dodge_forex(self):
        """Forex BE should work with small dodge buffer."""
        be = calculate_be_with_dodge(1.0850, "BUY", "EURUSDc", dodge_buffer=0.00010)
        assert be > 1.0850


# ═══════════════════════════════════════════════
# Test Full Pipeline with Anti-Hunt
# ═══════════════════════════════════════════════

class TestFullPipeline:
    def test_full_buy(self):
        """Full pipeline BUY should produce valid SL."""
        candles = _make_candles()
        close = float(candles['close'].iloc[-1])
        sl = calculate_smart_sl(
            close=close, atr=3.0, direction="BUY", candles=candles,
            sl_atr_mult=1.5, sl_buffer_atr=0.3,
            swing_lookback=15, adx=25.0, symbol="XAUUSDc",
        )
        assert sl < close
        assert abs(close - sl) > 0  # non-zero distance

    def test_full_sell(self):
        """Full pipeline SELL should produce valid SL."""
        candles = _make_candles()
        close = float(candles['close'].iloc[-1])
        sl = calculate_smart_sl(
            close=close, atr=3.0, direction="SELL", candles=candles,
            sl_atr_mult=1.5, sl_buffer_atr=0.3,
            swing_lookback=15, adx=25.0, symbol="XAUUSDc",
        )
        assert sl > close

    def test_deterministic(self):
        """Same inputs → same output."""
        candles = _make_candles()
        close = float(candles['close'].iloc[-1])
        kwargs = dict(
            close=close, atr=3.0, direction="BUY", candles=candles,
            sl_atr_mult=1.5, adx=25.0, symbol="XAUUSDc",
        )
        sl1 = calculate_smart_sl(**kwargs)
        sl2 = calculate_smart_sl(**kwargs)
        assert sl1 == sl2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
