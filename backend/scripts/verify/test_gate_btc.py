import sys
from pathlib import Path
from dataclasses import dataclass
from unittest.mock import MagicMock

# Add backend to path
sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.domain.enums import BlockReason, Action
from app.domain.models import Decision, AccountState, SymbolProfile, GateResult
from app.risk.gate import PreTradeGate

@dataclass
class MockSettings:
    max_risk_per_trade_pct: float = 2.0
    max_positions_per_symbol: int = 2
    max_total_positions: int = 10
    capital_floor_pct: float = 90.0
    floating_dd_block_pct: float = 10.0
    max_daily_trades_crypto: int = 5
    max_daily_trades_gold: int = 3
    max_daily_trades_fx: int = 2
    min_equity_threshold: float = 50.0

def test_gate_logic():
    print("--- Testing Gate Logic ---")
    settings = MockSettings()
    gate = PreTradeGate(settings)
    
    # 1. BTCUSD on WEEKEND (Should Pass session check)
    btc_decision = Decision(symbol="BTCUSD", action=Action.BUY, confidence=0.8, stop_loss=60000, reason="Test signal")
    btc_profile = SymbolProfile(symbol="BTCUSD", is_active=True, contract_size=1, point=0.01, spread_max_allowed=1000)
    account_ok = AccountState(balance=100.0, equity=100.0, free_margin=100.0, initial_balance=100.0)
    
    result = gate.check(
        decision=btc_decision,
        profile=btc_profile,
        account=account_ok,
        mt5_connected=True,
        market_open=True,
        current_session="WEEKEND"
    )
    
    print(f"BTC on WEEKEND passed: {result.passed}")
    if not result.passed:
        print(f"Reasons: {result.reasons}")
    assert result.passed

    # 2. XAUUSD on WEEKEND (Should be BLOCKED by session)
    xau_decision = Decision(symbol="XAUUSD", action=Action.BUY, confidence=0.8, stop_loss=2000, reason="Test signal")
    xau_profile = SymbolProfile(symbol="XAUUSD", is_active=True, contract_size=100, point=0.01, spread_max_allowed=50)
    
    result = gate.check(
        decision=xau_decision,
        profile=xau_profile,
        account=account_ok,
        mt5_connected=True,
        market_open=True,
        current_session="WEEKEND"
    )
    
    print(f"XAU on WEEKEND passed: {result.passed}")
    print(f"Reasons (Expected SESSION_BLOCKED): {result.reasons}")
    assert not result.passed
    assert BlockReason.SESSION_BLOCKED in result.reasons

    # 3. Low Equity Check (Should be BLOCKED by EQUITY_TOO_LOW)
    account_low = AccountState(balance=40.0, equity=40.0, free_margin=40.0, initial_balance=100.0)
    result = gate.check(
        decision=btc_decision,
        profile=btc_profile,
        account=account_low,
        mt5_connected=True,
        market_open=True,
        current_session="LONDON"
    )
    
    print(f"Low equity BTC passed: {result.passed}")
    print(f"Reasons (Expected EQUITY_TOO_LOW): {result.reasons}")
    assert not result.passed
    assert BlockReason.EQUITY_TOO_LOW in result.reasons

    print("\n✅ All Gate Logic Tests Passed!")

if __name__ == "__main__":
    test_gate_logic()
