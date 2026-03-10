# -*- coding: utf-8 -*-
"""
XAU Multi-Timeframe Tournament Runner
=====================================
Runs the XAU tournament backtest across multiple timeframes (M3, M5, M6, M12, M15, H1, H2, H4)
and summarizes the results.

Usage:
  python backend/scripts/run_xau_tournament_multi_tf.py
"""
import subprocess
import os
import sys
import json
from datetime import datetime

# List of timeframes to test as requested
TIME_FRAMES = ["M2", "M3", "M5", "M12", "M15", "M30", "H1", "H4", "D1"]

# Scaling bars based on TF to cover approximately 60 days of history
TF_BARS = {
    "M2": 43200,
    "M3": 28800,
    "M5": 17280,
    "M12": 7200,
    "M15": 6000,
    "M30": 2880,
    "H1": 1500,
    "H4": 400,
    "D1": 60
}

def run_tournament(tf, bars):
    print(f"\n" + "="*70)
    print(f"🏆 STARTING XAU TOURNAMENT FOR TF: {tf} ({bars} bars)")
    print("="*70)
    
    cmd = [
        sys.executable, 
        "backend/scripts/backtest_xau_tournament.py",
        "--symbol", "XAUUSDm",
        "--tf", tf,
        "--bars", str(bars)
    ]
    
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running tournament for {tf}: {e}")
        return False

if __name__ == "__main__":
    print("\n" + "#"*70)
    print("🔥 ANTIGRAVITY XAU MULTI-TIMEFRAME TOURNAMENT SUITE 🔥")
    print("#"*70)
    
    start_time = datetime.now()
    success_count = 0
    
    for tf in TIME_FRAMES:
        bars = TF_BARS.get(tf, 1000)
        if run_tournament(tf, bars):
            success_count += 1
            
    end_time = datetime.now()
    duration = end_time - start_time
    
    print("\n" + "="*70)
    print("✅ MULTI-TF TOURNAMENT COMPLETE")
    print(f"   Timeframes tested: {success_count}/{len(TIME_FRAMES)}")
    print(f"   Total Duration:    {duration}")
    print("   Check individual JSON results in backend/trader/data/")
    print("="*70 + "\n")
