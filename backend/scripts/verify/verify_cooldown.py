"""
Verify: Cooldown Manager — ทดสอบ cooldown หลังขาดทุน.

ทดสอบ 4 ข้อ:
    1. ขาดทุน 1 ครั้ง → cooldown 5 นาที
    2. ขาดทุน 2 ครั้งติด → ปิดเทรด session
    3. ชนะ → รีเซ็ต consecutive losses
    4. เปลี่ยน session → เคลียร์สถานะทั้งหมด

วิธีรัน:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/verify/verify_cooldown.py
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

# Suppress app logs (WARNING only) to keep output clean
import logging
from app.risk.cooldown_manager import CooldownManager  # Import here to init logger

# Force silence
logging.getLogger("app.risk.cooldown_manager").setLevel(logging.CRITICAL)
logging.getLogger("app.core.logging").setLevel(logging.CRITICAL)
logging.getLogger().setLevel(logging.CRITICAL)

passed = 0
failed = 0


def check(name: str, result: bool, detail: str = ""):
    global passed, failed
    if result:
        passed += 1
        print(f"[PASS] {name}")
    else:
        failed += 1
        print(f"[FAIL] {name}: {detail}")


print("=" * 60)
print("🔒 Verify: Cooldown Manager")
print("=" * 60)


# ─── Test 1: Single loss → cooldown ───
print("\n[1/4] ขาดทุน 1 ครั้ง → cooldown 5 นาที")
try:
    from app.risk.cooldown_manager import CooldownManager

    cm = CooldownManager(cooldown_minutes=5, max_consecutive_losses=2)

    # Before any loss → allowed
    ok, reason = cm.is_allowed("XAUUSDc")
    check("ก่อนขาดทุน → เทรดได้", ok)

    # Record loss
    cm.record_loss("XAUUSDc")

    # After 1 loss → should be in cooldown
    ok2, reason2 = cm.is_allowed("XAUUSDc")
    check("หลังขาดทุน 1 ครั้ง → ถูกบล็อก (Cooldown)", not ok2,
          f"คาดว่าจะถูกบล็อก แต่เทรดได้")
    check("เหตุผลระบุว่า COOLDOWN", "COOLDOWN" in reason2)

    # Status check
    status = cm.get_status("XAUUSDc")
    check("consecutive_losses = 1", status["consecutive_losses"] == 1)
    check("cooldown_remaining > 0", status["cooldown_remaining_s"] > 0)
    check("session ยังไม่ถูกปิด", not status["is_nuclear"])

except Exception as e:
    check("Test 1 ผิดพลาด", False, str(e))


# ─── Test 2: Two consecutive losses → nuclear block ───
print("\n[2/4] ขาดทุน 2 ครั้งติด → Nuclear Block (1 ชม.)")
try:
    cm2 = CooldownManager(cooldown_minutes=5, max_consecutive_losses=2)

    cm2.record_loss("XAUUSDc")
    cm2.record_loss("XAUUSDc")

    ok, reason = cm2.is_allowed("XAUUSDc")
    check("หลังขาดทุน 2 ครั้ง → ถูกบล็อก", not ok)
    check("เหตุผลระบุว่า NUCLEAR", "NUCLEAR" in reason)
    check("สถานะ is_nuclear = True", cm2.get_status("XAUUSDc")["is_nuclear"])

    # Different symbol still allowed
    ok2, _ = cm2.is_allowed("EURUSDc")
    check("สินค้าอื่นยังเทรดได้ (EURUSDc)", ok2)

except Exception as e:
    check("Test 2 ผิดพลาด", False, str(e))


# ─── Test 3: Win resets consecutive losses ───
print("\n[3/4] ชนะ → รีเซ็ตจำนวนครั้งที่แพ้")
try:
    cm3 = CooldownManager(cooldown_minutes=1, max_consecutive_losses=3)

    cm3.record_loss("XAUUSDc")
    cm3.record_loss("XAUUSDc")
    
    status_before = cm3.get_status("XAUUSDc")
    check("ก่อนชนะ: แพ้ติดกัน 2 ครั้ง", status_before["consecutive_losses"] == 2)

    # Win resets losses
    cm3.record_win("XAUUSDc")
    
    status = cm3.get_status("XAUUSDc")
    # print(f"DEBUG: status after win: {status}") # Comment out debug
    
    check("หลังชนะ → แพ้ติดกันเป็น 0", status["consecutive_losses"] == 0)
    check("หลังชนะ → Clear Cooldown", status["cooldown_remaining_s"] == 0)
    check("หลังชนะ → Clear Nuclear", not status["is_nuclear"])

except Exception as e:
    check("Test 3 ผิดพลาด", False, str(e))


# ─── Test 4: Session reset clears all ───
print("\n[4/4] เปลี่ยน Session → เคลียร์สถานะทั้งหมด")
try:
    cm4 = CooldownManager(cooldown_minutes=5, max_consecutive_losses=2)

    cm4.record_loss("XAUUSDc")
    cm4.record_loss("XAUUSDc")  # nuclear block
    cm4.record_loss("EURUSDc")  # different symbol with cooldown

    check("ก่อน Reset: XAU ติด Nuclear", cm4.get_status("XAUUSDc")["is_nuclear"])

    cm4.reset_session()

    ok, _ = cm4.is_allowed("XAUUSDc")
    check("หลัง Reset: XAU เทรดได้", ok)

    ok2, _ = cm4.is_allowed("EURUSDc")
    check("หลัง Reset: EUR เทรดได้", ok2)

    check("Counters เป็น 0", cm4.get_status("XAUUSDc")["consecutive_losses"] == 0)

except Exception as e:
    check("Test 4 ผิดพลาด", False, str(e))


# ─── Summary ───
print("\n" + "=" * 60)
total = passed + failed
print(f"📊 ผลลัพธ์: {passed}/{total} ผ่าน ({passed/total*100:.0f}%)")
if failed == 0:
    print("🎉 ผ่านทั้งหมด — Cooldown Manager ทำงานถูกต้อง")
else:
    print(f"⚠️ ไม่ผ่าน: {failed} ข้อ")
print("=" * 60)
sys.exit(0 if failed == 0 else 1)
