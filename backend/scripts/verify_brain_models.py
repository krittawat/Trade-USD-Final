import sys
import os
from pathlib import Path

# Add project root to path
root = Path.cwd()
sys.path.append(str(root))

from backend.trader.brain.deep_brain import get_brain

symbols = ["XAUUSD", "BTCUSD", "UKOIL"]

print("\n--- Model Loading Verification ---")
for symbol in symbols:
    try:
        brain = get_brain(symbol)
        status = "OK" if brain.model is not None else "FAILED"
        print(f"Symbol: {symbol:8} | Status: {status}")
        if brain.model:
            print(f"  Path: {brain.model_path}")
    except Exception as e:
        print(f"Symbol: {symbol:8} | Status: ERROR ({e})")

print("\n--- Done ---")
