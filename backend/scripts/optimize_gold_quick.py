
"""
XAUUSDc 30-day Quick Optimization to Seed AI Brain.
"""

import sys
from pathlib import Path

# Fix import path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.optimize_gold_300d import run_optimization, DEFAULT_PORTFOLIOS
import logging

def main():
    # Mute all logging to prevent Unicode errors and speed up
    logging.getLogger().handlers = []
    logging.getLogger().setLevel(logging.CRITICAL)
    
    # Force 30 days, XAUUSDc symbol
    print("Starting Quick AI Training (30 Days)...")
    exit_code = run_optimization(
        days=30,
        user_symbol="XAUUSDc",
        portfolios=[1000.0, 3000.0], # Reduced portfolios for speed
        csv_path="backend/data/optimization_gold_quick.csv",
        min_pf=1.1
    )
    if exit_code == 0:
        print("\nAI Training Complete. Brain Seeded.")
    else:
        print("\nAI Training Failed.")
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
