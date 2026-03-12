import sys
import os
import pandas as pd
import numpy as np
from typing import Optional
from dataclasses import dataclass

# Setup sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from app.execution.backtester import Backtester
from app.domain.models import Decision, SymbolProfile
from app.domain.enums import Action

class MockStrategy:
    def analyze(self, candles: pd.DataFrame, **kwargs) -> Decision:
        idx = candles.index[-1]
        close = candles['close'].iloc[-1]
        # Buy at index 20, 40, 60...
        if idx > 10 and idx % 20 == 0:
            return Decision(
                symbol="XAUUSD",
                action=Action.BUY,
                confidence=0.9,
                reason="Mock Buy Signal",
                stop_loss=float(close - 10),
                take_profit=float(close + 20),
                strategy_name="MockStrategy"
            )
        return Decision(
            symbol="XAUUSD",
            action=Action.HOLD,
            confidence=0.0,
            reason="No Signal",
            strategy_name="MockStrategy"
        )

def run_test():
    print("--- Starting Backtester Verification ---")
    
    # 1. Create dummy data (100 bars)
    # Price trending up slightly
    data = {
        'open': np.linspace(2000, 2100, 100),
        'high': np.linspace(2002, 2102, 100),
        'low': np.linspace(1998, 2098, 100),
        'close': np.linspace(2000, 2100, 100),
        'tick_volume': [100] * 100
    }
    df = pd.DataFrame(data)
    df.index.name = 'time'
    
    # 2. Mock Strategy
    strat = MockStrategy()
    
    # 3. Initialize Backtester
    bt = Backtester(
        strat, 
        initial_equity=10000.0, 
        warmup_bars=10,
        risk_per_trade=0.02 # 2% risk
    )
    
    # 4. Run backtest
    print("Running Backtest on XAUUSD...")
    try:
        result = bt.run(df, symbol="XAUUSD")
        
        print(f"Stats:")
        print(f"  Total Trades: {result.total_trades}")
        print(f"  Win Rate: {result.win_rate}%")
        print(f"  Final Equity: {result.final_equity}")
        print(f"  Max Drawdown: {result.max_drawdown_pct}%")
        
        if result.total_trades > 0:
            print("Trades:")
            for t in result.trades:
                print(f"  - {t['entry_time']}: {t['action']} at {t['entry']} -> {t['exit']} (PnL: {t['pnl']})")
        
        # Basic assertions
        assert result.total_trades > 0, "Backtest should have executed trades"
        assert result.final_equity > 0, "Final equity should be positive"
        
        print("\n--- Backtester Verification: PASSED ---")
        
    except Exception as e:
        print(f"\n--- Backtester Verification: FAILED ---")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    run_test()
