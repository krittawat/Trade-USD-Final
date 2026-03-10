"""
OPUS Ghost Protocol — Verification Suite.

ทดสอบทุก module ของ OPUS ด้วย synthetic data:
    1. Enums — new RegimeType + BlockReason values
    2. OPUS Regime Engine — classification + confidence scoring
    3. Liquidity Hunter — sweep/displacement/re-acceptance detection
    4. Kill Switch — all 7 disable conditions
    5. Strategy imports — OpusLiquidityHunter + OpusTrendKiller

Usage:
    python scripts/verify/test_opus_system.py
"""

import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

passed = 0
failed = 0
errors = []


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed, errors
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        msg = f"  ❌ {name}: {detail}"
        print(msg)
        errors.append(msg)


def make_candles(n: int = 200, base_price: float = 2000.0,
                 volatility: float = 5.0, trend: float = 0.0) -> pd.DataFrame:
    """Generate synthetic OHLCV candles."""
    np.random.seed(42)
    closes = [base_price]
    for i in range(1, n):
        change = np.random.normal(trend, volatility)
        closes.append(closes[-1] + change)

    closes = np.array(closes)
    highs = closes + np.abs(np.random.normal(2, 1, n))
    lows = closes - np.abs(np.random.normal(2, 1, n))
    opens = closes + np.random.normal(0, 1, n)
    volumes = np.random.randint(100, 1000, n).astype(float)
    times = pd.date_range("2025-01-01", periods=n, freq="5min")

    return pd.DataFrame({
        "time": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "tick_volume": volumes,
    })


# ═══════════════════════════════════════════════════════════════════
# TEST 1: Enums
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 1: Enums — New OPUS values")
print("=" * 60)

try:
    from app.domain.enums import RegimeType, BlockReason

    check("RegimeType.VOL_EXPANSION exists",
          hasattr(RegimeType, "VOL_EXPANSION"),
          "Missing VOL_EXPANSION")
    check("RegimeType.VOL_COMPRESSION exists",
          hasattr(RegimeType, "VOL_COMPRESSION"),
          "Missing VOL_COMPRESSION")
    check("RegimeType.DISTRIBUTION exists",
          hasattr(RegimeType, "DISTRIBUTION"),
          "Missing DISTRIBUTION")
    check("BlockReason.OPUS_KILL_SWITCH exists",
          hasattr(BlockReason, "OPUS_KILL_SWITCH"),
          "Missing OPUS_KILL_SWITCH")
    check("VOL_EXPANSION value correct",
          RegimeType.VOL_EXPANSION.value == "VOL_EXPANSION")
    check("OPUS_KILL_SWITCH value correct",
          BlockReason.OPUS_KILL_SWITCH.value == "OPUS_KILL_SWITCH")
except Exception as e:
    check("Enums import", False, str(e))


# ═══════════════════════════════════════════════════════════════════
# TEST 2: OPUS Regime Engine
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 2: OPUS Regime Engine")
print("=" * 60)

try:
    from app.brain.opus_regime import (
        classify_opus_regime, OpusRegimeResult, _detect_swings
    )

    # Test with insufficient data
    short_df = make_candles(n=10)
    result = classify_opus_regime(short_df)
    check("Insufficient data → UNKNOWN",
          result.regime == RegimeType.UNKNOWN,
          f"Got {result.regime}")
    check("Insufficient data → not actionable",
          not result.actionable)

    # Test with normal data
    normal_df = make_candles(n=200, trend=0.5, volatility=3.0)
    result = classify_opus_regime(normal_df)
    check("Normal data → valid regime",
          result.regime != RegimeType.UNKNOWN,
          f"Got {result.regime}")
    check("Confidence in [0, 1]",
          0.0 <= result.confidence <= 1.0,
          f"Got {result.confidence}")
    check("OpusRegimeResult has structure",
          result.structure in ("BULLISH", "BEARISH", "UNCLEAR"),
          f"Got {result.structure}")
    check("OpusRegimeResult has atr_expansion_factor",
          result.atr_expansion_factor > 0,
          f"Got {result.atr_expansion_factor}")
    check("OpusRegimeResult has details dict",
          isinstance(result.details, dict) and len(result.details) > 0)

    # Test to_regime_context conversion
    ctx = result.to_regime_context()
    check("to_regime_context() works",
          ctx.regime == result.regime and ctx.score == result.confidence)

    # Test swing detection
    high = normal_df["high"].values.astype(float)
    low = normal_df["low"].values.astype(float)
    swings = _detect_swings(high, low, lookback=30)
    check("Swing detection returns dict",
          isinstance(swings, dict) and "swing_highs" in swings)
    check("Swing clarity_score in [0, 1]",
          0.0 <= swings["clarity_score"] <= 1.0)

    # Test with high-volatility data
    volatile_df = make_candles(n=200, volatility=20.0)
    result_vol = classify_opus_regime(volatile_df)
    check("High-vol data classified",
          result_vol.regime != RegimeType.UNKNOWN,
          f"Got {result_vol.regime}")

    print(f"\n  📊 Normal regime: {result.regime.value} "
          f"(conf={result.confidence}, structure={result.structure})")
    print(f"  📊 Volatile regime: {result_vol.regime.value} "
          f"(conf={result_vol.confidence})")

except Exception as e:
    import traceback
    check("OPUS Regime Engine import/test", False, str(e))
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════
# TEST 3: Liquidity Hunter
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 3: Liquidity Hunter")
print("=" * 60)

try:
    from app.brain.liquidity_hunter import (
        LiquidityHunter, LiquiditySignal, LiquidityZone
    )

    hunter = LiquidityHunter()

    # Test with normal data
    normal_df = make_candles(n=200)
    signal = hunter.scan(normal_df, session="LONDON")
    check("LiquiditySignal returned",
          isinstance(signal, LiquiditySignal))
    check("Signal has liquidity_zones list",
          isinstance(signal.liquidity_zones, list))
    check("Signal confidence in [0, 1]",
          0.0 <= signal.confidence <= 1.0,
          f"Got {signal.confidence}")
    check("Signal sweep_direction valid",
          signal.sweep_direction in ("BUY", "SELL", "NONE"),
          f"Got {signal.sweep_direction}")
    check("Signal trap_type valid",
          signal.trap_type in ("SESSION_TRAP", "NEWS_SPIKE", "NONE"),
          f"Got {signal.trap_type}")

    # Test with insufficient data
    short_signal = hunter.scan(make_candles(n=10))
    check("Short data → reasons include insufficient",
          any("Insufficient" in r for r in short_signal.reasons))

    # Test zone detection count
    zones_count = len(signal.liquidity_zones)
    check("Found ≥ 1 liquidity zone",
          zones_count >= 1,
          f"Found {zones_count} zones")

    print(f"\n  📊 Zones found: {zones_count}")
    print(f"  📊 Sweep: {signal.sweep_detected} ({signal.sweep_direction})")
    print(f"  📊 Confidence: {signal.confidence}")
    for z in signal.liquidity_zones[:3]:
        print(f"     Zone: {z.zone_type} @ {z.price:.2f} (touches={z.touches})")

except Exception as e:
    import traceback
    check("Liquidity Hunter import/test", False, str(e))
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════
# TEST 4: Kill Switch
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 4: OPUS Kill Switch")
print("=" * 60)

try:
    from app.risk.opus_kill_switch import OpusKillSwitch, KillSwitchResult

    ks = OpusKillSwitch(
        max_consecutive_losses=3,
        max_daily_dd_pct=0.06,
        daily_target_usd=20.0,
        spread_max_points=50,
    )

    now = datetime(2026, 3, 1, 14, 0, 0, tzinfo=timezone.utc)

    # Test 1: All clear
    r = ks.evaluate(consecutive_losses=0, daily_pl_pct=0.01, now=now)
    check("All clear → not blocked", not r.blocked)

    # Test 2: Consecutive losses
    r = ks.evaluate(consecutive_losses=3, now=now)
    check("3 consecutive losses → blocked",
          r.blocked and r.block_type == "LOSS_STREAK",
          f"blocked={r.blocked}, type={r.block_type}")

    # Test 3: Daily DD
    r = ks.evaluate(daily_pl_pct=-0.07, now=now)
    check("Daily DD -7% → blocked",
          r.blocked and r.block_type == "DAILY_DD",
          f"blocked={r.blocked}, type={r.block_type}")

    # Test 4: Extreme ATR
    r = ks.evaluate(atr_current=10.0, atr_baseline=4.0, now=now)
    check("ATR 2.5x → blocked",
          r.blocked and r.block_type == "ATR_EXTREME",
          f"blocked={r.blocked}, type={r.block_type}")

    # Test 5: Spread explosion
    r = ks.evaluate(spread_points=60, now=now)
    check("Spread 60 > 50 → blocked",
          r.blocked and r.block_type == "SPREAD",
          f"blocked={r.blocked}, type={r.block_type}")

    # Test 6: Daily target hit
    r = ks.evaluate(daily_pl_usd=25.0, now=now)
    check("Daily target $25 ≥ $20 → blocked",
          r.blocked and r.block_type == "TARGET_HIT",
          f"blocked={r.blocked}, type={r.block_type}")

    # Test 7: Low liquidity for metals
    late_now = datetime(2026, 3, 1, 21, 0, 0, tzinfo=timezone.utc)
    r = ks.evaluate(symbol="XAUUSDc", now=late_now)
    check("21:00 UTC + XAU → low liq blocked",
          r.blocked and r.block_type == "LOW_LIQ",
          f"blocked={r.blocked}, type={r.block_type}")

    # Test 8: Low liquidity does NOT block BTC
    r = ks.evaluate(symbol="BTCUSDc", now=late_now)
    check("21:00 UTC + BTC → NOT blocked",
          not r.blocked,
          f"blocked={r.blocked}")

    # Test 9: News spike
    r = ks.evaluate(has_news_spike=True, now=now)
    check("News spike → blocked",
          r.blocked and r.block_type == "NEWS_SPIKE",
          f"blocked={r.blocked}, type={r.block_type}")

    # Test 10: News spike expires
    future_now = now + timedelta(minutes=20)
    r = ks.evaluate(now=future_now)
    check("News spike expired after 20min → not blocked",
          not r.blocked,
          f"blocked={r.blocked}")

except Exception as e:
    import traceback
    check("Kill Switch import/test", False, str(e))
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════
# TEST 5: Strategy Imports
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 5: Strategy Imports")
print("=" * 60)

try:
    from app.strategy.templates.opus_liquidity_hunter import OpusLiquidityHunterStrategy
    from app.strategy.templates.opus_trend_killer import OpusTrendKillerStrategy
    from app.strategy.base import BaseStrategy

    strat_a = OpusLiquidityHunterStrategy()
    strat_b = OpusTrendKillerStrategy()

    check("OpusLiquidityHunterStrategy instantiates", strat_a is not None)
    check("OpusTrendKillerStrategy instantiates", strat_b is not None)
    check("Model A is BaseStrategy",
          isinstance(strat_a, BaseStrategy))
    check("Model B is BaseStrategy",
          isinstance(strat_b, BaseStrategy))
    check("Model A name correct",
          strat_a.name == "opus_liquidity_hunter",
          f"Got '{strat_a.name}'")
    check("Model B name correct",
          strat_b.name == "opus_trend_killer",
          f"Got '{strat_b.name}'")
    check("Model A timeframe M5",
          strat_a.timeframe == "M5")
    check("Model B timeframe M5",
          strat_b.timeframe == "M5")

    # Test with synthetic data (should return HOLD due to no real patterns)
    from app.domain.models import SymbolProfile
    profile = SymbolProfile(symbol="XAUUSDc", digits=2)
    candles = make_candles(n=200)

    decision_a = strat_a.analyze(candles, profile)
    check("Model A returns Decision", decision_a is not None)
    check("Model A Decision has strategy_name",
          decision_a.strategy_name == "opus_liquidity_hunter",
          f"Got '{decision_a.strategy_name}'")

    decision_b = strat_b.analyze(candles, profile)
    check("Model B returns Decision", decision_b is not None)
    check("Model B Decision has strategy_name",
          decision_b.strategy_name == "opus_trend_killer",
          f"Got '{decision_b.strategy_name}'")

    print(f"\n  📊 Model A decision: {decision_a.action.value} — {decision_a.reason[:80]}")
    print(f"  📊 Model B decision: {decision_b.action.value} — {decision_b.reason[:80]}")

except Exception as e:
    import traceback
    check("Strategy imports/test", False, str(e))
    traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════
# TEST 6: Seed Registry
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 6: Seed Registry")
print("=" * 60)

try:
    from app.strategy.templates import _SEED_REGISTRY

    opus_entries = [s for s in _SEED_REGISTRY if "opus" in s["strategy_name"]]
    check("OPUS strategies in seed registry",
          len(opus_entries) == 2,
          f"Found {len(opus_entries)}")

    for entry in opus_entries:
        check(f"Registry entry '{entry['strategy_name']}' has priority",
              entry.get("priority", 0) >= 120,
              f"priority={entry.get('priority')}")

except Exception as e:
    check("Seed Registry test", False, str(e))


# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
total = passed + failed
print(f"  OPUS Ghost Protocol QC: {passed}/{total} passed, {failed} failed")
print("=" * 60)

if errors:
    print("\n❌ Failures:")
    for e in errors:
        print(e)

sys.exit(0 if failed == 0 else 1)
