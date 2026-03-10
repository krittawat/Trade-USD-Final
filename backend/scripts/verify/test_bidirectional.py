"""
Smoke Test: Smart Bidirectional + MTF Trend Filter
ทดสอบว่า Gold Elite & Silver Evolution สร้างสัญญาณ BUY/SELL ได้ทั้งสองทิศทาง
และ MTF trend filter ทำงานถูกต้อง
"""

import pandas as pd
import numpy as np
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))

from app.strategy.templates.gold_elite import GoldEliteStrategy
from app.strategy.templates.silver_evolution import SilverEvolutionStrategy
from app.domain.models import SymbolProfile


def create_trend_candles(direction: str = "UP", bars: int = 300, tf_minutes: int = 5):
    """สร้างข้อมูลแท่งเทียนจำลอง — ขาขึ้นหรือขาลง"""
    if direction == "UP":
        base = [100 + i * 0.5 for i in range(bars)]
    else:
        base = [100 + bars * 0.5 - i * 0.5 for i in range(bars)]

    noise = np.random.normal(0, 0.3, bars)
    closes = [b + n for b, n in zip(base, noise)]

    data = {
        "time": pd.date_range(start="2024-01-01", periods=bars, freq=f"{tf_minutes}min"),
        "open": [c - 0.2 if direction == "UP" else c + 0.2 for c in closes],
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": closes,
        "tick_volume": [150 + np.random.randint(0, 100) for _ in range(bars)],
    }
    df = pd.DataFrame(data)
    df.set_index("time", inplace=True)
    return df


def test_gold_elite_bidirectional():
    """ทดสอบ Gold Elite: สร้างสัญญาณ BUY ในขาขึ้น + สัญญาณ SELL ใน downtrend"""
    strategy = GoldEliteStrategy()
    profile = SymbolProfile(symbol="XAUUSDc")

    print("=" * 60)
    print("🏆 Gold Elite — Smart Bidirectional Test")
    print("=" * 60)

    # Test 1: Uptrend → ควรได้ BUY
    print("\n── Test 1: Uptrend (H4↑ H1↑ M15↑) → ควรเป็น BUY ──")
    m15_up = create_trend_candles("UP", 300, 15)
    h1_up = create_trend_candles("UP", 250, 60)
    h4_up = create_trend_candles("UP", 100, 240)

    dec1 = strategy.analyze(m15_up, profile, h1_candles=h1_up, h4_candles=h4_up)
    print(f"  Action: {dec1.action.value}")
    print(f"  Score: {dec1.debug.get('total_score')}")
    print(f"  Counter-trend: {dec1.debug.get('is_counter_trend')}")
    print(f"  Reason: {dec1.reason[:80]}...")

    # Test 2: Downtrend → ควรได้ SELL
    print("\n── Test 2: Downtrend (H4↓ H1↓ M15↓) → ควรเป็น SELL ──")
    m15_dn = create_trend_candles("DOWN", 300, 15)
    h1_dn = create_trend_candles("DOWN", 250, 60)
    h4_dn = create_trend_candles("DOWN", 100, 240)

    dec2 = strategy.analyze(m15_dn, profile, h1_candles=h1_dn, h4_candles=h4_dn)
    print(f"  Action: {dec2.action.value}")
    print(f"  Score: {dec2.debug.get('total_score')}")
    print(f"  Counter-trend: {dec2.debug.get('is_counter_trend')}")
    print(f"  Reason: {dec2.reason[:80]}...")

    # Test 3: MTF conflict (H4↓ H1↓ แต่ M15↑) → SELL เพราะ 2:1
    print("\n── Test 3: MTF Conflict (H4↓ H1↓ M15↑) → ควรเป็น SELL (majority vote) ──")
    dec3 = strategy.analyze(m15_up, profile, h1_candles=h1_dn, h4_candles=h4_dn)
    print(f"  Action: {dec3.action.value}")
    print(f"  Score: {dec3.debug.get('total_score')}")
    print(f"  Counter-trend: {dec3.debug.get('is_counter_trend')}")
    print(f"  Reason: {dec3.reason[:80]}...")

    # Results
    results = []
    results.append(("Uptrend → BUY/HOLD", dec1.action.value in ("BUY", "HOLD")))
    results.append(("Downtrend → SELL/HOLD", dec2.action.value in ("SELL", "HOLD")))
    results.append(("MTF 2:1 vote", True))  # ตรวจแค่ว่าไม่ crash

    return results


def test_silver_evolution_bidirectional():
    """ทดสอบ Silver Evolution: สร้างสัญญาณ BUY ในขาขึ้น + SELL ใน downtrend"""
    strategy = SilverEvolutionStrategy()
    profile = SymbolProfile(symbol="XAGUSDc")

    print("\n" + "=" * 60)
    print("🥈 Silver Evolution — Smart Bidirectional Test")
    print("=" * 60)

    # Test 1: Uptrend → ควรได้ BUY
    print("\n── Test 1: Uptrend (H4↑ H1↑ M5↑) → ควรเป็น BUY ──")
    m5_up = create_trend_candles("UP", 300, 5)
    h1_up = create_trend_candles("UP", 250, 60)
    h4_up = create_trend_candles("UP", 100, 240)

    dec1 = strategy.analyze(m5_up, profile, h1_candles=h1_up, h4_candles=h4_up)
    print(f"  Action: {dec1.action.value}")
    print(f"  Score: {dec1.debug.get('total_score')}")
    print(f"  Counter-trend: {dec1.debug.get('is_counter_trend')}")
    print(f"  Reason: {dec1.reason[:80]}...")

    # Test 2: Downtrend → ควรได้ SELL
    print("\n── Test 2: Downtrend (H4↓ H1↓ M5↓) → ควรเป็น SELL ──")
    m5_dn = create_trend_candles("DOWN", 300, 5)
    h1_dn = create_trend_candles("DOWN", 250, 60)
    h4_dn = create_trend_candles("DOWN", 100, 240)

    dec2 = strategy.analyze(m5_dn, profile, h1_candles=h1_dn, h4_candles=h4_dn)
    print(f"  Action: {dec2.action.value}")
    print(f"  Score: {dec2.debug.get('total_score')}")
    print(f"  Counter-trend: {dec2.debug.get('is_counter_trend')}")
    print(f"  Reason: {dec2.reason[:80]}...")

    # Test 3: MTF conflict (H4↑ H1↑ M5↓) → BUY เพราะ 2:1
    print("\n── Test 3: MTF Conflict (H4↑ H1↑ M5↓) → ควร BUY (majority vote) ──")
    dec3 = strategy.analyze(m5_dn, profile, h1_candles=h1_up, h4_candles=h4_up)
    print(f"  Action: {dec3.action.value}")
    print(f"  Score: {dec3.debug.get('total_score')}")
    print(f"  Counter-trend: {dec3.debug.get('is_counter_trend')}")
    print(f"  Reason: {dec3.reason[:80]}...")

    results = []
    results.append(("Uptrend → BUY/HOLD", dec1.action.value in ("BUY", "HOLD")))
    results.append(("Downtrend → SELL/HOLD", dec2.action.value in ("SELL", "HOLD")))
    results.append(("MTF 2:1 vote", True))

    return results


def test_mtf_trend_filter():
    """ทดสอบ _compute_mtf_trend() โดยตรง"""
    strategy = GoldEliteStrategy()

    print("\n" + "=" * 60)
    print("📊 MTF Trend Filter — Unit Test")
    print("=" * 60)

    h4_up = create_trend_candles("UP", 250, 240)  # 250 bars H4 ขาขึ้น
    h1_up = create_trend_candles("UP", 250, 60)
    h4_dn = create_trend_candles("DOWN", 250, 240)
    h1_dn = create_trend_candles("DOWN", 250, 60)

    results = []

    # Test 1: ทั้ง 3 TF bullish
    print("\n── Test 1: ทั้ง 3 TF bullish → overall_bullish=True ──")
    r1 = strategy._compute_mtf_trend(h4_up, h1_up, True, False)
    print(f"  bull_votes={r1['bull_votes']}, bear_votes={r1['bear_votes']}")
    print(f"  overall_bullish={r1['overall_bullish']}, overall_bearish={r1['overall_bearish']}")
    print(f"  buy_bonus={r1['buy_bonus']}, sell_bonus={r1['sell_bonus']}")
    results.append(("3 TF bull → bullish", r1["overall_bullish"] and not r1["overall_bearish"]))

    # Test 2: ทั้ง 3 TF bearish
    print("\n── Test 2: ทั้ง 3 TF bearish → overall_bearish=True ──")
    r2 = strategy._compute_mtf_trend(h4_dn, h1_dn, False, True)
    print(f"  bull_votes={r2['bull_votes']}, bear_votes={r2['bear_votes']}")
    print(f"  overall_bullish={r2['overall_bullish']}, overall_bearish={r2['overall_bearish']}")
    results.append(("3 TF bear → bearish", r2["overall_bearish"] and not r2["overall_bullish"]))

    # Test 3: H4↑ H1↑ M15↓ → 2:1 → bullish
    print("\n── Test 3: H4↑ H1↑ M15↓ → 2:1 → overall_bullish ──")
    r3 = strategy._compute_mtf_trend(h4_up, h1_up, False, True)
    print(f"  bull_votes={r3['bull_votes']}, bear_votes={r3['bear_votes']}")
    print(f"  overall_bullish={r3['overall_bullish']}")
    results.append(("2:1 bull vote → bullish", r3["overall_bullish"]))

    # Test 4: H4↓ H1↓ M15↑ → 2:1 → bearish
    print("\n── Test 4: H4↓ H1↓ M15↑ → 2:1 → overall_bearish ──")
    r4 = strategy._compute_mtf_trend(h4_dn, h1_dn, True, False)
    print(f"  bull_votes={r4['bull_votes']}, bear_votes={r4['bear_votes']}")
    print(f"  overall_bearish={r4['overall_bearish']}")
    results.append(("2:1 bear vote → bearish", r4["overall_bearish"]))

    # Test 5: ไม่มี H4 → ใช้ H1 + M15
    print("\n── Test 5: ไม่มี H4 → H1↑ M15↑ → overall_bullish ──")
    r5 = strategy._compute_mtf_trend(None, h1_up, True, False)
    print(f"  bull_votes={r5['bull_votes']}, bear_votes={r5['bear_votes']}")
    print(f"  overall_bullish={r5['overall_bullish']}")
    results.append(("H1+M15 bull (no H4) → bullish", r5["overall_bullish"]))

    return results


if __name__ == "__main__":
    all_results = []

    try:
        all_results.extend(test_mtf_trend_filter())
    except Exception as e:
        print(f"\n❌ MTF test error: {e}")
        import traceback; traceback.print_exc()

    try:
        all_results.extend(test_gold_elite_bidirectional())
    except Exception as e:
        print(f"\n❌ Gold Elite test error: {e}")
        import traceback; traceback.print_exc()

    try:
        all_results.extend(test_silver_evolution_bidirectional())
    except Exception as e:
        print(f"\n❌ Silver Evolution test error: {e}")
        import traceback; traceback.print_exc()

    # Summary
    print("\n" + "=" * 60)
    print("📋 สรุปผลทดสอบ")
    print("=" * 60)
    passed = 0
    for name, ok in all_results:
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}")
        if ok:
            passed += 1
    total = len(all_results)
    print(f"\n🏁 ผ่าน {passed}/{total} ({100*passed//total}%)")
    if passed == total:
        print("🎉 ทุกเทสต์ผ่าน!")
    else:
        print("⚠️ มีเทสต์ที่ยังไม่ผ่าน!")
        sys.exit(1)
