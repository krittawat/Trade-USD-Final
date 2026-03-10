"""
Verify: Regime Filter — ทดสอบ NO-TRADE filter.

ทดสอบ 4 ข้อ:
    1. ADX < 18 → NO-TRADE
    2. EMA compression → NO-TRADE
    3. ATR < ATR_MA → NO-TRADE
    4. Trending market ADX > 25 → TRADE allowed

วิธีรัน:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/verify/verify_regime_filter.py
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

import numpy as np
import pandas as pd

passed = 0
failed = 0


def check(name: str, result: bool, detail: str = ""):
    global passed, failed
    if result:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}: {detail}")


def make_candles(
    n: int = 200,
    base: float = 2000.0,
    trend: float = 0.0,
    noise: float = 1.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Create synthetic OHLC candles."""
    np.random.seed(seed)
    returns = np.random.randn(n) * noise + trend
    close = base + np.cumsum(returns)
    high = close + abs(np.random.randn(n)) * noise * 2
    low = close - abs(np.random.randn(n)) * noise * 2

    return pd.DataFrame({
        "open": close - returns * 0.5,
        "high": high,
        "low": low,
        "close": close,
    })


print("=" * 60)
print("🔒 Verify: Regime Filter")
print("=" * 60)

from app.risk.regime_filter import RegimeFilter

# ─── Test 1: Flat market (ADX < 18) → NO-TRADE ───
print("\n[1/4] Flat market (tiny noise) → NO-TRADE")
try:
    rf = RegimeFilter(enabled=True, adx_min=18.0, range_threshold_pct=0.5)

    flat = make_candles(n=200, base=2000.0, trend=0.0, noise=0.1)
    result = rf.check(flat)
    check("Flat market → NOT tradable", not result.tradable,
          f"tradable={result.tradable}, reason={result.reason}")
    if result.details:
        print(f"     Details: {result.details}")

except Exception as e:
    check("Flat market test", False, str(e))


# ─── Test 2: EMA compression → NO-TRADE ───
print("\n[2/4] EMA compression (very tight range) → NO-TRADE")
try:
    rf2 = RegimeFilter(enabled=True, ema_compression_pct=0.1)

    # Extremely compressed EMAs (micro noise)
    compressed = make_candles(n=200, base=2000.0, trend=0.0, noise=0.05)
    result2 = rf2.check(compressed)
    check("EMA compressed → NOT tradable", not result2.tradable,
          f"tradable={result2.tradable}, reason={result2.reason}")

except Exception as e:
    check("EMA compression test", False, str(e))


# ─── Test 3: ATR < ATR_MA → NO-TRADE ───
print("\n[3/4] Declining volatility (ATR dropping) → NO-TRADE")
try:
    rf3 = RegimeFilter(enabled=True)

    # High vol → low vol transition: ATR_current < ATR_MA
    np.random.seed(99)
    n = 200
    # First half: high volatility, second half: very low
    noise1 = np.random.randn(100) * 5.0
    noise2 = np.random.randn(100) * 0.3
    returns = np.concatenate([noise1, noise2])
    close = 2000.0 + np.cumsum(returns)
    high = close + abs(np.concatenate([np.random.randn(100) * 5, np.random.randn(100) * 0.2]))
    low = close - abs(np.concatenate([np.random.randn(100) * 5, np.random.randn(100) * 0.2]))

    declining = pd.DataFrame({
        "open": close - returns * 0.3,
        "high": high, "low": low, "close": close,
    })

    result3 = rf3.check(declining)
    # After vol drops, current ATR should be < ATR_MA
    check("Declining vol → NOT tradable", not result3.tradable,
          f"tradable={result3.tradable}, reason={result3.reason}")

except Exception as e:
    check("ATR declining test", False, str(e))


# ─── Test 4: Strong trend → tradable ───
print("\n[4/4] Strong uptrend → TRADE allowed")
try:
    rf4 = RegimeFilter(
        enabled=True, adx_min=18.0,
        ema_compression_pct=0.05, range_threshold_pct=0.3,
    )

    # Strong persistent trend with EXPANDING volatility
    # ATR(14) must be > ATR_MA(50) → recent bars must be wider than average
    np.random.seed(7)
    n = 200
    # Increasing bar range: starts small, grows steadily
    bar_width = np.linspace(5, 25, n)  # bar range grows from 5 to 25
    close = 2000.0 + np.cumsum(np.ones(n) * 1.0)  # steady uptrend
    high = close + bar_width * 0.6
    low = close - bar_width * 0.4

    trending = pd.DataFrame({
        "open": close - 0.5,
        "high": high,
        "low": low,
        "close": close,
    })
    result4 = rf4.check(trending)
    check("Strong trend → tradable", result4.tradable,
          f"tradable={result4.tradable}, reason={result4.reason}")
    if result4.details:
        print(f"     Details: {result4.details}")

except Exception as e:
    check("Strong trend test", False, str(e))


# ─── Test 5: Disabled → always tradable ───
print("\n[BONUS] Disabled filter → always tradable")
try:
    rf_off = RegimeFilter(enabled=False)
    flat2 = make_candles(n=200, base=2000.0, trend=0.0, noise=0.01)
    result5 = rf_off.check(flat2)
    check("Disabled → tradable", result5.tradable)
except Exception as e:
    check("Disabled test", False, str(e))


# ─── Summary ───
print("\n" + "=" * 60)
total = passed + failed
print(f"📊 ผลลัพธ์: {passed}/{total} ผ่าน ({passed/total*100:.0f}%)")
if failed == 0:
    print("🎉 ALL PASSED — Regime Filter ทำงานถูกต้อง")
else:
    print(f"⚠️ FAILED: {failed} ข้อ")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
