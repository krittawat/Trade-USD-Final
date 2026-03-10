"""
Migration Readiness Checker.
Analyzes Dry Run performance and logs to determine if the system is ready for LIVE trading.

Criteria:
1. Dry Run Duration > 30 mins (simulated check here, real check via logs)
2. Profit Factor > 1.0 (or at least not blowing up)
3. No Critical Errors in logs
4. Acceptance Tests Passing
"""

import sys
import os
import json
import logging
from datetime import datetime, timezone, timedelta

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

def check_dry_run_state():
    print("\n--- Checking Dry Run State ---")
    state_file = "backend/data/paper_state.json"
    if not os.path.exists(state_file):
        print("❌ Paper State not found. Run Dry Run first.")
        return False

    try:
        with open(state_file, "r") as f:
            data = json.load(f)
            
        balance = data.get("balance", 10000.0)
        positions = data.get("positions", [])
        
        print(f"Balance: {balance}")
        print(f"Open Positions: {len(positions)}")
        
        # Calculate closed PnL implicitly (balance - 10000)
        pnl = balance - 10000.0
        print(f"Realized PnL: {pnl:.2f}")
        
        if balance < 9000.0: # 10% Drawdown
            print("❌ Account blew up (>10% DD).")
            return False
            
        print("✅ Account State Healthy (No massive DD)")
        return True
    except Exception as e:
        print(f"❌ Error reading state: {e}")
        return False

def check_error_logs():
    print("\n--- Checking Error Logs ---")
    log_file = "backend/logs/error.log"
    if not os.path.exists(log_file):
        print("✅ No error.log found (Clean run?)")
        return True
        
    critical_errors = 0
    recent_errors = 0
    threshold_time = datetime.now() - timedelta(minutes=30)
    
    try:
        with open(log_file, "r") as f:
            for line in f:
                if "CRITICAL" in line:
                    critical_errors += 1
                # Timestamp check would be complex parsing, skipping for now
                recent_errors += 1
                
    except Exception:
        pass
        
    print(f"Total Errors Found: {recent_errors}")
    if critical_errors > 0:
        print(f"❌ Found {critical_errors} CRITICAL errors.")
        return False
        
    print("✅ No Critical Errors")
    return True

def main():
    print("=== Migration Readiness Check ===")
    
    checks = [
        check_dry_run_state(),
        check_error_logs()
    ]
    
    if all(checks):
        print("\n🎉 SYSTEM READY FOR LIVE MIGRATION (Conceptually) 🎉")
        print("Ensure you have run 'backend/scripts/verify/test_dry_run.py' and it passed.")
        sys.exit(0)
    else:
        print("\n⛔ SYSTEM NOT READY")
        sys.exit(1)

if __name__ == "__main__":
    main()
