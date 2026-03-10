
import sys
import unittest
from unittest.mock import MagicMock, patch
import json

# Mock settings
mock_settings = {
    "risk_limits": {
        "max_positions_per_symbol": {
            "BTCUSD": 1,
            "USOIL": 3,
            "XAUUSD": 2
        }
    }
}

class TestPositionLimits(unittest.TestCase):
    def test_symbol_extraction(self):
        # Simulate logic in main.py
        symbols = ["BTCUSDm", "USOILm", "XAUUSDm"]
        
        # Test BTC 1 pos
        symbol = "BTCUSDm"
        max_pos = mock_settings["risk_limits"]["max_positions_per_symbol"].get(symbol.split('m')[0], 2)
        self.assertEqual(max_pos, 1)
        
        # Test USOIL 3 pos
        symbol = "USOILm"
        max_pos = mock_settings["risk_limits"]["max_positions_per_symbol"].get(symbol.split('m')[0], 2)
        self.assertEqual(max_pos, 3)
        
        # Test Default
        symbol = "XAGUSDm"
        max_pos = mock_settings["risk_limits"]["max_positions_per_symbol"].get(symbol.split('m')[0], 2)
        self.assertEqual(max_pos, 2)

if __name__ == "__main__":
    unittest.main()
