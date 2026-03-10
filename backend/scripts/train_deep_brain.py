import sys
import os
from pathlib import Path
import logging

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.trader.brain.deep_brain import DeepBrainPredictor
from backend.trader.data.fetcher import fetcher
import MetaTrader5 as mt5

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DeepBrainInit")

def main():
    # symbols from main.py CONFIG (hardcoded here for simplicity or derived if possible)
    symbols = ["BTCUSD", "USOILm", "US30m", "USTECm", "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY"]
    
    if not fetcher.connect():
        logger.error("Could not connect to MT5")
        return

    model_dir = Path("d:/VibeCode/Trade/backend/trader/brain/models")
    model_dir.mkdir(parents=True, exist_ok=True)

    for symbol in symbols:
        # Check if model already exists (any variation)
        variations = [symbol, f"{symbol}m", f"{symbol}c"]
        found = False
        for var in variations:
            if (model_dir / f"{var}_deep_brain.joblib").exists():
                found = True
                break
        
        if found:
            logger.info(f"Model for {symbol} already exists. Skipping.")
            continue

        logger.info(f"Training initial Brain for {symbol}...")
        
        # Fetch data (M15 is good for initial brain)
        df = fetcher.get_rates(symbol, mt5.TIMEFRAME_M15, 5000)
        
        if df is not None and len(df) >= 500:
            brain = DeepBrainPredictor(symbol)
            success = brain.train_offline(df, epochs=50) # Use 50 epochs for quick init
            if success:
                logger.info(f"Successfully initialized Brain for {symbol}")
            else:
                logger.error(f"Failed to initialize Brain for {symbol}")
        else:
            logger.warning(f"Insufficient data for {symbol} (need 500, got {len(df) if df is not None else 0})")

    fetcher.disconnect()

if __name__ == "__main__":
    main()
