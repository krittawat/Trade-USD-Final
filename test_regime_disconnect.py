import pandas as pd
import pandas_ta as ta
import numpy as np
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from app.brain.regime import classify_regime
from app.risk.regime_filter import RegimeFilter
from app.strategy.templates.gold_scalp_pro import GoldScalpProStrategy
from app.domain.models import SymbolProfile

# Mock Data Generator
def generate_mock_candles(mode="CHOP"):
    dates = pd.date_range(start="2024-01-01", periods=100, freq="5min")
    data = {"open": [], "high": [], "low": [], "close": [], "volume": []}
    
    price = 2000.0
    
    for _ in range(100):
        if mode == "CHOP":
            change = (np.random.random() - 0.5) * 2  # -1 to +1
            close = price + change
            high = max(price, close) + 0.5
            low = min(price, close) - 0.5
            open_ = price
        elif mode == "TRENDING":
            change = np.random.random() * 2  # 0 to +2
            close = price + change
            high = close + 0.2
            low = price - 0.2
            open_ = price
            
        data["open"].append(open_)
        data["high"].append(high)
        data["low"].append(low)
        data["close"].append(close)
        data["volume"].append(1000)
        
        price = close

    df = pd.DataFrame(data, index=dates)
    return df

def test_disconnect():
    print("=== TESTING REGIME DISCONNECT ===")
    
    # Cases to test
    modes = ["CHOP", "TRENDING"]
    
    for mode in modes:
        print(f"\n--- MODE: {mode} ---")
        df = generate_mock_candles(mode)
        
        # 1. Brain Regime
        brain_regime = classify_regime(df)
        print(f"[BRAIN] Classified as: {brain_regime.value}")
        
        # 2. Risk Gate Filter
        rf = RegimeFilter()
        gate_result = rf.check(df)
        print(f"[GATE] Tradable: {gate_result.tradable} (Reason: {gate_result.reason})")
        
        # 3. Strategy Analysis
        strat = GoldScalpProStrategy()
        profile = SymbolProfile(symbol="XAUUSD", digits=2, point=0.01, contract_size=100, spread_max_allowed=50)
        decision = strat.analyze(df, profile)
        print(f"[STRAT] Action: {decision.action} (Reason: {decision.reason})")
        
        # Check consistency
        # e.g. Brain says FAKEOUT but Strat says BUY -> Disconnect
        
if __name__ == "__main__":
    test_disconnect()
