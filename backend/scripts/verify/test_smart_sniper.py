"""
Script to verify SmartSniper logic.
Tests:
1. Initialization
2. Regime Metrics (ADX)
3. Audit Feedback Integration (Session Safety)
4. Decision Making (Hold on Unsafe/Low ADX)
"""

import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np
import logging

# Add backend to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from app.core.config import Settings
from app.domain.enums import RegimeType, Action
from app.domain.models import SymbolProfile
from app.strategy.templates.smart_sniper import SmartSniper

# Mock Settings
settings = Settings()

# Setup Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_mock_candles(length=300, trend=True, volatile=True):
    """Create mock candles with specific characteristics"""
    dates = pd.date_range(end=pd.Timestamp.now(), periods=length, freq="15min")
    
    if trend:
        # Uptrend (Steeper slope for higher ADX)
        close = np.linspace(2000, 2100, length)
    else:
        # Sideways
        close = np.linspace(2000, 2000, length)
        
    if volatile:
        # High noise for high ADX/ATR? 
        # Actually ADX needs TREND. ATR needs Volatility.
        # SmartSniper needs ADX > 20.
        if trend:
            # Strong trend, moderate noise (Clean trend = High ADX)
            noise = np.random.normal(0, 2, length)
        else:
            # Choppy (High Volatility but No Trend)
            noise = np.random.normal(0, 5, length)
    else:
        noise = np.random.normal(0, 0.5, length) # Low noise
        
    close += noise
    open_ = close - noise * 0.5
    high = close + abs(noise)
    low = close - abs(noise)
    
    df = pd.DataFrame({
        "time": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": np.random.randint(100, 1000, length)
    })
    return df

def test_smart_sniper():
    print(">>> Testing SmartSniper Initialization...")
    strategy = SmartSniper(settings)
    print(f"Strategy Name: {strategy.name}")
    print(f"Audit Data Loaded: {bool(strategy.audit_data)}")
    
    profile = SymbolProfile(symbol="XAUUSDc")
    
    # Test 1: Low Volatility (Sideways) -> Should HOLD (ADX Filter)
    print("\n>>> Test 1: Low Volatility (Sideways)")
    candles_sideways = create_mock_candles(trend=False, volatile=False)
    decision = strategy.analyze(candles_sideways, profile, RegimeType.LOW_VOLATILITY, session="LONDON")
    print(f"Decision: {decision.action} ({decision.reason})")
    
    if decision.action != Action.HOLD or "Low Volatility" not in decision.reason:
         print("FAIL: Should hold on low volatility")
    else:
         print("PASS: Held on low volatility")

    # Test 2: Unsafe Session
    print("\n>>> Test 2: Unsafe Session Check")
    # Mock audit data to simulate bleed in ASIA
    strategy.audit_data = {
        "root_causes": {
            "session_bleed": {
                "0": -20.0, "1": -20.0, "2": -20.0 # Total -60
            }
        }
    }
    decision = strategy.analyze(candles_sideways, profile, RegimeType.RANGING, session="ASIA") # 0-8 UTC matches mocked bleed
    print(f"Decision: {decision.action} ({decision.reason})")
    
    if decision.action != Action.HOLD or "Unsafe Session" not in decision.reason:
        print("FAIL: Should hold on unsafe session")
    else:
        print("PASS: Held on unsafe session")

    # Test 3: Good Condition (Trend + Safe Session)
    print("\n>>> Test 3: Good Condition (Trend + Safe Session)")
    candles_trend = create_mock_candles(trend=True, volatile=True)
    # Ensure ADX is high enough by adding enough volatility/trend
    
    # Mock safe session
    strategy.audit_data = {} 
    
    # Note: Logic inside Sniper might still return HOLD if specific patterns (FVG/Structure) are not met.
    # We just want to ensure it passes the Smart Gates (Session/ADX).
    # If it returns HOLD due to "Confluence" that is fine, as long as it passed Smart Gates.
    
    decision = strategy.analyze(candles_trend, profile, RegimeType.TRENDING_UP, session="NY")
    print(f"Decision: {decision.action} ({decision.reason})")
    
    if "Unsafe Session" in decision.reason or "Low Volatility" in decision.reason:
        print("FAIL: Blocked by Smart Gates incorrectly")
    else:
        print("PASS: Passed Smart Gates (Decision based on core logic)")

if __name__ == "__main__":
    test_smart_sniper()
