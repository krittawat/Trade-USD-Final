# -*- coding: utf-8 -*-
import subprocess
import os
import sys

# List of timeframes to test
TIME_FRAMES = ["M2", "M3", "M5", "M6", "M12", "M15", "M30", "H1", "H2", "H4", "D1"]

# Scaling bars based on TF (to cover similar historical period ~60 days)
TF_BARS = {
    "M2": 43200,
    "M3": 28800,
    "M5": 18000,
    "M6": 14400,
    "M12": 7200,
    "M15": 6000,
    "M30": 2880,
    "H1": 1500,
    "H2": 720,
    "H4": 400,
    "D1": 60
}

def run_tournament(tf, bars):
    print(f"\n" + "="*50)
    print(f"🚀 STARTING TOURNAMENT FOR TF: {tf} ({bars} bars)")
    print("="*50)
    
    cmd = [
        sys.executable, 
        "backend/scripts/backtest_btc_tournament.py",
        "--symbol", "BTCUSDm",
        "--tf", tf,
        "--bars", str(bars)
    ]
    
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running tournament for {tf}: {e}")

if __name__ == "__main__":
    print("🔥 ANTIGRAVITY MULTI-TIMEFRAME BACKTEST SUITE 🔥")
    
    for tf in TIME_FRAMES:
        bars = TF_BARS.get(tf, 1000)
        run_tournament(tf, bars)
        
    print("\n✅ ALL TIMEFRAMES TESTED. Check individual JSON results in backend/trader/data/")
