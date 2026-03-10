import os
from pathlib import Path

mt5_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
print(f"Checking MT5 Path: {mt5_path}")
if os.path.exists(mt5_path):
    print("SUCCESS: MT5 terminal found.")
else:
    print("ERROR: MT5 terminal NOT found at this path.")

# Check for exness variations
variations = [
    r"C:\Program Files\Exness MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\Exness MetaTrader 5\terminal64.exe"
]

for v in variations:
    if os.path.exists(v):
        print(f"FOUND alternate: {v}")
