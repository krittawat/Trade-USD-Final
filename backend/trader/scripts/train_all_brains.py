import sys
from pathlib import Path
import logging
from datetime import datetime

# Set up paths
ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.trader.brain.deep_brain import DeepBrainPredictor
from backend.trader.data.fetcher import fetcher
from backend.trader.main import CONFIG
import MetaTrader5 as mt5

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TRAIN_ALL")

def train_all():
    """
    Antigravity AI - Mass Brain Training Cycle
    Fetches M15 data for all symbols in main.py CONFIG and initializes/updates models.
    """
    logger.info(f"🚀 Starting Mass Training Cycle at {datetime.now()}")
    
    if not fetcher.connect():
        logger.error("❌ MT5 Connection Failed")
        return

    # Symbols we want to ensure have models
    all_symbols = list(set(CONFIG["symbols"] + CONFIG.get("shadow_symbols", [])))
    
    results = {}

    for symbol in all_symbols:
        logger.info(f"\n--- Processing {symbol} ---")
        
        # Try to find existing model first
        brain = DeepBrainPredictor(symbol)
        
        # Fetch data for training (M15 is robust for pattern recognition)
        # 5000 bars cover ~52 days on M15
        bars_to_fetch = 5000
        logger.info(f"  📥 Fetching {bars_to_fetch} M15 bars...")
        df = fetcher.get_rates(symbol, mt5.TIMEFRAME_M15, bars_to_fetch)
        
        if df is not None and len(df) >= 1000:
            logger.info(f"  🧠 Training Deep Brain for {symbol} (Samples: {len(df)})")
            success = brain.train_offline(df, epochs=50) # Use 50 for quick but high-quality init
            
            if success:
                # Get a quick accuracy check on latest data
                logger.info(f"  ✅ {symbol} Brain Ready.")
                results[symbol] = "SUCCESS"
            else:
                logger.error(f"  ❌ {symbol} Training Failed.")
                results[symbol] = "FAILED"
        else:
            logger.warning(f"  ⚠️ Insufficient data for {symbol} (Got {len(df) if df is not None else 0})")
            results[symbol] = "INSUFFICIENT_DATA"

    fetcher.disconnect()
    
    # Summary
    logger.info("\n" + "="*40)
    logger.info("  MASS TRAINING SUMMARY")
    logger.info("="*40)
    for sym, res in results.items():
        logger.info(f"  {sym:<15}: {res}")
    logger.info("="*40)

if __name__ == "__main__":
    train_all()
