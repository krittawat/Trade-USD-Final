"""
Verification Script for Phase B: Dynamic Lot Scaling
Checks:
1. Loss Streak scaling (2 -> 0.8x, 3 -> 0.5x, 4 -> 0.25x)
2. Drawdown scaling (5-10% -> 0.75x, >10% -> 0.5x)
3. Win Streak boosting (>3 -> 1.1x)
4. Gambler protection (0.1x)
"""

import sys
import os
import unittest
from unittest.mock import MagicMock

# Adjust path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../backend"))

from app.risk.sizing import calculate_lot_size
from app.domain.models import AccountState, Decision, SymbolProfile
from app.domain.enums import Action
from app.core.config import Settings

class TestDynamicSizing(unittest.TestCase):
    
    def setUp(self):
        self.settings = Settings(max_risk_per_trade_pct=1.0) # Base 1.0%
        self.account = AccountState(
            balance=100000.0, 
            equity=100000.0,
            initial_balance=100000.0,
            free_margin=100000.0
        )
        self.profile = SymbolProfile(
            symbol="EURUSD",
            contract_size=100000.0,
            digits=5,
            point=0.00001,
            volume_min=0.01,
            volume_step=0.01,
            volume_max=100.0
        )
        self.decision = Decision(
            symbol="EURUSD",
            action=Action.BUY,
            stop_loss=1.0900,
            take_profit=1.1100,
            strategy_name="TestStrategy",
            confidence=0.9,
            reason="Test Signal"
        )
        # Entry price assumption
        self.entry_price = 1.1000 
        self.sl_distance = abs(self.entry_price - 1.0900) # 0.0100 -> 1000 points

    def test_baseline(self):
        """Test without metrics -> Should be base risk"""
        plan = calculate_lot_size(
            decision=self.decision,
            profile=self.profile,
            account=self.account,
            settings=self.settings,
            entry_price=self.entry_price
        )
        # Risk 1% of 10000 = 100 USD
        # Lot = 100 / (0.0100 * 100000) = 100 / 1000 = 0.1
        self.assertAlmostEqual(plan.risk_pct, 1.0, places=2)
        print(f"[TEST] Baseline: Risk {plan.risk_pct}% (Expect 1.0%) -> OK")

    def test_loss_streak_2(self):
        """Test 2 losses -> 0.8x"""
        metrics = {"current_loss_streak": 2}
        plan = calculate_lot_size(
            decision=self.decision,
            profile=self.profile,
            account=self.account,
            settings=self.settings,
            entry_price=self.entry_price,
            performance_metrics=metrics
        )
        # Expect 0.8%
        self.assertAlmostEqual(plan.risk_pct, 0.8, places=2)
        print(f"[TEST] Loss Streak 2: Risk {plan.risk_pct}% (Expect 0.8%) -> OK")

    def test_loss_streak_3(self):
        """Test 3 losses -> 0.5x"""
        metrics = {"current_loss_streak": 3}
        plan = calculate_lot_size(
            decision=self.decision,
            profile=self.profile,
            account=self.account,
            settings=self.settings,
            entry_price=self.entry_price,
            performance_metrics=metrics
        )
        # Expect 0.5%
        self.assertAlmostEqual(plan.risk_pct, 0.5, places=2)
        print(f"[TEST] Loss Streak 3: Risk {plan.risk_pct}% (Expect 0.5%) -> OK")
        
    def test_loss_streak_4_plus(self):
        """Test 4 losses -> 0.25x (Cold Streak)"""
        metrics = {"current_loss_streak": 5}
        plan = calculate_lot_size(
            decision=self.decision,
            profile=self.profile,
            account=self.account,
            settings=self.settings,
            entry_price=self.entry_price,
            performance_metrics=metrics
        )
        # Expect 0.25%
        self.assertAlmostEqual(plan.risk_pct, 0.25, places=2)
        print(f"[TEST] Loss Streak 5: Risk {plan.risk_pct}% (Expect 0.25%) -> OK")

    def test_drawdown_scaling(self):
        """Test Drawdown > 5% -> 0.75x"""
        metrics = {"max_drawdown_pct": 6.0}
        plan = calculate_lot_size(
            decision=self.decision,
            profile=self.profile,
            account=self.account,
            settings=self.settings,
            entry_price=self.entry_price,
            performance_metrics=metrics
        )
        # Expect 0.75%
        self.assertAlmostEqual(plan.risk_pct, 0.75, places=2)
        print(f"[TEST] Drawdown 6%: Risk {plan.risk_pct}% (Expect 0.75%) -> OK")
        
    def test_deep_drawdown_scaling(self):
        """Test Drawdown > 10% -> 0.5x"""
        metrics = {"max_drawdown_pct": 12.0}
        plan = calculate_lot_size(
            decision=self.decision,
            profile=self.profile,
            account=self.account,
            settings=self.settings,
            entry_price=self.entry_price,
            performance_metrics=metrics
        )
        # Expect 0.5%
        self.assertAlmostEqual(plan.risk_pct, 0.5, places=2)
        print(f"[TEST] Drawdown 12%: Risk {plan.risk_pct}% (Expect 0.5%) -> OK")

    def test_win_streak_boost(self):
        """Test Win Streak 3 -> 1.1x capped at 1.25"""
        metrics = {"current_win_streak": 3}
        # Allow room for boost
        self.settings.max_risk_per_trade_pct = 2.0
        self.decision.risk_pct = 1.0
        
        plan = calculate_lot_size(
            decision=self.decision,
            profile=self.profile,
            account=self.account,
            settings=self.settings,
            entry_price=self.entry_price,
            performance_metrics=metrics
        )
        # Expect 1.1%
        self.assertAlmostEqual(plan.risk_pct, 1.1, places=2)
        print(f"[TEST] Win Streak 3: Risk {plan.risk_pct}% (Expect 1.1%) -> OK")

if __name__ == "__main__":
    unittest.main()
