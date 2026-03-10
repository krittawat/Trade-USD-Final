
import sys
import os
import unittest
import unittest
from unittest.mock import MagicMock, patch

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

# Mock logger before importing recommender to allow safe import if it uses get_logger at module level
with patch('app.core.logging.get_logger'):
    from app.brain.recommender import Recommender
    from app.brain.memory_store import MemoryStore

class TestRecommenderFallback(unittest.TestCase):
    def setUp(self):
        self.memory = MagicMock(spec=MemoryStore)
        # Mock memory to return None for get_best_strategy
        self.memory.get_best_strategy.return_value = None
        self.recommender = Recommender(self.memory)
        # Verify logger is mocked or we can patch it on the instance if needed
        # But since we import logger in recommender, we should patch it there
        self.logger_patcher = patch('app.brain.recommender.logger')
        self.mock_logger = self.logger_patcher.start()

    def tearDown(self):
        self.logger_patcher.stop()

    def test_fallback_gold(self):
        # Gold Trend -> gold_elite
        self.assertEqual(self.recommender.recommend("XAUUSD", "TRENDING_UP"), "gold_elite")
        # Gold Volatile -> gold_scalp_pro
        self.assertEqual(self.recommender.recommend("XAUUSD", "HIGH_VOLATILITY"), "gold_scalp_pro")
        # Gold Range -> scalping? or gold_elite? (fallback checks RANGE/SIDEWAYS -> scalping)
        self.assertEqual(self.recommender.recommend("XAUUSD", "RANGING"), "scalping")

    def test_fallback_generic(self):
        # Generic Trend -> trend_rider
        self.assertEqual(self.recommender.recommend("EURUSD", "TRENDING_UP"), "trend_rider")
        # Generic Range -> scalping
        self.assertEqual(self.recommender.recommend("EURUSD", "RANGING"), "scalping")
        # Generic Volatile -> sniper
        self.assertEqual(self.recommender.recommend("GBPUSD", "HIGH_VOLATILITY"), "sniper")

    def test_fallback_unknown(self):
        # Unknown -> sniper
        self.assertEqual(self.recommender.recommend("USDJPY", "UNKNOWN"), "sniper")

if __name__ == '__main__':
    unittest.main()
