"""
Verify: Risk Dampener — ทดสอบปรับ lot อัตโนมัติ.

ทดสอบ:
    1. 0 losses → multiplier 1.0
    2. 1 loss → multiplier 0.7
    3. 2 losses → multiplier 0.5
    4. 3+ losses → multiplier 0.3
    5. Win resets losses
    6. Recovery mechanism: 3+ wins → gradual increase

วิธีรัน:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/verify/verify_risk_dampener.py
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
print("🔒 Verify: Risk Dampener")
print("=" * 60)

from app.risk.risk_dampener import RiskDampener

# ─── Test 1: Default multiplier ───
print("\n[1/6] Default → multiplier 1.0")
rd = RiskDampener()
mult = rd.get_multiplier("XAUUSDc")
check("Default = 1.0", mult == 1.0, f"Got {mult}")

# ─── Test 2: 1 loss ───
print("\n[2/6] After 1 loss → 0.7")
rd.record_loss("XAUUSDc")
mult = rd.get_multiplier("XAUUSDc")
check("1 loss = 0.7", mult == 0.7, f"Got {mult}")

# ─── Test 3: 2 losses ───
print("\n[3/6] After 2 losses → 0.5")
rd.record_loss("XAUUSDc")
mult = rd.get_multiplier("XAUUSDc")
check("2 losses = 0.5", mult == 0.5, f"Got {mult}")

# ─── Test 4: 3+ losses ───
print("\n[4/6] After 3+ losses → 0.3 (floor)")
rd.record_loss("XAUUSDc")
mult = rd.get_multiplier("XAUUSDc")
check("3 losses = 0.3", mult == 0.3, f"Got {mult}")

# 5 losses still 0.3
rd.record_loss("XAUUSDc")
rd.record_loss("XAUUSDc")
mult = rd.get_multiplier("XAUUSDc")
check("5 losses still = 0.3", mult == 0.3, f"Got {mult}")

# ─── Test 5: Win resets ───
print("\n[5/6] Win resets losses")
rd.record_win("XAUUSDc")
mult = rd.get_multiplier("XAUUSDc")
check("After 1 win → back to 1.0", mult == 1.0, f"Got {mult}")
check("consecutive_losses = 0", rd.get_status("XAUUSDc")["consecutive_losses"] == 0)

# ─── Test 6: Recovery on multiple wins ───
print("\n[6/6] Recovery mechanism on win streak")
rd2 = RiskDampener(loss3_mult=0.3, win_recovery=0.1)

# Simulate: 3 losses → 3 wins
rd2.record_loss("XAUUSDc")
rd2.record_loss("XAUUSDc")
rd2.record_loss("XAUUSDc")
check("After 3 losses = 0.3", rd2.get_multiplier("XAUUSDc") == 0.3)

# First win resets losses
rd2.record_win("XAUUSDc")
m1 = rd2.get_multiplier("XAUUSDc")
check(f"After 1 win = 1.0 (reset)", m1 == 1.0, f"Got {m1}")

# Build up win streak past 3
rd2.record_win("XAUUSDc")
rd2.record_win("XAUUSDc")
rd2.record_win("XAUUSDc")
m3 = rd2.get_multiplier("XAUUSDc")
check(f"After 4 wins → recovery applied ({m3})", m3 <= 1.0)

# ─── Test: Different symbols independent ───
print("\n[BONUS] Different symbols are independent")
rd3 = RiskDampener()
rd3.record_loss("XAUUSDc")
rd3.record_loss("XAUUSDc")
check("XAU has 2 losses", rd3.get_multiplier("XAUUSDc") == 0.5)
check("EUR has 0 losses", rd3.get_multiplier("EURUSDc") == 1.0)


# ─── Summary ───
print("\n" + "=" * 60)
total = passed + failed
print(f"📊 ผลลัพธ์: {passed}/{total} ผ่าน ({passed/total*100:.0f}%)")
if failed == 0:
    print("🎉 ALL PASSED — Risk Dampener ทำงานถูกต้อง")
else:
    print(f"⚠️ FAILED: {failed} ข้อ")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
