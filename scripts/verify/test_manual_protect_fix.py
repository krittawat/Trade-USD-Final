
import asyncio
import logging
from unittest.mock import MagicMock, AsyncMock

# Setup logger
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("app.risk.postfill")

# Mock MT5Client
class MockMT5Client:
    def __init__(self):
        self.positions = []
        self.closed_tickets = []
        self.modified_tickets = []

    def get_positions(self):
        return self.positions

    def close_position(self, ticket):
        print(f"!!! CLOSING POSITION {ticket} !!!")
        self.closed_tickets.append(ticket)
        # Remove from positions
        # self.positions = [p for p in self.positions if p['ticket'] != ticket]
        return True
    
    def disconnect(self):
        pass

    def modify_sl(self, ticket, new_sl, new_tp=0.0):
        print(f"!!! MODIFYING POSITION {ticket} SL={new_sl} TP={new_tp} !!!")
        self.modified_tickets.append((ticket, new_sl, new_tp))
        return True

# Import PostFillGuard (we need to inject the mock)
# Mock app.risk.postfill import since it won't resolve in script dir easily without PYTHONPATH
# We'll just define the class logic here for isolated test OR import if possible.
# Better to import to test actual code. But need sys.path.

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../backend')))

from app.risk.postfill import PostFillGuard

async def test_manual_protection_fix():
    print("--- Starting Test: Manual Trade Protection Fix ---")
    
    # Setup
    client = MockMT5Client()
    guard = PostFillGuard(client)
    
    # Scene 1: Manual Trade (Magic=0) without SL
    # Expectation: PostFillGuard should IGNORE it (not close it)
    print("\n[Scene 1] Manual Trade (Magic=0, No SL)")
    manual_pos = {
        'ticket': 1001,
        'symbol': 'XAUUSD',
        'magic': 0,
        'sl': 0.0,
        'tp': 0.0,
        'type': 'BUY',
        'volume': 0.01,
        'price_open': 2000.0,
        'price_current': 2000.0
    }
    client.positions = [manual_pos]
    
    await guard.verify_all_active()
    
    if 1001 in client.closed_tickets:
        print("❌ FAILED: Manual trade was closed!")
    else:
        print("✅ PASSED: Manual trade was NOT closed.")

    # Scene 2: Bot Trade (Magic=888888) without SL
    # Expectation: PostFillGuard should CLOSE it (strict safety)
    print("\n[Scene 2] Bot Trade (Magic=888888, No SL)")
    bot_pos = {
        'ticket': 2001,
        'symbol': 'XAUUSD',
        'magic': 888888,
        'sl': 0.0, # Naked!
        'tp': 0.0,
        'type': 'BUY',
        'volume': 0.01,
        'price_open': 2000.0,
        'price_current': 2000.0
    }
    client.positions = [bot_pos]
    client.closed_tickets = [] # Reset
    
    await guard.verify_all_active()
    
    if 2001 in client.closed_tickets:
        print("✅ PASSED: Naked Bot trade was closed safely.")
    else:
        print("❌ FAILED: Naked Bot trade was NOT closed!")

if __name__ == "__main__":
    asyncio.run(test_manual_protection_fix())
