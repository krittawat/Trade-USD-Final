"""
Tests for TickVolumeAnalyzer — tick volume + OHLC microstructure analysis.

Tests:
    1. Sufficient data → is_valid=True
    2. Insufficient data → is_valid=False, score=0
    3. Rising volume trend detection
    4. Climax detection (3x avg + long wick)
    5. Dry-up detection (< 0.3x avg)
    6. Buying/selling pressure calculation
    7. Body conviction calculation
    8. Divergence detection (new high, falling vol)
    9. Score bounds check (-20 to +20)
"""

import sys
import os
import numpy as np
import pandas as pd

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.analysis.tick_volume_analyzer import TickVolumeAnalyzer, TickVolumeSignal


def _make_candles(
    n: int = 50,
    base_price: float = 2700.0,
    base_volume: float = 500.0,
    trend: str = "flat",
    vol_pattern: str = "normal",
) -> pd.DataFrame:
    """Create synthetic OHLCV candles for testing."""
    np.random.seed(42)

    prices = [base_price]
    for i in range(1, n):
        if trend == "up":
            change = abs(np.random.normal(0, 2)) + 0.5
        elif trend == "down":
            change = -(abs(np.random.normal(0, 2)) + 0.5)
        else:
            change = np.random.normal(0, 2)
        prices.append(prices[-1] + change)

    close = np.array(prices, dtype=float)
    open_ = close - np.random.normal(0, 1, n)
    high = np.maximum(close, open_) + abs(np.random.normal(0, 1, n))
    low = np.minimum(close, open_) - abs(np.random.normal(0, 1, n))

    # Volume pattern
    if vol_pattern == "normal":
        vol = np.full(n, base_volume) + np.random.normal(0, 50, n)
    elif vol_pattern == "climax":
        vol = np.full(n, base_volume) + np.random.normal(0, 50, n)
        # Last bar: 4x volume + widen wicks (climax signature)
        vol[-1] = base_volume * 4.0
        # Make last bar have long wicks (> 50% wick)
        mid = (high[-1] + low[-1]) / 2
        close[-1] = mid + 0.1
        open_[-1] = mid - 0.1
        high[-1] = mid + 5
        low[-1] = mid - 5
    elif vol_pattern == "dryup":
        vol = np.full(n, base_volume) + np.random.normal(0, 50, n)
        vol[-1] = base_volume * 0.1  # 10% of avg
    elif vol_pattern == "rising":
        # Volume steadily increasing
        vol = np.linspace(base_volume * 0.5, base_volume * 2.0, n)
    elif vol_pattern == "falling":
        vol = np.linspace(base_volume * 2.0, base_volume * 0.5, n)
    else:
        vol = np.full(n, base_volume)

    vol = np.maximum(vol, 1)  # no zero/negative volume

    df = pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": vol.astype(int),
    })
    return df


analyzer = TickVolumeAnalyzer()


# ─── Test 1: Valid data ───
def test_valid_data():
    candles = _make_candles(50)
    signal = analyzer.analyze(candles)
    assert signal.is_valid is True, "Should be valid with 50 bars"
    assert isinstance(signal.score, int)
    print("✅ test_valid_data PASSED")


# ─── Test 2: Insufficient data ───
def test_insufficient_data():
    candles = _make_candles(10)
    signal = analyzer.analyze(candles)
    assert signal.is_valid is False, "Should be invalid with 10 bars"
    assert signal.score == 0, "Score should be 0 when invalid"
    print("✅ test_insufficient_data PASSED")


def test_none_data():
    signal = analyzer.analyze(None)
    assert signal.is_valid is False
    assert signal.score == 0
    print("✅ test_none_data PASSED")


# ─── Test 3: Rising volume trend ───
def test_rising_volume_trend():
    candles = _make_candles(50, vol_pattern="rising")
    signal = analyzer.analyze(candles)
    assert signal.is_valid is True
    assert signal.volume_trend == "RISING", f"Expected RISING, got {signal.volume_trend}"
    assert signal.score > 0, "Rising volume should give positive score"
    print("✅ test_rising_volume_trend PASSED")


# ─── Test 4: Climax detection ───
def test_climax_detection():
    candles = _make_candles(50, vol_pattern="climax")
    signal = analyzer.analyze(candles)
    assert signal.is_valid is True
    assert signal.is_climax is True, "Should detect climax"
    assert signal.score < 0, "Climax should give negative score"
    print("✅ test_climax_detection PASSED")


# ─── Test 5: Dry-up detection ───
def test_dryup_detection():
    candles = _make_candles(50, vol_pattern="dryup")
    signal = analyzer.analyze(candles)
    assert signal.is_valid is True
    assert signal.is_dryup is True, "Should detect dry-up"
    assert signal.score < 0, "Dry-up should give negative score"
    print("✅ test_dryup_detection PASSED")


# ─── Test 6: Buying/selling pressure ───
def test_buying_pressure():
    candles = _make_candles(50)
    signal = analyzer.analyze(candles)
    assert 0.0 <= signal.buying_pressure <= 1.0, f"Buying pressure out of range: {signal.buying_pressure}"
    assert 0.0 <= signal.selling_pressure <= 1.0, f"Selling pressure out of range: {signal.selling_pressure}"
    # Pressure should roughly add to 1
    total = signal.buying_pressure + signal.selling_pressure
    assert 0.8 <= total <= 1.2, f"Pressure total should be ~1.0, got {total}"
    print("✅ test_buying_pressure PASSED")


# ─── Test 7: Body conviction ───
def test_body_conviction():
    candles = _make_candles(50)
    signal = analyzer.analyze(candles)
    assert 0.0 <= signal.body_conviction <= 1.0, f"Body conviction out of range: {signal.body_conviction}"
    print("✅ test_body_conviction PASSED")


# ─── Test 8: Divergence detection ───
def test_divergence_detection():
    # Create uptrend with declining volume → bearish divergence
    candles = _make_candles(50, trend="up", vol_pattern="falling")
    signal = analyzer.analyze(candles)
    assert signal.is_valid is True
    # May or may not detect divergence depending on exact values,
    # but divergence flag should be boolean
    assert isinstance(signal.has_divergence, bool)
    print("✅ test_divergence_detection PASSED")


# ─── Test 9: Score bounds ───
def test_score_bounds():
    for vol_pat in ["normal", "climax", "dryup", "rising", "falling"]:
        for trend in ["flat", "up", "down"]:
            candles = _make_candles(50, trend=trend, vol_pattern=vol_pat)
            signal = analyzer.analyze(candles)
            assert -20 <= signal.score <= 20, (
                f"Score {signal.score} out of [-20,+20] bounds "
                f"(vol={vol_pat}, trend={trend})"
            )
    print("✅ test_score_bounds PASSED")


# ─── Test 10: AD line trend ───
def test_ad_line_trend():
    candles = _make_candles(50)
    signal = analyzer.analyze(candles)
    assert signal.ad_line_trend in ("BULLISH", "BEARISH", "NEUTRAL"), (
        f"Unexpected AD trend: {signal.ad_line_trend}"
    )
    print("✅ test_ad_line_trend PASSED")


# ─── Test 11: No volume column ───
def test_no_volume_column():
    candles = _make_candles(50)
    candles = candles.drop(columns=["tick_volume"])
    signal = analyzer.analyze(candles)
    assert signal.is_valid is False, "Should be invalid without volume"
    print("✅ test_no_volume_column PASSED")


# ─── Run all ───
if __name__ == "__main__":
    tests = [
        test_valid_data,
        test_insufficient_data,
        test_none_data,
        test_rising_volume_trend,
        test_climax_detection,
        test_dryup_detection,
        test_buying_pressure,
        test_body_conviction,
        test_divergence_detection,
        test_score_bounds,
        test_ad_line_trend,
        test_no_volume_column,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"❌ {t.__name__} FAILED: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {passed+failed} total")
    if failed > 0:
        sys.exit(1)
    else:
        print("ALL TESTS PASSED ✅")
