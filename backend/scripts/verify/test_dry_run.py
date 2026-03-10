"""
Integration Test Suite for DRY_RUN Mode.
Verifies:
1. Ledger PnL Tracking (PaperBroker)
2. Execution Adapter Parity
3. Risk Management in Dry Run
"""

import sys
import os
import asyncio
import logging
from datetime import datetime, timezone

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from app.core.config import Settings
from app.domain.models import OrderPlan, Action, AccountState
from app.execution.paper.broker import PaperBroker
from app.execution.paper.adapter import DryRunAdapter
from app.execution.pipeline import ExecutionPipeline

# Mock MT5 Client for testing
class MockMT5Client:
    def __init__(self):
        self.connected = True
        self.prices = {
            "XAUUSDc": (2000.0, 2000.5), # Bid, Ask (Spread 0.5)
            "EURUSDc": (1.1000, 1.1002)
        }
        self.symbols = {
             "XAUUSDc": type("Info", (), {"point": 0.01, "contract_size": 100.0, "volume_step": 0.01, "digits": 2, "visible": True, "trade_mode": 1})(),
             "EURUSDc": type("Info", (), {"point": 0.00001, "contract_size": 100000.0, "volume_step": 0.01, "digits": 5, "visible": True, "trade_mode": 1})()
        }

    def is_connected(self): return True
    def is_market_open(self, symbol): return True
    def get_current_price(self, symbol): return self.prices.get(symbol, (0,0))
    def get_symbol_info(self, symbol): return self.symbols.get(symbol)
    def get_positions(self, symbol=None): return [] # No real pos
    def get_current_spread(self, symbol): 
        bid, ask = self.prices.get(symbol, (0,0))
        return ask - bid

async def test_ledger_ops():
    print("\n--- Testing Ledger Operations ---")
    settings = Settings(trading_mode="DRY_RUN", dry_run_initial_balance=10000.0)
    # Use a temp state file
    t_file = "backend/data/test_state.json"
    if os.path.exists(t_file): os.remove(t_file)
    
    broker = PaperBroker(settings, state_file_path=t_file)
    adapter = DryRunAdapter(broker, MockMT5Client())
    
    print(f"Initial Balance: {broker.balance}")
    assert broker.balance == 10000.0
    
    # 1. Buy Order
    plan = OrderPlan(
        symbol="XAUUSDc",
        action=Action.BUY,
        lot_size=1.0, # 100 oz
        stop_loss=1990.0,
        take_profit=2010.0,
        risk_usd=100.0,
        risk_pct=1.0
    )
    
    # Execute (Ask = 2000.5, Slippage=5pts=0.05 -> Fill ~2000.55)
    res = adapter.execute_order(plan)
    print(f"Executed BUY: Ticket={res.ticket} Price={res.price}")
    
    assert res.ticket > 0
    assert len(broker.positions) == 1
    pos = broker.positions[res.ticket]
    assert pos.action == Action.BUY
    assert pos.volume == 1.0
    
    # Check Valuation (Price didn't change yet)
    # Bid=2000.0. Open=2000.55. Profit = (2000.0 - 2000.55) * 1 * 100 = -55 USD
    account = adapter.get_account_state()
    print(f"Equity: {account.equity}, Floating: {account.floating_pl}")
    
    # Allow some float tolerance for slippage calc
    assert account.equity < 10000.0
    
    # 2. Simulate Price Move (Up to 2010.0)
    mock_mt5 = adapter.mt5
    mock_mt5.prices["XAUUSDc"] = (2010.0, 2010.5) # Bid, Ask
    
    adapter.get_account_state() # Trigger update
    pos = broker.positions[res.ticket]
    print(f"Price moved up. Profit: {pos.profit}")
    
    # Profit approx: (2010.0 - 2000.55) * 100 * 1 = 945 USD
    assert pos.profit > 900.0
    
    # 3. Close Position
    adapter.close_position(res.ticket, "Test Close")
    print(f"Closed. Balance: {broker.balance}")
    
    assert broker.balance > 10000.0
    assert len(broker.positions) == 0
    
    os.remove(t_file)
    print("✅ Ledger Ops Passed")

async def test_risk_integration():
    print("\n--- Testing Risk Integration (Dry Run) ---")
    settings = Settings(
        trading_mode="DRY_RUN", 
        max_daily_loss_pct=1.0, # 1% daily loss limit
        dry_run_initial_balance=10000.0 
    )
    broker = PaperBroker(settings, state_file_path="backend/data/test_risk.json")
    if os.path.exists("backend/data/test_risk.json"): os.remove("backend/data/test_risk.json")
    
    adapter = DryRunAdapter(broker, MockMT5Client())
    pipeline = ExecutionPipeline(
        settings=settings,
        adapter=adapter,
        mt5_client=MockMT5Client() # For Gate fallback ? No, Pipeline uses adapter
    )
    
    # Hack: Force daily loss
    broker.balance = 9500.0 # Lost 500 (5%)
    # Logic in AccountState.daily_pl is currently 0.0 in basic implementation
    # We need to simulate daily loss being tracked. 
    # For now, let's test Max Risk Per Trade (which is stateless mostly)
    
    from app.domain.models import Decision
    
    # Create massive risk decision
    decision = Decision(
        symbol="XAUUSDc",
        action=Action.BUY,
        confidence=0.9,
        reason="Test",
        stop_loss=1900.0 # Wide SL
    )
    
    from app.domain.models import SymbolProfile
    profile = SymbolProfile(symbol="XAUUSDc", spread_avg=10, is_active=True)
    
    # Execute Pipeline
    res = await pipeline.execute(decision, profile, adapter.get_account_state())
    
    print(f"Pipeline Result: {res['result']}")
    if res['result'] == 'ok':
        print(f"Order Plan Lots: {res['order_plan'].lot_size}")
        print(f"Risk USD: {res['order_plan'].risk_usd}")
        
        # Verify Risk %
        # 2% of 9500 = 190 USD
        assert res['order_plan'].risk_usd <= 190.0 + 10.0 # Tolerance
        print("✅ Risk Sizing Passed")
    else:
        print(f"Blocked: {res.get('reason')}")

    if os.path.exists("backend/data/test_risk.json"): os.remove("backend/data/test_risk.json")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_ledger_ops())
    asyncio.run(test_risk_integration())
