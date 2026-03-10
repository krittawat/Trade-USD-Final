import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import logging
import sys
from pathlib import Path

# Add project root to path (d:\VibeCode\Trade)
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.trader.brain.deep_brain import get_brain
from backend.trader.observability.logger import setup_logger

logger = logging.getLogger("train_ukoil")

def train_ukoil():
    if not mt5.initialize():
        print("MT5 initialization failed")
        return

    symbol = "UKOIL"
    print(f"Fetching historical data for {symbol}...")
    
    # Fetch 10,000 M5 bars (~34 days)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 10000)
    
    if rates is None or len(rates) == 0:
        # Try with prefix/suffix
        for alt in [f"{symbol}m", f"{symbol}c"]:
            rates = mt5.copy_rates_from_pos(alt, mt5.TIMEFRAME_M5, 0, 10000)
            if rates is not None and len(rates) > 0:
                symbol = alt
                break
                
    if rates is None or len(rates) == 0:
        print(f"Could not fetch data for {symbol}")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    print(f"Data fetched: {len(df)} bars for {symbol}")
    
    # Initialize Brain (using base symbol name for model filename consistency)
    brain = get_brain("UKOIL")
    
    # Trigger training
    success = brain.train_offline(df, epochs=150)
    
    if success:
        print(f"UKOIL Brain trained and saved to {brain.model_path}")
    else:
        print("Training failed. Check logs.")

    mt5.shutdown()

if __name__ == "__main__":
    train_ukoil()
