
import sys
import os
from pathlib import Path

# Add backend to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from app.risk.sizing import calculate_lot_size
from app.domain.models import Decision, AccountState, SymbolProfile, OrderPlan
from app.domain.enums import Action, BlockReason
from app.core.config import Settings

def test_dynamic_sizing():
    """Test dynamic lot sizing based on performance metrics."""
    print("running_test_dynamic_sizing...")
    
    # 1. Setup Mock Objects
    settings = Settings(
        max_risk_per_trade_pct=2.0,
        trading_mode="DRY_RUN",
    )
    
    account = AccountState(
        balance=10000.0,
        equity=10000.0,
        free_margin=10000.0,
    )
    
    profile = SymbolProfile(
        symbol="XAUUSDc",
        digits=2,
        point=0.01,
        contract_size=100.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        spread_avg=20,
    )
    
    decision = Decision(
        action=Action.BUY,
        symbol="XAUUSDc",
        strategy_name="TestStrategy",
        confidence=0.9,
        reason="Dynamic Sizing Test",
        stop_loss=2000.0, # Price
        take_profit=2020.0,
    )
    
    entry_price = 2005.0 # SL dist = 5.0
    
    # Baseline: No Metrics
    print("\n--- Baseline Test (No Metrics) ---")
    plan_base = calculate_lot_size(
        decision=decision, profile=profile, account=account, settings=settings,
        entry_price=entry_price, stop_loss=2000.0
    )
    if isinstance(plan_base, BlockReason):
        print(f"FAIL: Baseline blocked: {plan_base}")
        return
        
    print(f"Baseline Lot: {plan_base.lot_size} (Risk: {plan_base.risk_pct}%)")
    base_lot = plan_base.lot_size
    
    # Test 1: Moderate Drawdown (6% -> 0.75x)
    print("\n--- Test 1: Moderate Drawdown (6%) ---")
    metrics_mod_dd = {"max_drawdown_pct": 6.0}
    plan_mod = calculate_lot_size(
        decision=decision, profile=profile, account=account, settings=settings,
        entry_price=entry_price, stop_loss=2000.0,
        performance_metrics=metrics_mod_dd
    )
    print(f"Mod DD Lot: {plan_mod.lot_size} (Expected ~{base_lot * 0.75:.2f})")
    assert plan_mod.lot_size < base_lot, "Lot should be reduced"
    # Allow small rounding diffs
    assert abs(plan_mod.lot_size - (base_lot * 0.75)) < 0.05, f"Expected 0.75x, got {plan_mod.lot_size/base_lot:.2f}x"

    # Test 2: Deep Drawdown (12% -> 0.50x)
    print("\n--- Test 2: Deep Drawdown (12%) ---")
    metrics_deep_dd = {"max_drawdown_pct": 12.0}
    plan_deep = calculate_lot_size(
        decision=decision, profile=profile, account=account, settings=settings,
        entry_price=entry_price, stop_loss=2000.0,
        performance_metrics=metrics_deep_dd
    )
    print(f"Deep DD Lot: {plan_deep.lot_size} (Expected ~{base_lot * 0.50:.2f})")
    assert abs(plan_deep.lot_size - (base_lot * 0.50)) < 0.05

    # Test 3: Losing Streak (3 losses -> 0.50x)
    print("\n--- Test 3: Losing Streak (3) ---")
    metrics_streak = {"current_loss_streak": 3}
    plan_streak = calculate_lot_size(
        decision=decision, profile=profile, account=account, settings=settings,
        entry_price=entry_price, stop_loss=2000.0,
        performance_metrics=metrics_streak
    )
    print(f"Streak Lot: {plan_streak.lot_size} (Expected ~{base_lot * 0.50:.2f})")
    assert abs(plan_streak.lot_size - (base_lot * 0.50)) < 0.05

    # Test 4: Martingale Detected (-> 0.10x)
    print("\n--- Test 4: Martingale Detected ---")
    metrics_marti = {"martingale_detected": True}
    plan_marti = calculate_lot_size(
        decision=decision, profile=profile, account=account, settings=settings,
        entry_price=entry_price, stop_loss=2000.0,
        performance_metrics=metrics_marti
    )
    print(f"Martingale Lot: {plan_marti.lot_size} (Expected ~{base_lot * 0.10:.2f})")
    assert abs(plan_marti.lot_size - (base_lot * 0.10)) < 0.05

    # Test 5: Combined (Deep DD + Streak -> 0.5 * 0.5 = 0.25x)
    print("\n--- Test 5: Combined Deep DD + Streak ---")
    metrics_combo = {"max_drawdown_pct": 11.0, "current_loss_streak": 4}
    plan_combo = calculate_lot_size(
        decision=decision, profile=profile, account=account, settings=settings,
        entry_price=entry_price, stop_loss=2000.0,
        performance_metrics=metrics_combo
    )
    print(f"Combo Lot: {plan_combo.lot_size} (Expected ~{base_lot * 0.25:.2f})")
    assert abs(plan_combo.lot_size - (base_lot * 0.25)) < 0.05

    print("\n*** ALL TESTS PASSED ***")

if __name__ == "__main__":
    test_dynamic_sizing()
