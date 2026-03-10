import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock
from app.risk.hedging import HedgeManager
from app.core.config import Settings
import MetaTrader5 as mt5

class TestHedgeManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mt5 = MagicMock()
        self.mt5.is_connected.return_value = True
        self.mt5.get_symbol_tick = MagicMock()
        self.settings = Settings()
        self.settings.hedge_enable = True
        self.settings.hedge_threshold_dd_pct = 10.0
        self.settings.hedge_threshold_usd = 100.0 # Hedge if loss > 100
        self.settings.hedge_ratio = 1.0
        
        self.manager = HedgeManager(self.mt5, self.settings)
        # Mock _execute_hedge to avoid real calls and just verify logic
        self.manager._execute_hedge = AsyncMock()

    async def test_no_hedge_normal(self):
        """Test normal condition: no hedge needed"""
        positions = [
            {'ticket': 1, 'symbol': 'XAUUSD', 'type': 'BUY', 'volume': 1.0, 'profit': 10.0, 'swap': 0.0}
        ]
        equity = 10000.0
        balance = 10000.0 # 0% DD
        
        await self.manager.monitor_risks(positions, equity, balance)
        self.manager._execute_hedge.assert_not_called()

    async def test_global_dd_hedge(self):
        """Test Global DD Trigger"""
        positions = [
            {'ticket': 1, 'symbol': 'XAUUSD', 'type': 'BUY', 'volume': 1.0, 'profit': -2000.0, 'swap': 0.0}
        ]
        balance = 10000.0
        equity = 8000.0 # 20% DD (threshold is 10%)
        
        await self.manager.monitor_risks(positions, equity, balance)
        
        # Should trigger hedge for XAUUSD (Net Buy 1.0 -> Sell 1.0)
        self.manager._execute_hedge.assert_called_with('XAUUSD', mt5.ORDER_TYPE_SELL, 1.0)

    async def test_symbol_loss_hedge(self):
        """Test Symbol Loss Trigger (USD)"""
        self.settings.hedge_threshold_usd = 50.0 # Trigger if loss > 50
        
        positions = [
            {'ticket': 1, 'symbol': 'EURUSD', 'type': 'SELL', 'volume': 0.5, 'profit': -60.0, 'swap': 0.0}
        ]
        equity = 9940.0
        balance = 10000.0 # DD is minimal, but symbol loss is huge
        
        await self.manager.monitor_risks(positions, equity, balance)
        
        # Should trigger hedge for EURUSD (Net Sell 0.5 -> Buy 0.5)
        self.manager._execute_hedge.assert_called_with('EURUSD', mt5.ORDER_TYPE_BUY, 0.5)

    async def test_already_hedged(self):
        """Test that it doesn't double hedge if net exposure is zero"""
        positions = [
            {'ticket': 1, 'symbol': 'XAUUSD', 'type': 'BUY', 'volume': 1.0, 'profit': -2000.0, 'swap': 0.0},
            {'ticket': 2, 'symbol': 'XAUUSD', 'type': 'SELL', 'volume': 1.0, 'profit': 50.0, 'swap': 0.0} # Hedged
        ]
        equity = 8000.0 # 20% DD
        balance = 10000.0
        
        await self.manager.monitor_risks(positions, equity, balance)
        
        self.manager._execute_hedge.assert_not_called()

if __name__ == '__main__':
    unittest.main()
