"""
Verify: No Overtrade — ทดสอบว่าระบบป้องกัน overtrading ทำงาน.

ทดสอบ 3 ข้อ:
    1. Session Guard: สูงสุด 3 เทรดต่อ session ต่อ symbol
    2. Regime Filter: NO-TRADE ในตลาด sideways (ADX < 18)
    3. Daily Loss Cap: บล็อกเมื่อขาดทุนเกิน 3%

วิธีรัน:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/verify/verify_no_overtrade.py
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

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


print("=" * 60)
print("🔒 Verify: No Overtrade")
print("=" * 60)

# ─── Test 1: Session Guard ───
print("\n[1/3] Session Guard — max 3 trades per session per symbol")
try:
    from app.risk.session_guard import SessionGuard

    sg = SessionGuard(max_trades_per_session=3)

    # Record 3 trades
    for i in range(3):
        ok, reason = sg.is_allowed("XAUUSDc", "LONDON")
        check(f"Trade {i+1} allowed", ok)
        sg.record_trade("XAUUSDc", "LONDON")

    # 4th trade should be blocked
    ok, reason = sg.is_allowed("XAUUSDc", "LONDON")
    check("Trade 4 blocked", not ok, f"Expected blocked, got allowed")
    check("Block reason contains SESSION_MAX", "SESSION_MAX" in reason)

    # Different symbol should still be allowed
    ok2, _ = sg.is_allowed("EURUSDc", "LONDON")
    check("Different symbol still allowed", ok2)

    # Different session should still be allowed
    ok3, _ = sg.is_allowed("XAUUSDc", "NEW_YORK")
    check("Different session still allowed", ok3)

    # Reset session → should allow again
    sg.reset_session("LONDON")
    ok4, _ = sg.is_allowed("XAUUSDc", "LONDON")
    check("After reset → allowed", ok4)

except Exception as e:
    check("Session Guard test", False, str(e))


# ─── Test 2: Regime Filter ───
print("\n[2/3] Regime Filter — NO-TRADE in sideways")
try:
    import numpy as np
    import pandas as pd
    from app.risk.regime_filter import RegimeFilter

    rf = RegimeFilter(
        enabled=True,
        adx_min=18.0,
        ema_compression_pct=0.1,
        range_threshold_pct=0.5,
    )

    # Create synthetic sideways candles (small range, low ADX)
    n = 100
    np.random.seed(42)
    base_price = 2000.0
    noise = np.random.randn(n) * 0.5  # Very small moves
    close = base_price + np.cumsum(noise * 0.1)  # Very tight range
    high = close + abs(noise) * 0.3
    low = close - abs(noise) * 0.3

    sideways_candles = pd.DataFrame({
        "open": close - noise * 0.05,
        "high": high,
        "low": low,
        "close": close,
    })

    result = rf.check(sideways_candles)
    check("Sideways candles → NOT tradable", not result.tradable,
          f"Expected NOT tradable, got tradable={result.tradable}")
    if not result.tradable:
        check("Has reason", len(result.reason) > 0)

    # Create trending candles (expanding volatility → ATR > ATR_MA)
    n2 = 100
    bar_width = np.linspace(5, 25, n2)  # expanding range
    trending_close = base_price + np.cumsum(np.ones(n2) * 1.0)
    trending_high = trending_close + bar_width * 0.6
    trending_low = trending_close - bar_width * 0.4

    trending_candles = pd.DataFrame({
        "open": trending_close - 1.0,
        "high": trending_high,
        "low": trending_low,
        "close": trending_close,
    })

    result2 = rf.check(trending_candles)
    check("Trending candles → tradable", result2.tradable,
          f"Expected tradable, got tradable={result2.tradable}, reason={result2.reason}")

    # Disabled filter → always tradable
    rf_off = RegimeFilter(enabled=False)
    result3 = rf_off.check(sideways_candles)
    check("Disabled → always tradable", result3.tradable)

except Exception as e:
    check("Regime Filter test", False, str(e))


# ─── Test 3: Daily Loss Cap ───
print("\n[3/3] Daily Loss Cap — block at 3%")
try:
    from app.risk.gate import PreTradeGate
    from app.domain.models import Decision, SymbolProfile, AccountState
    from app.domain.enums import Action, BlockReason
    from app.core.config import get_settings

    settings = get_settings()

    gate = PreTradeGate(settings)

    profile = SymbolProfile(
        symbol="XAUUSDc", contract_size=100.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        point=0.01, digits=2,
    )

    decision = Decision(
        symbol="XAUUSDc", action=Action.BUY, confidence=0.8,
        reason="test", stop_loss=1900.0, take_profit=1920.0,
    )

    # Account with 3.1% daily loss → should block
    account_loss = AccountState(
        balance=10000.0, equity=9690.0, daily_pl=-310.0,
        initial_balance=10000.0,
    )

    max_daily = getattr(settings, 'max_daily_loss_pct', 3.0)
    result = gate.check(
        decision=decision, profile=profile, account=account_loss,
        mt5_connected=True, market_open=True, current_spread=0.1,
        current_session="LONDON", news_safe=True,
    )
    has_daily_loss = BlockReason.DAILY_LOSS_EXCEEDED in result.reasons
    check(f"3.1% daily loss → blocked (max={max_daily}%)", has_daily_loss,
          f"Expected DAILY_LOSS_EXCEEDED in {[r.value for r in result.reasons]}")

    # Account with 2% daily loss → should pass daily check
    account_ok = AccountState(
        balance=10000.0, equity=9800.0, daily_pl=-200.0,
        initial_balance=10000.0,
    )
    result2 = gate.check(
        decision=decision, profile=profile, account=account_ok,
        mt5_connected=True, market_open=True, current_spread=0.1,
        current_session="LONDON", news_safe=True,
    )
    no_daily = BlockReason.DAILY_LOSS_EXCEEDED not in result2.reasons
    check("2% daily loss → not blocked by daily cap", no_daily,
          f"Unexpected DAILY_LOSS_EXCEEDED in {[r.value for r in result2.reasons]}")

except Exception as e:
    check("Daily Loss Cap test", False, str(e))


# ─── Summary ───
print("\n" + "=" * 60)
total = passed + failed
print(f"📊 ผลลัพธ์: {passed}/{total} ผ่าน ({passed/total*100:.0f}%)")
if failed == 0:
    print("🎉 ALL PASSED — ระบบป้องกัน overtrading ทำงานถูกต้อง")
else:
    print(f"⚠️ FAILED: {failed} ข้อ")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
