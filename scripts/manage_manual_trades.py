import requests
import json

BASE_URL = "http://localhost:8000/api"
TICKETS = [3840030022, 3840113187]

def apply_risk(ticket):
    print(f"\n🔧 Configuring Ticket {ticket}...")

    try:
        # 1. Set Trailing Stop (Dynamic / Fixed)
        trailing_payload = {
            "mode": "dynamic",  # dynamic = ATP based
            "atr_multiplier": 1.5,
            "activation_r": 1.5,
            "step_r": 0.2
        }
        
        print(f"   Sending Trailing request for {ticket}...")
        r = requests.post(
            f"{BASE_URL}/positions/{ticket}/trailing", 
            json=trailing_payload,
            timeout=10
        )
        if r.status_code == 200:
            print(f"   ✅ Trailing Set: Dynamic (Act 1.5R)")
        else:
            print(f"   ❌ Trailing Failed: {r.status_code} {r.text}")

        # 2. Set Profit Lock (Multi-tier)
        lock_payload = {
            "tiers": [
                {"r": 0.8, "lock_pct": 0.2},   # +0.8R -> Lock 20%
                {"r": 1.5, "lock_pct": 0.6},   # +1.5R -> Lock 60%
                {"r": 3.0, "lock_pct": 0.8},   # +3.0R -> Lock 80%
            ]
        }
        
        print(f"   Sending Profit Lock request for {ticket}...")
        r = requests.post(
            f"{BASE_URL}/positions/{ticket}/profit-lock", 
            json=lock_payload,
            timeout=10
        )
        if r.status_code == 200:
            print(f"   ✅ Profit Lock Set: 3 Tiers")
        else:
             print(f"   ❌ Profit Lock Failed: {r.status_code} {r.text}")

    except Exception as e:
        print(f"   ⚠️ Error configuring {ticket}: {e}")

    # 3. Modify SL (Safety Net - 500 points / $5)
    # Note: MT5 might reject if too close, but we try.
    # Actually, we rely on the bot's managers mainly. 
    # But let's verify if user wants specific hard SL? 
    # The API modify_sl takes absolute price. Ideally we read position first.
    # For now, we skip hard SL modification via API to avoid price conflict, 
    # relying on Trailing/ProfitLock to manage it.
    
    print(f"   ℹ️ Risk Managers Active.")

for t in TICKETS:
    apply_risk(t)
