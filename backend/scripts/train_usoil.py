import os
import sys
import pandas as pd
import MetaTrader5 as mt5
from pathlib import Path

# Add project root to sys.path
root = Path(__file__).parent.parent.parent
sys.path.append(str(root))

from backend.trader.brain.deep_brain import DeepBrainPredictor
from backend.trader.data.mapper import mapper

def main():
    symbol = "USOILm"
    broker_symbol = mapper.to_broker(symbol)
    
    print(f"--- Retraining Brain for {symbol} ({broker_symbol}) ---")
    
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return
        
    print("MT5 Initialized. Fetching data...")
    # Fetch 5000 M5 candles for robust training
    rates = mt5.copy_rates_from_pos(broker_symbol, mt5.TIMEFRAME_M5, 0, 5000)
    
    if rates is None or len(rates) < 1000:
        print(f"Insufficient data: {len(rates) if rates is not None else 0} bars")
        mt5.shutdown()
        return
        
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    # Rename columns to match engineer_deep_features expectations
    # DeepBrain expects: time, open, high, low, close
    # copy_rates returns: time, open, high, low, close, tick_volume, spread, real_volume
    
    print(f"Data fetched: {len(df)} bars. Training model...")
    
    brain = DeepBrainPredictor(symbol)
    success = brain.train_offline(df, epochs=100)
    
    if success:
        print(f"SUCCESS: Model saved for {symbol}")
    else:
        print(f"FAILED: Training failed for {symbol}")
        
    mt5.shutdown()

if __name__ == "__main__":
    main()
