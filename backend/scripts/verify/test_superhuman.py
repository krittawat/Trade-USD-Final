"""
Test Suite — Super-Human Trading Modules.

Tests for:
    1. MTF Confluence Engine (multi-timeframe scoring)
    2. Entry Optimizer (adaptive entry timing)
    3. Outcome Analyzer (trade outcome learning loop)

All tests are self-contained — no MT5 or DB connections required.
"""

import sys
import os
import numpy as np
import pandas as pd

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

def make_candles(n: int = 100, trend: str = "UP", base: float = 2000.0, volatility: float = 5.0) -> pd.DataFrame:
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
        "tick_volume": volume,
    }, index=dates)


# ====================================================================
# 1. MTF Confluence Engine Tests
# ====================================================================

print("\n=== MTF Confluence Engine ===")

try:
    from app.brain.mtf_confluence import MTFConfluenceEngine, MTFScore
    engine = MTFConfluenceEngine()
    test("MTF import", True)
except Exception as e:
    test("MTF import", False, str(e))
    engine = None

if engine:
    # Test: All TFs trending UP → high confluence
    candles_up = {
        "M1": make_candles(100, "UP"),
        "M5": make_candles(100, "UP"),
        "M15": make_candles(100, "UP"),
        "H1": make_candles(100, "UP"),
    }
    result = engine.score(candles_up, direction="BUY")
    test("MTF all_up score > 30", result.total > 30, f"got {result.total:.1f}")
    test("MTF all_up direction BUY or NEUTRAL", result.direction in ("BUY", "NEUTRAL"), f"got {result.direction}")
    test("MTF all_up no block", not result.should_block, f"should_block={result.should_block}")

    # Test: Conflicting TFs → low score
    candles_mixed = {
        "M5": make_candles(100, "UP"),
        "M15": make_candles(100, "DOWN"),
        "H1": make_candles(100, "DOWN"),
    }
    result_mixed = engine.score(candles_mixed, direction="BUY")
    test("MTF mixed has lower score", result_mixed.total < result.total,
         f"mixed={result_mixed.total:.1f} vs aligned={result.total:.1f}")

    # Test: Insufficient TFs → returns 0
    result_insuf = engine.score({"M5": make_candles(5)})
    test("MTF insufficient_tfs score=0", result_insuf.total == 0, f"got {result_insuf.total}")

    # Test: Direction conflict → score halved
    result_conflict = engine.score(candles_up, direction="SELL")
    test("MTF direction_conflict penalized", result_conflict.total <= result.total,
         f"conflict={result_conflict.total:.1f} vs agree={result.total:.1f}")

    # Test: Confidence boost/neutral for moderate score
    test("MTF moderate_score boost >= 0", result.confidence_boost >= 0,
         f"boost={result.confidence_boost}")


# ====================================================================
# 2. Entry Optimizer Tests
# ====================================================================

print("\n=== Entry Optimizer ===")

try:
    from app.brain.entry_optimizer import EntryOptimizer, EntryQuality
    optimizer = EntryOptimizer()
    test("Entry import", True)
except Exception as e:
    test("Entry import", False, str(e))
    optimizer = None

if optimizer:
    # Test: Normal entry
    candles = make_candles(100, "UP")
    quality = optimizer.evaluate(candles, direction="BUY")
    test("Entry returns grade", quality.grade in ("A", "B", "C"), f"got {quality.grade}")
    test("Entry returns score 0-100", 0 <= quality.score <= 100, f"got {quality.score}")
    test("Entry returns optimal_entry > 0", quality.optimal_entry > 0, f"got {quality.optimal_entry}")

    # Test: Insufficient data
    short = make_candles(3)
    quality_short = optimizer.evaluate(short, direction="BUY")
    test("Entry insufficient -> grade B", quality_short.grade == "B", f"got {quality_short.grade}")

    # Test: Confidence boost range
    test("Entry boost in range", -0.15 <= quality.confidence_boost <= 0.15,
         f"got {quality.confidence_boost}")


# ====================================================================
# 3. Outcome Analyzer Tests
# ====================================================================

print("\n=== Outcome Analyzer ===")

try:
    from app.brain.outcome_analyzer import OutcomeAnalyzer, TradeContext, StrategyEdge
    # Test without DB
    analyzer = OutcomeAnalyzer(db=None)
    test("Outcome import", True)
except Exception as e:
    test("Outcome import", False, str(e))
    analyzer = None

if analyzer:
    # Test: No DB → graceful degradation
    status = analyzer.get_status()
    test("Outcome no_db status ok", status["initialized"] == False)

    # Test: Edge boost with no data → 0
    boost = analyzer.get_edge_boost("gold_elite", "XAUUSDc")
    test("Outcome no_data boost=0", boost == 0.0, f"got {boost}")

    # Test: Best strategy with no data → None
    best = analyzer.get_best_strategy("XAUUSDc")
    test("Outcome no_data best=None", best is None, f"got {best}")

    # Test: Prune returns 0 with no DB
    pruned = analyzer.prune_old_data()
    test("Outcome prune no_db=0", pruned == 0)


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
