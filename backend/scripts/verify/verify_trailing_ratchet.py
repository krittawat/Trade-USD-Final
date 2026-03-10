"""
Verification Script for Phase C: Ratchet Trailing Stop
Checks:
1. Ratchet Step 1: +1.0R -> Move to BE
2. Ratchet Step 2: +2.0R -> Lock 0.5R
3. Ratchet Step 3: +3.0R -> Lock 1.5R
"""

import sys
import os
import unittest
from unittest.mock import MagicMock

# Adjust path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../backend"))

from app.risk.trailing import TrailingManager
from app.core.config import Settings

# Mock Position Class
class MockPosition:
    def __init__(self, ticket, symbol, type, price_open, price_current, sl, tp):
        self.ticket = ticket
        self.symbol = symbol
        self.type = type # 0=BUY, 1=SELL
        self.price_open = price_open
        self.price_current = price_current
        self.sl = sl
        self.tp = tp

class TestRatchetTrailing(unittest.TestCase):
    
    def setUp(self):
        self.mt5 = MagicMock()
        self.settings = Settings()
        self.manager = TrailingManager(self.mt5, self.settings)
        
        # Setup for Gold (point=0.01)
        self.point = 0.01
        self.entry = 2000.00
        self.initial_sl = 1990.00 # Risk = 10.00
        self.sl_dist = 10.00
        
    def test_ratchet_step_1_be(self):
        """Test +1.1R -> Move to BE (Entry + Buffer)"""
        # +1.1R = Profit 11.0 -> Price 2011.0
        pos = MockPosition(
            ticket=1, symbol="XAUUSD", type=0, 
            price_open=self.entry, price_current=2011.00, 
            sl=self.initial_sl, tp=0
        )
        
        current_r = (pos.price_current - pos.price_open) / self.sl_dist
        # Call logic
        new_sl, reason = self.manager._apply_ratchet_logic(
            pos, current_r, self.entry, self.sl_dist, self.point
        )
        
        expected_sl = self.entry + (self.manager.be_buffer_points * self.point) # 2000 + 0.5 = 2000.5
        
        self.assertAlmostEqual(new_sl, expected_sl, places=2)
        print(f"[TEST] Step 1 (+1.1R): New SL {new_sl} (Expect BE {expected_sl}) -> OK")
        
    def test_ratchet_step_2_lock_half(self):
        """Test +2.1R -> Lock 0.5R"""
        # +2.1R = Profit 21.0 -> Price 2021.0
        pos = MockPosition(
            ticket=2, symbol="XAUUSD", type=0,
            price_open=self.entry, price_current=2021.00,
            sl=self.entry, tp=0
        )
        
        current_r = (pos.price_current - pos.price_open) / self.sl_dist
        new_sl, reason = self.manager._apply_ratchet_logic(
            pos, current_r, self.entry, self.sl_dist, self.point
        )
        
        # Lock 0.5R = 2000 + (0.5 * 10) = 2005.0
        expected_sl = 2005.0
        
        self.assertAlmostEqual(new_sl, expected_sl, places=2)
        print(f"[TEST] Step 2 (+2.1R): New SL {new_sl} (Expect {expected_sl}) -> OK")

    def test_ratchet_step_3_lock_winner(self):
        """Test +3.5R -> Lock 1.5R"""
        # +3.5R = Profit 35.0 -> Price 2035.0
        pos = MockPosition(
            ticket=3, symbol="XAUUSD", type=0,
            price_open=self.entry, price_current=2035.00,
            sl=2005.0, tp=0
        )
        
        current_r = (pos.price_current - pos.price_open) / self.sl_dist
        new_sl, reason = self.manager._apply_ratchet_logic(
            pos, current_r, self.entry, self.sl_dist, self.point
        )
        
        # Lock 1.5R = 2000 + (1.5 * 10) = 2015.0
        expected_sl = 2015.0
        
        self.assertAlmostEqual(new_sl, expected_sl, places=2)
        print(f"[TEST] Step 3 (+3.5R): New SL {new_sl} (Expect {expected_sl}) -> OK")

    def test_sell_logic(self):
        """Test Sell Logic Step 2"""
        # Entry 2000, SL 2010 (Risk 10), Profit +2.5R -> Price 1975
        entry = 2000.0
        sl_dist = 10.0
        current_price = 1975.0
        pos = MockPosition(
            ticket=4, symbol="XAUUSD", type=1, # SELL
            price_open=entry, price_current=current_price,
            sl=entry, tp=0
        )
        
        current_r = (entry - current_price) / sl_dist
        new_sl, reason = self.manager._apply_ratchet_logic(
            pos, current_r, entry, sl_dist, self.point
        )
        
        # Lock 0.5R = 2000 - (0.5 * 10) = 1995.0
        expected_sl = 1995.0
        self.assertAlmostEqual(new_sl, expected_sl, places=2)
        print(f"[TEST] Sell Step 2 (+2.5R): New SL {new_sl} (Expect {expected_sl}) -> OK")

if __name__ == "__main__":
    unittest.main()
