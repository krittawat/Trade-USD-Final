"""
Test Deep Model V4 — Verifies model architecture, training, and strategy integration.

Tests:
    1. Model architecture shapes (input=55, output=3)
    2. Forward pass produces valid softmax probabilities
    3. Per-symbol model isolation
    4. Feature engine V4 produces 55 features
    5. Strategy produces valid decisions
"""

import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime, timezone

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))


def test_model_architecture():
    """Test model creates with correct shapes and forward pass works."""
    print("=== Test 1: Model Architecture ===")

    from app.brain.deep_model_v4 import (
        TransformerGRU, ConvGateBlock, Attention,
        INPUT_SIZE_V4, SEQUENCE_LENGTH, NUM_CLASSES,
    )
    import torch

    # Create model
    model = TransformerGRU(input_size=INPUT_SIZE_V4)
    param_count = sum(p.numel() for p in model.parameters())
    print(f"  Parameters: {param_count:,}")
    assert param_count > 10000, f"Model too small: {param_count}"
    print(f"  ✅ Model has {param_count:,} parameters")

    # Test forward pass
    batch_size = 4
    x = torch.randn(batch_size, SEQUENCE_LENGTH, INPUT_SIZE_V4)
    logits = model(x)
    assert logits.shape == (batch_size, NUM_CLASSES), f"Expected ({batch_size}, {NUM_CLASSES}), got {logits.shape}"
    print(f"  ✅ Forward pass: input({batch_size}, {SEQUENCE_LENGTH}, {INPUT_SIZE_V4}) → output{logits.shape}")

    # Test predict_proba
    probs = model.predict_proba(x)
    assert probs.shape == (batch_size, NUM_CLASSES)
    sums = probs.sum(dim=-1)
    for s in sums:
        assert abs(s.item() - 1.0) < 1e-5, f"Probs don't sum to 1: {s.item()}"
    print(f"  ✅ Softmax probabilities sum to 1.0")

    print("=== Test 1: PASSED ===\n")


def test_per_symbol_isolation():
    """Test per-symbol models are independent."""
    print("=== Test 2: Per-Symbol Model Isolation ===")

    from app.brain.deep_model_v4 import DeepModelV4

    model_a = DeepModelV4("TEST_A")
    model_b = DeepModelV4("TEST_B")

    assert model_a.symbol == "TEST_A"
    assert model_b.symbol == "TEST_B"

    # Predict should return neutral for untrained models
    dummy = np.random.randn(60, 55).astype(np.float32)
    pred_a = model_a.predict(dummy)
    pred_b = model_b.predict(dummy)

    assert pred_a["action"] == "HOLD", f"Untrained model should HOLD, got {pred_a['action']}"
    assert pred_b["action"] == "HOLD", f"Untrained model should HOLD, got {pred_b['action']}"
    print(f"  ✅ Untrained models return HOLD")

    # Status check
    status_a = model_a.get_status()
    assert status_a["symbol"] == "TEST_A"
    assert status_a["trained"] == False
    print(f"  ✅ Status reports correctly")

    print("=== Test 2: PASSED ===\n")


def test_feature_engine_v4():
    """Test V4 feature engine produces 55 features."""
    print("=== Test 3: Feature Engine V4 ===")

    from app.brain.mtf_feature_engine import (
        MTFFeatureEngine, ALL_FEATURES_V4, INPUT_SIZE_V4, V4_EXTRA_FEATURES,
    )

    assert len(ALL_FEATURES_V4) == 55, f"Expected 55 features, got {len(ALL_FEATURES_V4)}"
    assert INPUT_SIZE_V4 == 55
    assert len(V4_EXTRA_FEATURES) == 12
    print(f"  ✅ Feature constants correct: {len(ALL_FEATURES_V4)} total, {len(V4_EXTRA_FEATURES)} V4-specific")

    # Test with synthetic data
    np.random.seed(42)
    n = 200
    dates = pd.date_range(end=datetime.now(), periods=n, freq="5min")
    base_price = 2000.0
    prices = base_price + np.cumsum(np.random.randn(n) * 2)

    df = pd.DataFrame({
        "time": dates,
        "open": prices,
        "high": prices + np.abs(np.random.randn(n) * 3),
        "low": prices - np.abs(np.random.randn(n) * 3),
        "close": prices + np.random.randn(n),
        "volume": np.random.randint(100, 1000, n),
    })

    engine = MTFFeatureEngine()

    # Test _compute_m5_features + _compute_v4_features
    m5 = engine._compute_m5_features(df.copy())
    m5_v4 = engine._compute_v4_features(m5)

    for feat in V4_EXTRA_FEATURES:
        assert feat in m5_v4.columns, f"Missing V4 feature: {feat}"
    print(f"  ✅ All 12 V4 features computed on synthetic data")

    # Check no NaN/inf in V4 features
    for feat in V4_EXTRA_FEATURES:
        vals = m5_v4[feat].dropna()
        if len(vals) > 0:
            assert not np.isinf(vals.values).any(), f"Inf in {feat}"
    print(f"  ✅ No infinities in V4 features")

    print("=== Test 3: PASSED ===\n")


def test_3class_target():
    """Test 3-class target generation."""
    print("=== Test 4: 3-Class Target ===")

    from app.brain.mtf_feature_engine import MTFFeatureEngine

    engine = MTFFeatureEngine()

    # Create trending data (should produce some BUY/SELL labels, not all HOLD)
    np.random.seed(123)
    n = 500
    dates = pd.date_range(end=datetime.now(), periods=n, freq="5min")
    base = 2000.0

    # Mix of trending and choppy periods
    trend_up = np.cumsum(np.random.randn(n // 2) * 3 + 0.5)
    trend_down = np.cumsum(np.random.randn(n // 2) * 3 - 0.5)
    prices = np.concatenate([base + trend_up, base + trend_down])

    df = pd.DataFrame({
        "time": dates,
        "open": prices,
        "high": prices + np.abs(np.random.randn(n) * 5),
        "low": prices - np.abs(np.random.randn(n) * 5),
        "close": prices + np.random.randn(n) * 2,
        "volume": np.random.randint(100, 1000, n),
    })

    # Compute M5 features (needed for atr_raw)
    m5 = engine._compute_m5_features(df.copy())

    # Compute 3-class target
    result = engine._compute_target_3class(m5)

    assert "target_3class" in result.columns
    unique_labels = set(result["target_3class"].unique())
    print(f"  Labels found: {unique_labels}")

    from collections import Counter
    dist = Counter(result["target_3class"].tolist())
    total = len(result)
    print(f"  BUY:  {dist.get(0, 0):,} ({dist.get(0, 0)/total*100:.1f}%)")
    print(f"  HOLD: {dist.get(1, 0):,} ({dist.get(1, 0)/total*100:.1f}%)")
    print(f"  SELL: {dist.get(2, 0):,} ({dist.get(2, 0)/total*100:.1f}%)")

    # Should have at least some of each class in trending data
    assert 0 in unique_labels or 2 in unique_labels, "Expected at least one BUY or SELL in trending data"
    print(f"  ✅ 3-class target generates valid labels")

    print("=== Test 4: PASSED ===\n")


def test_mini_training():
    """Test training pipeline with small synthetic data."""
    print("=== Test 5: Mini Training ===")

    from app.brain.deep_model_v4 import DeepModelV4, SEQUENCE_LENGTH, INPUT_SIZE_V4

    # Create small training set
    np.random.seed(42)
    n_samples = 600
    X = np.random.randn(n_samples, SEQUENCE_LENGTH, INPUT_SIZE_V4).astype(np.float32)
    y = np.random.randint(0, 3, n_samples).astype(np.int64)

    model = DeepModelV4("TEST_TRAIN")
    result = model.train_session(X, y, epochs=3, verbose=True)

    assert result["status"] == "trained", f"Expected 'trained', got {result['status']}"
    assert result["symbol"] == "TEST_TRAIN"
    assert result["input_size"] == INPUT_SIZE_V4
    assert result["num_classes"] == 3
    print(f"  ✅ Training completed successfully")

    # Test prediction after training
    test_seq = np.random.randn(SEQUENCE_LENGTH, INPUT_SIZE_V4).astype(np.float32)
    pred = model.predict(test_seq)

    assert pred["action"] in ("BUY", "HOLD", "SELL"), f"Invalid action: {pred['action']}"
    assert 0.0 <= pred["buy"] <= 1.0
    assert 0.0 <= pred["hold"] <= 1.0
    assert 0.0 <= pred["sell"] <= 1.0
    total_prob = pred["buy"] + pred["hold"] + pred["sell"]
    assert abs(total_prob - 1.0) < 0.01, f"Probs don't sum to 1: {total_prob}"
    print(f"  Prediction: {pred}")
    print(f"  ✅ Post-training prediction works")

    # Cleanup test model files
    import glob
    for f in glob.glob(os.path.join(model._model_path().replace("TEST_TRAIN.pth", ""), "*TEST_TRAIN*")):
        os.remove(f)
        print(f"  [cleanup] Removed {f}")

    print("=== Test 5: PASSED ===\n")


def test_strategy_integration():
    """Test V4 strategy produces valid decisions."""
    print("=== Test 6: Strategy Integration ===")

    from app.strategy.templates.ai_deep_v4_strategy import AIDeepV4Strategy
    from app.domain.models import SymbolProfile
    from app.domain.enums import RegimeType

    strategy = AIDeepV4Strategy()
    assert strategy.name == "ai_deep_v4"
    assert strategy.timeframe == "M5"
    print(f"  ✅ Strategy created")

    # Create mock profile
    profile = SymbolProfile(
        symbol="XAUUSDc",
        digits=2,
        point=0.01,
        contract_size=100,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        spread_avg=20,
    )

    # Create mock candles
    np.random.seed(42)
    n = 200
    dates = pd.date_range(end=datetime.now(), periods=n, freq="5min")
    base = 2000.0
    prices = base + np.cumsum(np.random.randn(n) * 2)

    candles = pd.DataFrame({
        "time": dates,
        "open": prices,
        "high": prices + np.abs(np.random.randn(n) * 3),
        "low": prices - np.abs(np.random.randn(n) * 3),
        "close": prices + np.random.randn(n),
        "tick_volume": np.random.randint(100, 1000, n),
        "volume": np.random.randint(100, 1000, n),
    })

    decision = strategy.analyze(
        candles=candles,
        profile=profile,
        regime=RegimeType.STRONG_TREND,
    )

    print(f"  Decision: action={decision.action}, reason={decision.reason[:80]}...")

    # Should be HOLD (no trained model)
    assert decision is not None
    print(f"  ✅ Strategy produces valid decision without trained model")

    print("=== Test 6: PASSED ===\n")


if __name__ == "__main__":
    print("=" * 60)
    print("  Deep Model V4 — Full Test Suite")
    print("=" * 60)

    tests = [
        ("Architecture", test_model_architecture),
        ("Per-Symbol Isolation", test_per_symbol_isolation),
        ("Feature Engine V4", test_feature_engine_v4),
        ("3-Class Target", test_3class_target),
        ("Mini Training", test_mini_training),
        ("Strategy Integration", test_strategy_integration),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"=== {name}: FAILED ===")
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"  Results: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
