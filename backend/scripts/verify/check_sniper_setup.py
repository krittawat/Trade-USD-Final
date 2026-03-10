
import sys
import os
from pathlib import Path

# Add backend to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from app.strategy.factory import StrategyFactory, TEMPLATE_REGISTRY
from app.risk.session_guard import SessionGuard
from app.risk.gate import PreTradeGate
from app.core.config import Settings
from app.domain.models import Decision, SymbolProfile, AccountState
from app.domain.enums import Action, BlockReason

def test_strategy_registration():
    print(">>> Testing Strategy Registration...")
    factory = StrategyFactory()
    factory.auto_register()
    
    if "fx_sniper" in factory._strategies:
        print("✅ fx_sniper registered successfully.")
        strat = factory._strategies["fx_sniper"]
        print(f"   Name: {strat.name}")
        print(f"   Timeframe: {strat.timeframe}")
        print(f"   Regimes: {strat.suitable_regimes}")
    else:
        print("❌ fx_sniper NOT registered!")
        sys.exit(1)

def test_daily_guard():
    print("\n>>> Testing Daily Guard...")
    sg = SessionGuard(max_trades_per_session=3)
    
    # Simulate 2 trades for FX
    symbol_fx = "EURUSDc"
    sg.record_trade(symbol_fx, "LONDON")
    sg.record_trade(symbol_fx, "LONDON")
    
    # Check if 3rd is allowed (Limit 2)
    allowed, reason = sg.is_daily_allowed(symbol_fx, max_daily=2)
    if not allowed and "DAILY_MAX" in reason:
        print(f"✅ Daily Limit Enforcement (FX) working: {reason}")
    else:
        print(f"❌ Daily Limit Failed (FX): Allowed={allowed}, Reason={reason}")
        
    # Simulate 1 trade for Gold
    symbol_gold = "XAUUSDc"
    sg.record_trade(symbol_gold, "LONDON")
    
    # Check if 2nd is allowed (Limit 1)
    allowed, reason = sg.is_daily_allowed(symbol_gold, max_daily=1)
    if not allowed and "DAILY_MAX" in reason:
        print(f"✅ Daily Limit Enforcement (Gold) working: {reason}")
    else:
        print(f"❌ Daily Limit Failed (Gold): Allowed={allowed}, Reason={reason}")

def test_gate_integration():
    print("\n>>> Testing Gate Integration...")
    settings = Settings()
    # Mock settings
    settings.max_daily_trades_fx = 2
    settings.max_daily_trades_gold = 1
    
    sg = SessionGuard()
    gate = PreTradeGate(settings, session_guard=sg)
    
    # Fill up limit
    sg.record_trade("XAUUSDc", "LONDON") # 1 trade
    
    decision = Decision(symbol="XAUUSDc", action=Action.BUY, confidence=0.9, reason="Test")
    profile = SymbolProfile(symbol="XAUUSDc")
    account = AccountState(balance=1000, equity=1000)
    
    # Check Gate
    result = gate.check(
        decision, profile, account, 
        mt5_connected=True, market_open=True, current_session="LONDON"
    )
    
    # Should be blocked by DAILY_MAX
    daily_blocked = any(r == BlockReason.DAILY_MAX_TRADES for r in result.reasons)
    
    if daily_blocked:
        print("✅ Gate successfully BLOCKED Gold trade due to Daily Limit.")
    else:
        print(f"❌ Gate FAILED to block: {result.reasons}")

if __name__ == "__main__":
    test_strategy_registration()
    test_daily_guard()
    test_gate_integration()
