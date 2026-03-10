
import sys
import os
import time
from app.core.config import get_settings
from app.risk.cooldown_manager import CooldownManager
from app.risk.risk_dampener import RiskDampener
from app.db.sqlite import SQLiteStore

# Setup
settings = get_settings()
db = SQLiteStore(settings)
db.connect()

print("--- 🛡️ Testing Risk Guards ---")

# 1. Test Nuclear Cooldown
print("\n[1] Testing Nuclear Cooldown...")
cm = CooldownManager(db=db, cooldown_minutes=1, max_consecutive_losses=2) # 1 min for test
symbol = "TESTUSD"

# Reset first
cm.reset_session()
allowed, reason = cm.is_allowed(symbol)
print(f"Initial State: Allowed={allowed}")

# Loss 1
print("Recording Loss 1...")
cm.record_loss(symbol)
allowed, reason = cm.is_allowed(symbol)
print(f"After Loss 1: Allowed={allowed} | Reason={reason}")

# Loss 2 (Trigger)
print("Recording Loss 2 (Should trigger Nuclear Block)...")
cm.record_loss(symbol)
allowed, reason = cm.is_allowed(symbol)
print(f"After Loss 2: Allowed={allowed} | Reason={reason}")
assert not allowed
assert "NUCLEAR_COOLDOWN" in reason

# Persistence Check
print("Simulating Restart (Re-initializing Manager)...")
cm2 = CooldownManager(db=db, cooldown_minutes=1, max_consecutive_losses=2)
allowed, reason = cm2.is_allowed(symbol)
print(f"After Restart: Allowed={allowed} | Reason={reason}")
assert not allowed
assert "NUCLEAR_COOLDOWN" in reason
print("✅ Persistence Verified!")

# 2. Test Hard Lot Cap
print("\n[2] Testing Hard Lot Cap...")
rd = RiskDampener(loss3_mult=0.3)
rd.reset_session()

# Loss 1
rd.record_loss(symbol)
mult = rd.get_multiplier(symbol)
print(f"Loss 1 Multiplier: {mult} (Expected 0.7)")

# Loss 2 (Trigger)
rd.record_loss(symbol)
mult = rd.get_multiplier(symbol)
print(f"Loss 2 Multiplier: {mult} (Expected 0.3 - Hard Cap)")
assert mult == 0.3

# Tilt Check
is_tilt = rd.is_tilt_active(symbol)
print(f"Is Tilt Active? {is_tilt}")
assert is_tilt
print("✅ Hard Cap Verified!")

db.disconnect()
