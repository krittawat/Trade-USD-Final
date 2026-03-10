"""
Tests for Bull/Bear Power Engine.

Verifies:
1. Uptrend candles produce BULL / STRONG_BULL.
2. Downtrend candles produce BEAR / STRONG_BEAR.
3. Flat candles produce NEUTRAL.
4. Insufficient data handles gracefully.
"""

import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.analysis.bull_bear_power import BullBearPowerEngine

def _make_candles(
    n: int = 50,
    base_price: float = 2700.0,
    trend: str = "flat",
    vol_mean: float = 500.0,
) -> pd.DataFrame:
    """Helper to generate synthetic candles."""
    np.random.seed(42)
    
    if trend == "up":
        price_moves = np.random.normal(2.0, 1.5, n)
    elif trend == "down":
        price_moves = np.random.normal(-2.0, 1.5, n)
    elif trend == "strong_up":
        price_moves = np.random.normal(5.0, 2.0, n)
    elif trend == "strong_down":
        price_moves = np.random.normal(-5.0, 2.0, n)
    else:
        price_moves = np.random.normal(0.0, 1.0, n)
        
    closes = base_price + np.cumsum(price_moves)
    
    data = []
    for i in range(n):
        c = closes[i]
        o = closes[i-1] if i > 0 else c - price_moves[i]
        
        # Ensure high >= max(o, c) and low <= min(o, c)
        whip_h = np.random.uniform(0.1, 2.0)
        whip_l = np.random.uniform(0.1, 2.0)
        
        h = max(o, c) + whip_h
        l = min(o, c) - whip_l
        
        v = max(10, np.random.normal(vol_mean, vol_mean * 0.2))
        
        data.append({
            "time": pd.Timestamp.now() + pd.Timedelta(minutes=5*i),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "tick_volume": v,
        })
        
    return pd.DataFrame(data)

engine = BullBearPowerEngine()

def test_insufficient_data():
    print("Testing insufficient data...")
    candles = _make_candles(n=10)
    result = engine.analyze(candles)
    assert not result.is_valid, "Should be invalid with < 20 bars"
    assert "Insufficient data" in result.reasons[0]
    print("  PASS")

def test_flat_trend():
    print("Testing flat trend...")
    candles = _make_candles(n=50, trend="flat")
    result = engine.analyze(candles)
    assert result.is_valid, "Should be valid"
    assert result.verdict == "NEUTRAL", f"Expected NEUTRAL, got {result.verdict} (Score: {result.score})"
    assert -25 <= result.score <= 25, f"Score {result.score} should be between -25 and 25"
    print("  PASS")

def test_up_trend():
    print("Testing normal up trend...")
    candles = _make_candles(n=50, trend="up")
    result = engine.analyze(candles)
    assert result.is_valid
    assert result.verdict in ["BULL", "STRONG_BULL"], f"Expected BULLish, got {result.verdict}"
    assert result.score > 25, f"Score {result.score} should be > 25"
    assert result.bull_power > 0, "Bull power should be positive"
    print("  PASS")

def test_strong_up_trend():
    print("Testing strong up trend...")
    candles = _make_candles(n=60, trend="strong_up")
    result = engine.analyze(candles)
    assert result.is_valid
    assert result.verdict == "STRONG_BULL", f"Expected STRONG_BULL, got {result.verdict}"
    assert result.score > 60, f"Score {result.score} should be > 60"
    print("  PASS")

def test_down_trend():
    print("Testing normal down trend...")
    candles = _make_candles(n=50, trend="down")
    result = engine.analyze(candles)
    assert result.is_valid
    assert result.verdict in ["BEAR", "STRONG_BEAR"], f"Expected BEARish, got {result.verdict}"
    assert result.score < -25, f"Score {result.score} should be < -25"
    assert result.bear_power < 0, "Bear power should be negative"
    print("  PASS")

def test_strong_down_trend():
    print("Testing strong down trend...")
    candles = _make_candles(n=60, trend="strong_down")
    result = engine.analyze(candles)
    assert result.is_valid
    assert result.verdict == "STRONG_BEAR", f"Expected STRONG_BEAR, got {result.verdict}"
    assert result.score < -60, f"Score {result.score} should be < -60"
    print("  PASS")

if __name__ == "__main__":
    tests = [
        test_insufficient_data,
        test_flat_trend,
        test_up_trend,
        test_strong_up_trend,
        test_down_trend,
        test_strong_down_trend,
    ]
    
    passed = 0
    failed = 0
    
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            failed += 1
            
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
    if failed > 0:
        sys.exit(1)
    else:
        print("ALL TESTS PASSED ✅")
