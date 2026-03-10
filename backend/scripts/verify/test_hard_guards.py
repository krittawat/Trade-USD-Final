
import sys
import os
from pathlib import Path

# Add backend to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from app.risk.gate import PreTradeGate
from app.domain.models import Decision, AccountState, SymbolProfile
from app.domain.enums import Action, BlockReason
from app.core.config import Settings

def test_hard_guards():
    """Test strict enforcement of hard risk guards."""
    print("running_test_hard_guards...")
    
    # Setup
    settings = Settings(
        max_daily_loss_pct=3.0,
        max_positions_per_symbol=2,
        max_total_positions=5,
        trading_mode="DRY_RUN"
    )
    
    gate = PreTradeGate(settings)
    
    profile = SymbolProfile(symbol="XAUUSDc", is_active=True)
    
    decision = Decision(
        action=Action.BUY,
        symbol="XAUUSDc",
        strategy_name="TestStrategy",
        confidence=0.9,
        reason="Test Guard",
        stop_loss=2000.0,
        take_profit=2020.0,
    )

    # 1. Test Daily Loss Limit
    print("\n--- Test 1: Daily Loss Limit (Exceeded) ---")
    account_loss = AccountState(
        balance=10000.0,
        equity=9600.0,
        daily_pl=-400.0, # -4% (Limit 3%)
    )
    res_loss = gate.check(
        decision=decision, profile=profile, account=account_loss,
        mt5_connected=True, market_open=True, current_spread=10
    )
    print(f"Daily Loss Result: {res_loss.passed}, Reasons: {res_loss.reasons}")
    assert not res_loss.passed, "Should be blocked by Daily Loss"
    assert BlockReason.DAILY_LOSS_EXCEEDED in res_loss.reasons, "Reason mismatch"

    # 2. Test Daily Loss Limit (Safe)
    print("\n--- Test 2: Daily Loss Limit (Safe) ---")
    account_safe = AccountState(
        balance=10000.0,
        equity=9800.0,
        daily_pl=-200.0, # -2% (Limit 3%)
    )
    res_safe = gate.check(
        decision=decision, profile=profile, account=account_safe,
        mt5_connected=True, market_open=True, current_spread=10
    )
    print(f"Safe PnL Result: {res_safe.passed}")
    assert res_safe.passed, "Should pass with safe PnL"

    # 3. Test Max Positions per Symbol
    print("\n--- Test 3: Max Positions (Symbol) ---")
    # Using safe account
    res_max_sym = gate.check(
        decision=decision, profile=profile, account=account_safe,
        mt5_connected=True, market_open=True, current_spread=10,
        open_positions_count=2 # Limit is 2 (>= check?)
    )
    # Check gate logic: if count >= max -> block
    # settings.max_positions_per_symbol = 2
    # If we have 2, we can't open 3rd.
    print(f"Max Sym Result: {res_max_sym.passed}, Reasons: {res_max_sym.reasons}")
    assert not res_max_sym.passed, "Should be blocked by Max Symbol Positions"
    assert BlockReason.MAX_POSITIONS in res_max_sym.reasons

    # 4. Test Max Total Positions
    print("\n--- Test 4: Max Positions (Total) ---")
    res_max_total = gate.check(
        decision=decision, profile=profile, account=account_safe,
        mt5_connected=True, market_open=True, current_spread=10,
        open_positions_count=0,
        total_positions_count=5 # Limit is 5
    )
    print(f"Max Total Result: {res_max_total.passed}, Reasons: {res_max_total.reasons}")
    assert not res_max_total.passed, "Should be blocked by Max Total Positions"
    assert BlockReason.MAX_POSITIONS in res_max_total.reasons

    # 5. Test Psychological Guard (Martingale)
    print("\n--- Test 5: Psychological Guard (Martingale) ---")
    metrics_marti = {"martingale_detected": True}
    res_psycho = gate.check(
        decision=decision, profile=profile, account=account_safe,
        mt5_connected=True, market_open=True, current_spread=10,
        performance_metrics=metrics_marti
    )
    print(f"Psycho Result: {res_psycho.passed}, Reasons: {res_psycho.reasons}")
    assert not res_psycho.passed, "Should be blocked by Psychological Guard"
    assert BlockReason.STRATEGY_BLACKLISTED in res_psycho.reasons

    print("\n*** ALL TESTS PASSED ***")

if __name__ == "__main__":
    test_hard_guards()
