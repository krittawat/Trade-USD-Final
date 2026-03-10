import json
from backend.trader.risk.opus_governor import governor

def verify_hunter_mode():
    # Simulate a stressed account state to trigger hunter mode
    acct = {
        "equity": 230,
        "balance": 246,
        "margin_level": 1500,
        "daily_pnl": -10,
        "consecutive_losses": 0
    }
    
    status = governor.compute_status(acct)
    print(f"--- Hunter Mode Verification ---")
    print(f"Risk Level: {status.risk_level}")
    print(f"Recommended Symbol: {status.recommended_symbol}")
    print(f"Lot Multiplier: {status.lot_multiplier}")

if __name__ == "__main__":
    verify_hunter_mode()
