"""
Verify SL/TP Attachment Logic (OrderPlanBuilder).
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parents[3]))

import pandas as pd
from app.core.config import Settings
from app.domain.enums import Action
from app.domain.models import Decision, SymbolProfile, AccountState
from app.risk.order_plan import OrderPlanBuilder

def test_sl_tp_generation():
    print("Testing OrderPlanBuilder SL/TP Generation...")
    
    settings = Settings()
    builder = OrderPlanBuilder(settings)
    
    # Mock Data
    profile = SymbolProfile(symbol="EURUSD", point=0.00001, digits=5)
    account = AccountState(balance=1000, equity=1000)
    decision = Decision(symbol="EURUSD", action=Action.BUY, confidence=0.9, reason="Test")
    
    # Candles for ATR
    candles = pd.DataFrame({
        "high": [1.1050]*20,
        "low": [1.1000]*20,
        "close": [1.1025]*20
    })
    # ATR approx 0.0050 (High-Low)
    
    # 1. Test Auto SL/TP
    print("Case 1: Auto SL/TP from ATR")
    plan = builder.build(decision, profile, account, candles, mt5_price=(1.1025, 1.1026))
    
    if hasattr(plan, 'stop_loss'):
        print(f"  PASS: Plan created. SL={plan.stop_loss}, TP={plan.take_profit}")
        if plan.stop_loss > 0 and plan.take_profit > 0:
            print("  PASS: SL/TP > 0")
        else:
            print("  FAIL: SL/TP is 0")
    else:
        print(f"  FAIL: Blocked reason: {plan}")

    # 2. Test Invalid SL (Too close)
    print("\nCase 2: SL too close (Broker check)")
    decision_close = Decision(symbol="EURUSD", action=Action.BUY, confidence=0.9, reason="Test", stop_loss=1.10255) # Entry 1.1026
    plan_close = builder.build(decision_close, profile, account, candles, mt5_price=(1.1025, 1.1026))
    
    if hasattr(plan_close, 'value') and "SL_TOO_CLOSE" in str(plan_close):
        print(f"  PASS: Correctly blocked SL too close. Reason: {plan_close}")
    else:
        print(f"  FAIL: Should block but got: {plan_close}")

if __name__ == "__main__":
    test_sl_tp_generation()
