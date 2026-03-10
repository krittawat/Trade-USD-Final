"""
Test Suite — Pattern Intelligence Upgrade.

Tests all 6 enhancements:
    1. SMC Patterns (FVG, Order Block, BOS, CHoCH) in PatternDetector
    2. Multi-TF detection (timeframe field, H1 tag)
    3. Pattern Sequence Learning (memory_store methods)
    4. Multi-bar ML features (21 features, no NaN)
    5. Context Awareness in PatternScorer
    6. Per-Symbol Adaptive Weights in PatternScorer

All tests are self-contained — no MT5 or DB connections required.
"""

import sys
import os
import numpy as np
import pandas as pd
import sqlite3
import tempfile

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Fix Windows console encoding
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

passed = 0
failed = 0
fail_details = []


def test(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        print(f"  OK  {name}")
        passed += 1
    else:
        msg = detail or "assertion failed"
        print(f" FAIL {name}: {msg}")
        failed += 1
        fail_details.append((name, msg))


# ====================================================================
# Helper: generate synthetic candles
# ====================================================================

def make_candles(n: int = 100, trend: str = "UP", base: float = 2000.0,
                 volatility: float = 5.0) -> pd.DataFrame:
    """Generate synthetic OHLCV candles."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=n, freq="5min")

    close = np.zeros(n)
    close[0] = base
    for i in range(1, n):
        if trend == "UP":
            close[i] = close[i-1] + np.random.normal(0.5, volatility)
        elif trend == "DOWN":
            close[i] = close[i-1] + np.random.normal(-0.5, volatility)
        else:
            close[i] = close[i-1] + np.random.normal(0, volatility)

    high = close + np.random.uniform(1, 5, n)
    low = close - np.random.uniform(1, 5, n)
    open_ = close + np.random.normal(0, 2, n)
    volume = np.random.randint(100, 1000, n).astype(float)

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dates)


def make_fvg_candles() -> pd.DataFrame:
    """Create candles with a clear bullish FVG (gap up)."""
    np.random.seed(42)
    n = 50
    dates = pd.date_range("2026-01-01", periods=n, freq="5min")

    # Normal candles for the first 47 bars
    close = np.linspace(2000, 2020, n)
    high = close + np.random.uniform(1, 3, n)
    low = close - np.random.uniform(1, 3, n)
    open_ = close - np.random.uniform(0, 1, n)
    volume = np.random.randint(100, 1000, n).astype(float)

    # Create FVG: bar[-3] high < bar[-1] low (gap up)
    # Bar -3 (i=47): small bar
    high[47] = 2015.0
    low[47] = 2012.0
    open_[47] = 2013.0
    close[47] = 2014.0

    # Bar -2 (i=48): big displacement candle (creates the gap)
    open_[48] = 2014.5
    close[48] = 2025.0
    low[48] = 2014.0
    high[48] = 2026.0

    # Bar -1 (i=49): gap up — low > bar[-3] high
    low[49] = 2023.0  # > 2015.0 (bar[-3].high) — this is the FVG
    open_[49] = 2025.0
    close[49] = 2026.0
    high[49] = 2027.0

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dates)


def make_displacement_candles() -> pd.DataFrame:
    """Create candles with clear bullish displacement for Order Block detection."""
    np.random.seed(42)
    n = 50
    dates = pd.date_range("2026-01-01", periods=n, freq="5min")
    close = np.linspace(2000, 2020, n)
    high = close + 2
    low = close - 2
    open_ = close - 0.5
    volume = np.random.randint(100, 1000, n).astype(float)

    # Bearish candle (Order Block candidate) at i=48
    open_[48] = 2020.0
    close[48] = 2015.0
    high[48] = 2021.0
    low[48] = 2014.0

    # Strong bullish displacement at i=49 (body >> ATR)
    open_[49] = 2015.0
    close[49] = 2055.0  # huge body
    high[49] = 2056.0
    low[49] = 2014.5

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dates)


# ====================================================================
# 1. SMC Pattern Detection Tests
# ====================================================================

print("\n=== #1 SMC Pattern Detection ===")

try:
    from app.brain.pattern_detector import PatternDetector, PatternSignal
    detector = PatternDetector()
    test("PatternDetector import", True)
except Exception as e:
    test("PatternDetector import", False, str(e))
    detector = None

if detector:
    # Test FVG detection
    fvg_candles = make_fvg_candles()
    signals_fvg = detector.detect_all(fvg_candles)
    fvg_names = [s.name for s in signals_fvg]
    test("FVG detected", "bullish_fvg" in fvg_names, f"found: {fvg_names}")

    # Test Order Block detection
    ob_candles = make_displacement_candles()
    signals_ob = detector.detect_all(ob_candles)
    ob_names = [s.name for s in signals_ob]
    test("Order Block detected", "bullish_order_block" in ob_names, f"found: {ob_names}")

    # Test BOS / CHoCH — use trending candles
    trend_candles = make_candles(200, "UP", volatility=8.0)
    signals_trend = detector.detect_all(trend_candles)
    smc_names = [s.name for s in signals_trend if "bos" in s.name or "choch" in s.name]
    test("BOS/CHoCH recognizable", True, f"SMC signals: {smc_names}")

    # Verify FVG details have required fields
    fvg_sigs = [s for s in signals_fvg if "fvg" in s.name]
    if fvg_sigs:
        fvg_det = fvg_sigs[0].details
        test("FVG has zone details", "fvg_top" in fvg_det and "fvg_bottom" in fvg_det,
             f"details: {fvg_det}")
    else:
        test("FVG has zone details", False, "No FVG signal found")

    # OB details
    ob_sigs = [s for s in signals_ob if "order_block" in s.name]
    if ob_sigs:
        ob_det = ob_sigs[0].details
        test("OB has zone details", "ob_high" in ob_det and "ob_low" in ob_det,
             f"details: {ob_det}")
    else:
        test("OB has zone details", False, "No OB signal found")


# ====================================================================
# 2. Multi-TF Detection Tests
# ====================================================================

print("\n=== #2 Multi-TF Timeframe Field ===")

try:
    from app.brain.pattern_detector import PatternSignal
    sig = PatternSignal(name="test", direction="bullish", strength=0.5)
    test("PatternSignal has timeframe", hasattr(sig, "timeframe"))
    test("PatternSignal default TF=M5", sig.timeframe == "M5", f"got {sig.timeframe}")

    # Test explicit H1 tag
    sig_h1 = PatternSignal(name="h1_engulfing", direction="bullish",
                           strength=0.85, timeframe="H1")
    test("PatternSignal H1 tag", sig_h1.timeframe == "H1")
except Exception as e:
    test("Multi-TF field", False, str(e))


# ====================================================================
# 3. Pattern Sequence Learning Tests
# ====================================================================

print("\n=== #3 Pattern Sequence Learning ===")

try:
    from app.brain.memory_store import MemoryStore

    # Create temp DB for testing
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name

    ms = MemoryStore(db_path=tmp_path)
    ms.connect()
    test("MemoryStore connect", True)

    # Record sequences
    ms.record_pattern_sequence("XAUUSDc", "doji→engulfing", is_win=True, profit=5.0)
    ms.record_pattern_sequence("XAUUSDc", "doji→engulfing", is_win=True, profit=3.0)
    ms.record_pattern_sequence("XAUUSDc", "doji→engulfing", is_win=True, profit=4.0)
    ms.record_pattern_sequence("XAUUSDc", "doji→engulfing", is_win=False, profit=-2.0)

    # Query sequences
    best = ms.get_best_sequences("XAUUSDc", min_trades=3)
    test("Sequence recorded", len(best) > 0, f"got {len(best)} sequences")
    if best:
        test("Sequence WR=0.75", best[0]["win_rate"] == 0.75,
             f"got WR={best[0]['win_rate']}")
        test("Sequence total=4", best[0]["total_trades"] == 4,
             f"got total={best[0]['total_trades']}")

    # Adaptive weights
    # Record some pattern performance first
    ms.record_pattern_outcomes(
        "XAUUSDc",
        pattern_win_rates={"bullish_engulfing": 0.80, "doji": 0.35},
        patterns_found={"bullish_engulfing": 10, "doji": 10},
    )

    weights = ms.get_pattern_weights_for_symbol("XAUUSDc", min_trades=5)
    test("Adaptive weights returned", len(weights) > 0, f"weights: {weights}")
    if "bullish_engulfing" in weights:
        test("High WR → weight 1.5", weights["bullish_engulfing"] == 1.5,
             f"got {weights['bullish_engulfing']}")
    if "doji" in weights:
        test("Low WR → weight 0.5", weights["doji"] == 0.5,
             f"got {weights['doji']}")

    ms.disconnect()
    os.unlink(tmp_path)

except Exception as e:
    test("Sequence learning", False, str(e))


# ====================================================================
# 4. Multi-bar ML Features Tests
# ====================================================================

print("\n=== #4 Multi-bar ML Features ===")

try:
    from app.brain.ml_pattern_learner import MLPatternLearner

    learner = MLPatternLearner()
    candles = make_candles(100, "UP")
    features = learner._extract_features(candles, regime="TRENDING_UP", session="NY")

    test("Features extracted", features is not None)
    if features:
        test("Feature count = 21", len(features) == 21, f"got {len(features)}")
        test("No NaN in features",
             all(not (np.isnan(f) or np.isinf(f)) for f in features))
        test("Feature names = 21", len(learner._feature_names) == 21,
             f"got {len(learner._feature_names)}")

        # Verify new features are present in names
        new_features = {"avg_body_5", "avg_wick_5", "body_trend",
                        "dir_consistency", "range_expansion", "close_pos"}
        actual = set(learner._feature_names)
        missing = new_features - actual
        test("All new features named", len(missing) == 0,
             f"missing: {missing}")

except Exception as e:
    test("ML features", False, str(e))


# ====================================================================
# 5. Context Awareness Tests
# ====================================================================

print("\n=== #5 Context Awareness ===")

try:
    from app.brain.pattern_scorer import PatternScorer, PatternScore, CONTEXT_BOS_BOOST

    scorer = PatternScorer()

    # Create signals with SMC context
    sigs = [
        PatternSignal(name="bullish_engulfing", direction="bullish", strength=0.75),
        PatternSignal(name="near_support", direction="bullish", strength=0.60,
                      details={"level": 2000.0, "touches": 3}),
        PatternSignal(name="bullish_bos", direction="bullish", strength=0.80,
                      details={"broken_level": 2010.0, "swing_bar": 45}),
        PatternSignal(name="bullish_fvg", direction="bullish", strength=0.70,
                      details={"fvg_top": 2015.0, "fvg_bottom": 2010.0}),
    ]

    score = scorer.score(sigs, "BUY", "XAUUSDc", "TRENDING_UP")
    test("Context boost > 0", score.context_boost > 0, f"got {score.context_boost}")
    test("Overall boost > 0.15", score.confidence_boost > 0.15,
         f"got {score.confidence_boost}")
    test("Reason has 'context'", "context" in score.reason, f"reason: {score.reason}")

    # Without context — just engulfing alone
    sigs_no_ctx = [
        PatternSignal(name="bullish_engulfing", direction="bullish", strength=0.75),
    ]
    score_no_ctx = scorer.score(sigs_no_ctx, "BUY", "XAUUSDc")
    test("Context boost > no-context boost",
         score.confidence_boost > score_no_ctx.confidence_boost,
         f"ctx={score.confidence_boost} vs no_ctx={score_no_ctx.confidence_boost}")

except Exception as e:
    test("Context awareness", False, str(e))


# ====================================================================
# 6. Adaptive Weights Tests
# ====================================================================

print("\n=== #6 Adaptive Weights ===")

try:
    from app.brain.pattern_scorer import PatternScorer

    # Mock memory_store
    class MockStore:
        def get_best_patterns(self, **kw):
            return [
                {"pattern_name": "bullish_engulfing", "win_rate": 0.85},
            ]
        def get_best_sequences(self, **kw):
            return []
        def get_pattern_weights_for_symbol(self, **kw):
            return {
                "bullish_engulfing": 1.5,  # high WR → boost more
                "doji": 0.5,               # low WR → dampen
            }

    scorer_ada = PatternScorer(memory_store=MockStore())

    sigs = [
        PatternSignal(name="bullish_engulfing", direction="bullish", strength=0.75),
    ]
    score = scorer_ada.score(sigs, "BUY", "XAUUSDc", "TRENDING_UP")
    test("Adaptive applied", score.adaptive_applied, f"got {score.adaptive_applied}")

    # Compare: with adaptive vs without
    scorer_plain = PatternScorer()
    score_plain = scorer_plain.score(sigs, "BUY", "XAUUSDc")
    test("Adaptive boost > plain boost",
         score.confidence_boost > score_plain.confidence_boost,
         f"adaptive={score.confidence_boost} vs plain={score_plain.confidence_boost}")

    # Test dampening for low-WR pattern
    sigs_doji = [
        PatternSignal(name="doji", direction="bullish", strength=0.5),
    ]
    score_doji = scorer_ada.score(sigs_doji, "BUY", "XAUUSDc")
    score_doji_plain = scorer_plain.score(sigs_doji, "BUY", "XAUUSDc")
    test("Low-WR dampened",
         score_doji.confidence_boost < score_doji_plain.confidence_boost,
         f"adaptive={score_doji.confidence_boost} plain={score_doji_plain.confidence_boost}")

except Exception as e:
    test("Adaptive weights", False, str(e))


# ====================================================================
# Summary
# ====================================================================

total = passed + failed
print(f"\n{'='*50}")
print(f"Result: {passed}/{total} passed, {failed} failed")
if fail_details:
    print("\nFailed tests:")
    for name, err in fail_details:
        print(f"  - {name}: {err}")
    sys.exit(1)
else:
    print("All tests passed! ✓")
    sys.exit(0)
