"""
Targeted Verification: Institutional Risk Triggers
Tests 14 USD Daily Loss Kill-Switch and 18 USD Daily Profit Lock.
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from app.risk.gate import PreTradeGate
from app.risk.sizing import calculate_lot_size
from app.domain.models import AccountState, Decision, SymbolProfile
from app.domain.enums import Action, BlockReason
from app.core.config import get_settings

def test_triggers():
    settings = get_settings()
    gate = PreTradeGate(settings)
    
    # Mock profile with Gold specs
    profile = SymbolProfile(
        symbol="XAUUSD", contract_size=100.0, 
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        point=0.01, digits=2, is_active=True
    )
    
    decision = Decision(symbol="XAUUSD", action=Action.BUY, confidence=0.8,
                       reason="test", stop_loss=1900.0, take_profit=1920.0, strategy_name="alpha_v6")
    
    print("\n--- 1. Testing 14 USD Daily Loss Kill-Switch ---")
    # Loss = -13.0 (Safe)
    safe_account = AccountState(balance=10000, equity=10000, daily_pl=-13.0)
    res_safe = gate.check(decision, profile, safe_account, mt5_connected=True, market_open=True)
    print(f"Daily PL: -13.0 USD -> Passed: {res_safe.passed} {res_safe.reasons}")
    assert res_safe.passed
    
    # Loss = -14.1 (Block)
    kill_account = AccountState(balance=10000, equity=10000, daily_pl=-14.1)
    res_kill = gate.check(decision, profile, kill_account, mt5_connected=True, market_open=True)
    print(f"Daily PL: -14.1 USD -> Passed: {res_kill.passed} {res_kill.reasons}")
    assert not res_kill.passed
    assert BlockReason.DAILY_LOSS_EXCEEDED in res_kill.reasons
    
    print("\n--- 2. Testing 18 USD Daily Profit Lock (Risk Reduction) ---")
    
    # Using 10k balance to ensure lot size stays above minimum even after 80% reduction
    # Base risk = 0.5% of 10k = 50 USD. SL distance 10. Lot = 0.05.
    # After 80% reduction = 10 USD. Lot = 0.01. (Passes min lot check)
    large_acct = AccountState(balance=10000, equity=10000, daily_pl=10.0, free_margin=10000)
    res_normal = calculate_lot_size(decision, profile, large_acct, settings, entry_price=1910.0)
    if isinstance(res_normal, BlockReason):
        print(f"FAILED: Normal case blocked by {res_normal}")
        return
    print(f"Daily PL: 10.0 USD -> Lot: {res_normal.lot_size}, Risk%: {res_normal.risk_pct:.3f}")
    
    # Profit = 19.0 (Reduced Risk by 80%)
    locked_acct = AccountState(balance=10000, equity=10000, daily_pl=19.0, free_margin=10000)
    res_locked = calculate_lot_size(decision, profile, locked_acct, settings, entry_price=1910.0)
    if isinstance(res_locked, BlockReason):
        print(f"FAILED: Locked case blocked by {res_locked}")
        return
    print(f"Daily PL: 19.0 USD -> Lot: {res_locked.lot_size}, Risk%: {res_locked.risk_pct:.3f}")
    
    # Verification: Risk should be ~0.2x of normal
    ratio = res_locked.risk_pct / res_normal.risk_pct
    print(f"Risk Ratio: {ratio:.2f} (Target: ~0.20)")
    assert ratio < 0.3 # Allow some room for rounding but must be significant reduction
    
    print("\n✅ Institutional Triggers Verified Successfully!")

if __name__ == "__main__":
    test_triggers()
