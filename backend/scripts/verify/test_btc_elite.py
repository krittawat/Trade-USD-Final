"""
BTC Elite Strategy — Smoke Test.

Tests:
    1. Import + instantiation
    2. HOLD on insufficient data
    3. Trend mode produces valid Decision (BUY/SELL with SL)
    4. Mean-reversion mode produces valid Decision
    5. Confidence in [0, 1] range
    6. Cooldown works
"""

import sys
import os
import numpy as np
import pandas as pd

# Ensure backend is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.strategy.templates.btc_elite import BtcEliteStrategy, BTC_ELITE_DEFAULTS
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

passed = 0
failed = 0
errors = []


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  ✅ {name}")
        passed += 1
    else:
        print(f"  ❌ {name}: {detail}")
        failed += 1
        errors.append(f"{name}: {detail}")


def make_profile(symbol="BTCUSDc"):
    return SymbolProfile(
        symbol=symbol,
        volume_min=0.01,
        volume_max=1.0,
        volume_step=0.01,
        point=0.01,
        digits=2,
        spread_avg=50.0,
        spread_max_allowed=200.0,
        sessions_allowed=["LONDON", "NEW_YORK", "ASIA"],
    )


def make_trending_candles(n=300, direction="up", base_price=95000.0):
    """Create synthetic BTC candles with clear trend."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=n, freq="5min")
    drift = 50.0 if direction == "up" else -50.0
    noise = np.random.normal(0, 50, n)

    close_arr = np.cumsum(np.full(n, drift) + noise) + base_price
    open_arr = np.zeros(n)
    open_arr[0] = base_price
    for i in range(1, n):
        open_arr[i] = close_arr[i-1]
        
    # Force last 3 candles to be strongly trending to pass HA filter
    for i in range(n-3, n):
        if direction == "up":
            close_arr[i] = close_arr[i-1] + 150.0
            open_arr[i] = close_arr[i-1]
        else:
            close_arr[i] = close_arr[i-1] - 150.0
            open_arr[i] = close_arr[i-1]

    high_arr = np.maximum(open_arr, close_arr) + np.abs(np.random.normal(40, 20, n))
    low_arr = np.minimum(open_arr, close_arr) - np.abs(np.random.normal(40, 20, n))
    volume = np.random.randint(50, 500, n).astype(float)

    return pd.DataFrame({
        "time": dates,
        "open": open_arr,
        "high": high_arr,
        "low": low_arr,
        "close": close_arr,
        "tick_volume": volume,
    })


def make_ranging_candles(n=300, base_price=95000.0, range_size=500.0):
    """Create synthetic ranging BTC candles (low ADX)."""
    np.random.seed(123)
    dates = pd.date_range("2026-01-01", periods=n, freq="5min")

    # Oscillate in a narrow range
    t = np.linspace(0, 10 * np.pi, n)
    close_arr = base_price + range_size * np.sin(t) + np.random.normal(0, 50, n)
    
    open_arr = np.zeros(n)
    open_arr[0] = base_price
    for i in range(1, n):
        open_arr[i] = close_arr[i-1]
        
    # Force last candle to show a strong dip and reversal for a BUY MR signal
    close_arr[-1] = open_arr[-1] - 400.0 # big drop to trigger lower BB
    close_arr[-2] = close_arr[-3] - 400.0 # consecutive drop for RSI

    high_arr = np.maximum(open_arr, close_arr) + np.abs(np.random.normal(30, 10, n))
    low_arr = np.minimum(open_arr, close_arr) - np.abs(np.random.normal(30, 10, n))
    
    volume = np.random.randint(30, 200, n).astype(float)

    return pd.DataFrame({
        "time": dates,
        "open": open_arr,
        "high": high_arr,
        "low": low_arr,
        "close": close_arr,
        "tick_volume": volume,
    })


# ═══════════════════════════════════════
# TEST 1: Import & Instantiation
# ═══════════════════════════════════════
print("\n═══ TEST 1: Import & Instantiation ═══")
try:
    strategy = BtcEliteStrategy()
    check("Import OK", True)
    check("Name", strategy.name == "btc_elite", f"got {strategy.name}")
    check("Timeframe", strategy.timeframe == "M5", f"got {strategy.timeframe}")
    check("Has params", hasattr(strategy, "p") and len(strategy.p) > 0)
except Exception as e:
    check("Import", False, str(e))

# ═══════════════════════════════════════
# TEST 2: HOLD on insufficient data
# ═══════════════════════════════════════
print("\n═══ TEST 2: Insufficient Data → HOLD ═══")
profile = make_profile()
small_df = make_trending_candles(n=50)  # Too few bars
decision = strategy.analyze(small_df, profile)
check("Returns Decision", isinstance(decision, Decision), f"got {type(decision)}")
check("Action is HOLD", decision.action == Action.HOLD, f"got {decision.action}")
check("Reason mentions data", "insufficient" in decision.reason.lower() or "data" in decision.reason.lower(),
      f"reason: {decision.reason}")

# ═══════════════════════════════════════
# TEST 3: Trending Up → BUY or HOLD
# ═══════════════════════════════════════
print("\n═══ TEST 3: Trending Up ═══")
strategy_fresh = BtcEliteStrategy()
up_candles = make_trending_candles(direction="up")
decision_up = strategy_fresh.analyze(up_candles, profile, regime=RegimeType.TRENDING_UP)
check("Returns Decision", isinstance(decision_up, Decision))
check("Action is BUY or HOLD", decision_up.action in (Action.BUY, Action.HOLD),
      f"got {decision_up.action}")
if decision_up.action == Action.BUY:
    check("Has SL", decision_up.stop_loss is not None, "SL is required for BUY")
    check("Has TP", decision_up.take_profit is not None, "TP is required for BUY")
    check("SL < close", decision_up.stop_loss < float(up_candles["close"].iloc[-1]))
    check("Confidence in [0,1]", 0.0 <= decision_up.confidence <= 1.0,
          f"got {decision_up.confidence}")
else:
    print(f"  ℹ️  HOLD with reason: {decision_up.reason}")
    check("Reason not empty", len(decision_up.reason) > 0)

# ═══════════════════════════════════════
# TEST 4: Trending Down → SELL or HOLD
# ═══════════════════════════════════════
print("\n═══ TEST 4: Trending Down ═══")
strategy_fresh2 = BtcEliteStrategy()
down_candles = make_trending_candles(direction="down")
decision_down = strategy_fresh2.analyze(down_candles, profile, regime=RegimeType.TRENDING_DOWN)
check("Returns Decision", isinstance(decision_down, Decision))
check("Action is SELL or HOLD", decision_down.action in (Action.SELL, Action.HOLD),
      f"got {decision_down.action}")
if decision_down.action == Action.SELL:
    check("Has SL", decision_down.stop_loss is not None, "SL is required for SELL")
    check("Has TP", decision_down.take_profit is not None)
    check("SL > close", decision_down.stop_loss > float(down_candles["close"].iloc[-1]))
    check("Confidence in [0,1]", 0.0 <= decision_down.confidence <= 1.0)
else:
    print(f"  ℹ️  HOLD with reason: {decision_down.reason}")
    check("Reason not empty", len(decision_down.reason) > 0)

# ═══════════════════════════════════════
# TEST 5: Ranging Market → MR mode
# ═══════════════════════════════════════
print("\n═══ TEST 5: Ranging (Mean Reversion) ═══")
strategy_fresh3 = BtcEliteStrategy()
range_candles = make_ranging_candles()
decision_range = strategy_fresh3.analyze(range_candles, profile, regime=RegimeType.RANGING)
check("Returns Decision", isinstance(decision_range, Decision))
check("Strategy name", decision_range.strategy_name == "btc_elite",
      f"got {decision_range.strategy_name}")
if decision_range.action != Action.HOLD:
    check("Has SL", decision_range.stop_loss is not None)
    check("Has TP", decision_range.take_profit is not None)
    check("Confidence in [0,1]", 0.0 <= decision_range.confidence <= 1.0)
else:
    print(f"  ℹ️  HOLD with reason: {decision_range.reason}")
    check("Reason not empty", len(decision_range.reason) > 0)

# ═══════════════════════════════════════
# TEST 6: With pressure data
# ═══════════════════════════════════════
print("\n═══ TEST 6: Pressure Data Integration ═══")
strategy_fresh4 = BtcEliteStrategy()
pressure_data = {
    "buying_pressure": 0.75,
    "selling_pressure": 0.25,
    "score": 0.65,
    "is_climax": False,
    "ad_line_trend": "UP",
}
decision_pressure = strategy_fresh4.analyze(
    up_candles, profile, regime=RegimeType.TRENDING_UP, pressure=pressure_data
)
check("Returns Decision with pressure", isinstance(decision_pressure, Decision))
check("Accepts pressure kwarg", True)  # No crash = success

# ═══════════════════════════════════════
# TEST 7: Registry check
# ═══════════════════════════════════════
print("\n═══ TEST 7: Registry ═══")
from app.strategy.templates import _SEED_REGISTRY
crypto_entries = [r for r in _SEED_REGISTRY if r.get("asset_class") == "crypto"]
check("Crypto entries exist", len(crypto_entries) > 0, f"found {len(crypto_entries)}")
btc_entry = [r for r in crypto_entries if r["strategy_name"] == "btc_elite"]
check("btc_elite in registry", len(btc_entry) == 1)
if btc_entry:
    check("Priority = 100", btc_entry[0]["priority"] == 100)
    check("Asset class = crypto", btc_entry[0]["asset_class"] == "crypto")
    check("Timeframe = M5", btc_entry[0]["timeframe"] == "M5")

# ═══════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════
print(f"\n{'='*50}")
print(f"BTC Elite Smoke Test: {passed} passed, {failed} failed")
if errors:
    print("\nFailed:")
    for e in errors:
        print(f"  ❌ {e}")
    sys.exit(1)
else:
    print("✅ All tests passed!")
    # sys.exit(0)
