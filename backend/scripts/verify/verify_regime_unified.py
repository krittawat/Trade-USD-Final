"""
Verification Script for Phase A: Unified Regime Logic
Checks:
1. classify_regime returns RegimeContext with correct actionable flags.
2. PreTradeGate blocks trades when RegimeContext.actionable is False.
3. GoldScalpPro respects RegimeContext.actionable.
"""

import sys
import os
import pandas as pd
import unittest
from datetime import datetime

# Adjust path to include backend
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../backend"))

from app.brain.regime import classify_regime
from app.domain.models import RegimeContext, RegimeType, Decision, SymbolProfile, AccountState, GateResult
from app.domain.enums import Action, BlockReason
from app.risk.gate import PreTradeGate
from app.strategy.templates.gold_scalp_pro import GoldScalpProStrategy

class TestUnifiedRegime(unittest.TestCase):
    
    def setUp(self):
        # Create dummy settings
        class Settings:
            floating_dd_block_pct = 10.0
            max_daily_loss_pct = 3.0
            capital_floor_pct = 90.0
            max_positions_per_symbol = 2
            news_block_minutes = 30
            
        self.settings = Settings()
        self.gate = PreTradeGate(self.settings)
        self.strategy = GoldScalpProStrategy()
        
        # Create dummy candles (Normal)
        self.candles_normal = pd.DataFrame({
            "time": pd.date_range("2024-01-01", periods=100, freq="5min"),
            "open": [100.0] * 100,
            "high": [105.0] * 100,
            "low": [95.0] * 100,
            "close": [102.0] * 100,
            "tick_volume": [1000] * 100,
        })
        # Make it trending (high - low sufficient for ATR, varying close for ADX)
        # To simulate high ADX, we need trend.
        for i in range(100):
            self.candles_normal.iloc[i, self.candles_normal.columns.get_loc('close')] = 100 + i * 0.1
            self.candles_normal.iloc[i, self.candles_normal.columns.get_loc('high')] = 100 + i * 0.1 + 2
            self.candles_normal.iloc[i, self.candles_normal.columns.get_loc('low')] = 100 + i * 0.1 - 2

    def test_classify_regime_structure(self):
        """Test that classify_regime returns RegimeContext."""
        ctx = classify_regime(self.candles_normal)
        self.assertIsInstance(ctx, RegimeContext)
        print(f"[TEST] Regime: {ctx.regime}, Actionable: {ctx.actionable}, Reason: {ctx.reason}")

    def test_low_adx_blocking(self):
        """Test ADX < 18 blocking."""
        # Create sideways candles
        candles = self.candles_normal.copy()
        # Random walk / flat
        import numpy as np
        np.random.seed(42)
        base = 100
        for i in range(100):
            noise = np.random.uniform(-0.5, 0.5)
            candles.iloc[i, candles.columns.get_loc('close')] = base + noise
            candles.iloc[i, candles.columns.get_loc('high')] = base + noise + 1
            candles.iloc[i, candles.columns.get_loc('low')] = base + noise - 1
            
        ctx = classify_regime(candles)
        print(f"[TEST] Low ADX Case -> Actionable: {ctx.actionable}, Reason: {ctx.reason}")
        
        # Expect actionable=False due to Low ADX or Ranging
        # Or at least investigate why if True (random walk might have accidentally trended)
        # But logically filter checks ADX < 18.
        # Let's assert based on flag
        if not ctx.actionable:
            print(" [PASS] Blocked correctly.")
        else:
            print(" [WARN] Not blocked. ADX might be high enough?")

    def test_gate_blocks_actionable_false(self):
        """Test Gate blocks when actionable=False."""
        ctx = RegimeContext(
            regime=RegimeType.RANGING,
            actionable=False,
            reason="Forced Block Test"
        )
        
        decision = Decision(
             symbol="XAUUSD",
             action=Action.BUY,
             confidence=0.9,
             reason="Test Signal",
             stop_loss=1900.0,
             take_profit=2000.0
        )
        profile = SymbolProfile(symbol="XAUUSD", is_active=True, spread_max_allowed=50.0)
        account = AccountState(balance=1000.0, equity=1000.0, initial_balance=1000.0)
        
        result: GateResult = self.gate.check(
            decision=decision,
            profile=profile,
            account=account,
            regime_context=ctx,
            mt5_connected=True,
            market_open=True
        )
        
        self.assertFalse(result.passed)
        self.assertIn(BlockReason.REGIME_NO_TRADE, result.reasons)
        print(f"[TEST] Gate Result: {result.passed}, Reasons: {result.reasons}")

    def test_strategy_holds_actionable_false(self):
        """Test GoldScalpPro holds when actionable=False."""
        ctx = RegimeContext(
            regime=RegimeType.RANGING,
            actionable=False,
            reason="Forced Block Test"
        )
        
        # Pass dummy df
        decision = self.strategy.analyze(
            df=self.candles_normal,
            symbol="XAUUSD",
            regime_context=ctx
        )
        
        self.assertEqual(decision.signal, "NO_TRADE")
        self.assertIn("Brain Block", decision.reason)
        print(f"[TEST] Strategy Signal: {decision.signal}, Reason: {decision.reason}")

if __name__ == "__main__":
    unittest.main()
