
import pandas as pd
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))

from app.strategy.templates.gold_elite import GoldEliteStrategy
from app.domain.models import SymbolProfile

def create_dummy_candles():
    # Create 300 bars of uptrend (min 220 needed for EMA 200)
    data = {
        "time": pd.date_range(start="2024-01-01", periods=300, freq="5min"),
        "open": [100 + i for i in range(300)],
        "high": [100 + i + 2 for i in range(300)],
        "low": [100 + i - 1 for i in range(300)],
        "close": [100 + i + 1 for i in range(300)],
        "tick_volume": [100 for _ in range(300)],
    }
    df = pd.DataFrame(data)
    return df

def test_gold_elite_pressure():
    strategy = GoldEliteStrategy()
    candles = create_dummy_candles()
    profile = SymbolProfile(symbol="XAUUSDc")
    
    print("\n--- Test 1: No Pressure ---")
    decision_no_press = strategy.analyze(candles, profile)
    print(f"Score: {decision_no_press.debug.get('total_score')}")
    print(f"Confidence: {decision_no_press.confidence}")
    
    print("\n--- Test 2: High Buying Pressure (0.8) ---")
    pressure_buy = {
        "buying_pressure": 0.8,
        "selling_pressure": 0.2,
        "score": 5,
        "is_climax": False
    }
    decision_buy = strategy.analyze(candles, profile, pressure=pressure_buy)
    score_buy = decision_buy.debug.get('total_score')
    print(f"Score: {score_buy}")
    print(f"Confidence: {decision_buy.confidence}")
    print(f"Reason: {decision_buy.reason}")
    
    if score_buy > decision_no_press.debug.get('total_score'):
        print("✅ SUCCESS: Buying Pressure boosted score!")
    else:
        print("❌ FAIL: No score boost from Buying Pressure.")

    print("\n--- Test 3: High Selling Pressure (0.8) on Uptrend ---")
    pressure_sell = {
        "buying_pressure": 0.2,
        "selling_pressure": 0.8,
        "score": -5,
        "is_climax": True
    }
    decision_sell = strategy.analyze(candles, profile, pressure=pressure_sell)
    score_sell = decision_sell.debug.get('total_score')
    print(f"Score: {score_sell}")
    print(f"Reason: {decision_sell.reason}")
    
    # In uptrend, selling pressure should boost SELL score, but since trend is UP, total action depends on net score.
    # Gold Elite prioritizes Trend. If Trend is UP, Buy Score is high.
    # Selling Pressure adds to Sell Score.
    # We expect SELL score to increase.
    print(f"Buy Score: {decision_sell.debug.get('buy_score')}")
    print(f"Sell Score: {decision_sell.debug.get('sell_score')}")
    
    if decision_sell.debug.get('sell_score') > decision_no_press.debug.get('sell_score'):
        print("✅ SUCCESS: Selling Pressure boosted Sell Score!")
    else:
        print("❌ FAIL: No boost to Sell Score.")

if __name__ == "__main__":
    test_gold_elite_pressure()
