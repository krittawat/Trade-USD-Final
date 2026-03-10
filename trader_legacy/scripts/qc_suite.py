"""
OPUS QC Suite — ตรวจสอบทุกโมดูลก่อนเปิด LIVE
เป้าหมาย: ผ่าน 100% ถึงจะอนุญาตให้รัน Live ได้
"""
import sys
import json
import traceback
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

# ─── Helpers ──────────────────────────────────────────────
PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []

def check(name: str, func):
    try:
        ok, detail = func()
        status = PASS if ok else FAIL
        results.append({"test": name, "status": status, "detail": detail})
        print(f"  {status}  {name}: {detail}")
    except Exception as e:
        results.append({"test": name, "status": FAIL, "detail": str(e)})
        print(f"  {FAIL}  {name}: {e}")
        traceback.print_exc()

# ─── Fake OHLCV DataFrame for unit checks ────────────────
def _make_test_df(n=200):
    np.random.seed(42)
    base = 2000.0
    closes = base + np.cumsum(np.random.randn(n) * 2)
    df = pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="5min").astype(int) // 10**9,
        "open":  closes + np.random.randn(n),
        "high":  closes + np.abs(np.random.randn(n) * 3),
        "low":   closes - np.abs(np.random.randn(n) * 3),
        "close": closes,
        "tick_volume": np.random.randint(100, 5000, n),
    })
    # Ensure high >= open,close and low <= open,close
    df["high"] = df[["open", "close", "high"]].max(axis=1)
    df["low"]  = df[["open", "close", "low"]].min(axis=1)
    return df

# ═══════════════════════════════════════════════════════════
# 1. CONFIG VALIDATION
# ═══════════════════════════════════════════════════════════
def test_config_loads():
    path = Path("d:/VibeCode/Trade/trader/config/settings.json")
    with open(path) as f:
        cfg = json.load(f)
    required_keys = ["risk_limits", "regime", "liquidity", "session_hours_utc", "symbols", "strategy"]
    missing = [k for k in required_keys if k not in cfg]
    return len(missing) == 0, f"Missing keys: {missing}" if missing else "All keys present"

def test_risk_limits_valid():
    with open("d:/VibeCode/Trade/trader/config/settings.json") as f:
        cfg = json.load(f)
    rl = cfg["risk_limits"]
    ok = (
        0 < rl["max_risk_per_trade_percent"] <= 5 and
        0 < rl["max_daily_loss_percent"] <= 10 and
        rl["max_consecutive_losses"] >= 1
    )
    return ok, f"risk/trade={rl['max_risk_per_trade_percent']}%, daily_cap={rl['max_daily_loss_percent']}%"

# ═══════════════════════════════════════════════════════════
# 2. FEATURE ENGINEERING
# ═══════════════════════════════════════════════════════════
def test_volatility_features():
    from trader.features.volatility import add_volatility_features
    df = _make_test_df()
    df = add_volatility_features(df)
    required_cols = ["atr", "atr_baseline", "compression_ratio", "body_size", "body_ratio"]
    missing = [c for c in required_cols if c not in df.columns]
    has_values = df["atr"].dropna().shape[0] > 50
    return len(missing) == 0 and has_values, f"Missing cols: {missing}" if missing else f"ATR computed ({df['atr'].dropna().shape[0]} valid rows)"

def test_structure_features():
    from trader.features.structure import add_structure_features
    df = _make_test_df()
    df = add_structure_features(df)
    has_swings = df["swing_high"].any() or df["swing_low"].any()
    has_struct = "structure" in df.columns
    return has_swings and has_struct, f"Swings found: {df['swing_high'].sum()} highs, {df['swing_low'].sum()} lows"

# ═══════════════════════════════════════════════════════════
# 3. REGIME CLASSIFIER
# ═══════════════════════════════════════════════════════════
def test_regime_classifier():
    from trader.features.volatility import add_volatility_features
    from trader.features.structure import add_structure_features
    from trader.regime.classifier import classify_regime
    df = _make_test_df()
    df = add_volatility_features(df)
    df = add_structure_features(df)
    result = classify_regime(df, {"trend_threshold": 0.6, "volatility_compression_threshold": 0.5, "volatility_expansion_threshold": 1.5})
    valid = (
        "regime" in result and
        "confidence" in result and
        "reasons" in result and
        0 <= result["confidence"] <= 1
    )
    return valid, f"Regime={result['regime']}, Confidence={result['confidence']:.2f}"

# ═══════════════════════════════════════════════════════════
# 4. LIQUIDITY DETECTOR
# ═══════════════════════════════════════════════════════════
def test_liquidity_detector():
    from trader.features.volatility import add_volatility_features
    from trader.features.structure import add_structure_features, detect_displacement
    from trader.liquidity.detector import detect_liquidity_events
    df = _make_test_df()
    df = add_volatility_features(df)
    df = add_structure_features(df)
    df = detect_displacement(df)
    events = detect_liquidity_events(df, {"eqh_eql_threshold_points": 20, "sweep_lookback_bars": 50})
    # Events may or may not be generated depending on random data — that's fine
    return isinstance(events, list), f"Events detected: {len(events)}"

# ═══════════════════════════════════════════════════════════
# 5. RISK GATE — MANDATORY
# ═══════════════════════════════════════════════════════════
def test_risk_gate_blocks_no_sl():
    from trader.risk.gate import RiskEngine
    engine = RiskEngine()
    sig = {"symbol": "XAUUSD", "side": "BUY", "sl": None, "model": "TEST"}
    result = engine.risk_gate(sig, {"equity": 1000, "daily_pnl": 0, "consecutive_losses": 0}, {"spread": 10, "is_news": False})
    return not result["allowed"], f"Blocked reasons: {result['reasons']}"

def test_risk_gate_blocks_daily_loss():
    from trader.risk.gate import RiskEngine
    engine = RiskEngine()
    sig = {"symbol": "XAUUSD", "side": "BUY", "sl": 1990.0, "model": "TEST"}
    result = engine.risk_gate(sig, {"equity": 1000, "daily_pnl": -65, "consecutive_losses": 0}, {"spread": 10, "is_news": False})
    return not result["allowed"], f"Blocked reasons: {result['reasons']}"

def test_risk_gate_blocks_consecutive_losses():
    from trader.risk.gate import RiskEngine
    engine = RiskEngine()
    sig = {"symbol": "XAUUSD", "side": "BUY", "sl": 1990.0, "model": "TEST"}
    result = engine.risk_gate(sig, {"equity": 1000, "daily_pnl": 0, "consecutive_losses": 3}, {"spread": 10, "is_news": False})
    return not result["allowed"], f"Blocked reasons: {result['reasons']}"

def test_risk_gate_blocks_news():
    from trader.risk.gate import RiskEngine
    engine = RiskEngine()
    sig = {"symbol": "XAUUSD", "side": "BUY", "sl": 1990.0, "model": "TEST"}
    result = engine.risk_gate(sig, {"equity": 1000, "daily_pnl": 0, "consecutive_losses": 0}, {"spread": 10, "is_news": True})
    return not result["allowed"], f"Blocked reasons: {result['reasons']}"

def test_risk_gate_allows_valid():
    from trader.risk.gate import RiskEngine
    engine = RiskEngine()
    sig = {"symbol": "XAUUSD", "side": "BUY", "sl": 1990.0, "model": "TEST"}
    result = engine.risk_gate(sig, {"equity": 1000, "daily_pnl": 0, "consecutive_losses": 0}, {"spread": 10, "is_news": False})
    return result["allowed"], "Valid signal should pass"

# ═══════════════════════════════════════════════════════════
# 6. STRATEGY PIPELINE (signal generation)
# ═══════════════════════════════════════════════════════════
def test_strategy_selector():
    from trader.features.volatility import add_volatility_features
    from trader.features.structure import add_structure_features
    from trader.regime.classifier import classify_regime
    from trader.liquidity.detector import detect_liquidity_events
    from trader.strategy.selector import select_and_generate_signal
    df = _make_test_df()
    df = add_volatility_features(df)
    df = add_structure_features(df)
    regime_res = classify_regime(df, {"trend_threshold": 0.6, "volatility_compression_threshold": 0.5, "volatility_expansion_threshold": 1.5})
    events = detect_liquidity_events(df, {"eqh_eql_threshold_points": 20, "sweep_lookback_bars": 50})
    context = {"symbol": "XAUUSD", "regime_result": regime_res}
    sig = select_and_generate_signal(df, context, events)
    # Signal can be None (no trade) — that's valid behavior
    if sig is None:
        return True, f"No signal (regime={regime_res['regime']}) — valid no-trade"
    required = ["symbol", "side", "sl", "tp1", "model", "confidence"]
    missing = [k for k in required if k not in sig]
    return len(missing) == 0, f"Signal OK: {sig['side']} {sig['model']}, conf={sig['confidence']:.2f}"

# ═══════════════════════════════════════════════════════════
# 7. SQLITE STORAGE
# ═══════════════════════════════════════════════════════════
def test_sqlite_storage():
    from trader.storage.sqlite_db import DataStore
    import tempfile, os
    tmp = os.path.join(tempfile.gettempdir(), "opus_qc_test.db")
    store = DataStore(db_path=tmp)
    store.log_incident("XAUUSD", "QC Test incident", "INFO")
    store.record_signal({"symbol": "XAUUSD", "model": "TEST", "side": "BUY", "confidence": 0.8, "rationale": ["test"]})
    store.record_trade({"ticket": 9999, "symbol": "XAUUSD", "mode": "dry_run", "side": "BUY",
                         "entry_price": 2000, "sl": 1990, "tp1": 2010, "tp2": 2020, "tp3": 2030, "lot": 0.01})
    cursor = store.conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM trades")
    count = cursor.fetchone()[0]
    store.conn.close()
    os.remove(tmp)
    return count == 1, f"Trades recorded: {count}"

# ═══════════════════════════════════════════════════════════
# RUN ALL
# ═══════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("  OPUS QC Suite — Production Readiness Check")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print("\n[1/7] Config Validation")
    check("Config loads correctly", test_config_loads)
    check("Risk limits within safe range", test_risk_limits_valid)

    print("\n[2/7] Feature Engineering")
    check("Volatility features compute", test_volatility_features)
    check("Structure features compute", test_structure_features)

    print("\n[3/7] Regime Classifier")
    check("Regime classification works", test_regime_classifier)

    print("\n[4/7] Liquidity Detector")
    check("Liquidity event detection works", test_liquidity_detector)

    print("\n[5/7] Risk Gate (Survival)")
    check("Blocks signal without SL", test_risk_gate_blocks_no_sl)
    check("Blocks when daily loss exceeded", test_risk_gate_blocks_daily_loss)
    check("Blocks after 3 consecutive losses", test_risk_gate_blocks_consecutive_losses)
    check("Blocks during news window", test_risk_gate_blocks_news)
    check("Allows valid signal", test_risk_gate_allows_valid)

    print("\n[6/7] Strategy Pipeline")
    check("Full pipeline signal generation", test_strategy_selector)

    print("\n[7/7] SQLite Storage")
    check("SQLite CRUD operations", test_sqlite_storage)

    # Summary
    passed = sum(1 for r in results if r["status"] == PASS)
    total = len(results)
    pct = (passed / total * 100) if total > 0 else 0
    print("\n" + "=" * 60)
    print(f"  RESULT: {passed}/{total} passed ({pct:.0f}%)")
    if pct == 100:
        print("  🟢 QC PASSED — Ready for Backtest")
    else:
        print("  🔴 QC FAILED — Fix issues before proceeding")
    print("=" * 60)

    return pct == 100

if __name__ == "__main__":
    # Add project root to path
    sys.path.insert(0, "d:/VibeCode/Trade")
    success = main()
    sys.exit(0 if success else 1)
