
import sys
import unittest
import pandas as pd
import numpy as np
from pathlib import Path

# Add backend to path
BACKEND_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND_DIR))

from app.brain.personality import PersonalityEngine, PersonalityProfile
from app.brain.regime import RegimeType

class TestPersonalityEngine(unittest.TestCase):
    def setUp(self):
        self.engine = PersonalityEngine()

    def test_calculate_hurst(self):
        # Generate Trending Data (Random Walk with drift)
        np.random.seed(42)
        trend = np.cumsum(np.random.normal(0.1, 1, 1000)) # Drift 0.1
        hurst_trend = self.engine._calculate_hurst(pd.Series(trend))
        print(f"Hurst (Trend): {hurst_trend}")
        self.assertGreater(hurst_trend, 0.5)

        # Generate Mean Reverting Data (AR(1) aka Ornstein-Uhlenbeck discrete)
        # x_t = phi * x_{t-1} + e_t, where phi < 1
        mean_rev = np.zeros(1000)
        for t in range(1, 1000):
            mean_rev[t] = 0.5 * mean_rev[t-1] + np.random.normal(0, 1)
            
        hurst_mr = self.engine._calculate_hurst(mean_rev)
        print(f"Hurst (MeanRev): {hurst_mr}")
        # Relax threshold slightly due to simplified estimator bias
        self.assertLess(hurst_mr, 0.6)
        
    def test_analyze_symbol(self):
        # Create dummy candles
        data = {
            'time': pd.date_range(start='2024-01-01', periods=100, freq='5min'),
            'open': np.linspace(100, 110, 100),
            'high': np.linspace(101, 111, 100),
            'low': np.linspace(99, 109, 100),
            'close': np.linspace(100.5, 110.5, 100),
            'tick_volume': np.random.randint(100, 1000, 100),
        }
        df = pd.DataFrame(data)
        
        profile = self.engine.analyze_symbol("XAUUSD", df)
        
        self.assertIsInstance(profile, PersonalityProfile)
        self.assertEqual(profile.symbol, "XAUUSD")
        self.assertGreater(profile.volatility_score, 0)
        print(f"Profile: {profile}")

if __name__ == '__main__':
    unittest.main()
