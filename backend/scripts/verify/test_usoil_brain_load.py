
import os
import sys
import joblib
import logging

# --- Add backend to path ---
from pathlib import Path
BACKEND_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND_DIR))

from backend.trader.brain.deep_brain import DeepBrainPredictor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_usoil_brain_load():
    symbol = "USOILm"
    predictor = DeepBrainPredictor(symbol)
    
    # Check if files exist
    model_path = os.path.join("backend/trader/brain/models", f"{symbol}_deep_brain.joblib")
    scaler_path = os.path.join("backend/trader/brain/models", f"{symbol}_scaler.joblib")
    
    if os.path.exists(model_path) and os.path.exists(scaler_path):
        print(f"[OK] Found model files for {symbol}")
        try:
            # Predictor initialization already loads the model
            if predictor.model is not None:
                print(f"[OK] Successfully loaded {symbol} brain model.")
            else:
                print(f"[FAIL] Failed to load {symbol} brain model (model is None).")
        except Exception as e:
            print(f"[FAIL] Error during loading: {e}")
    else:
        print(f"[FAIL] Model files NOT found for {symbol} at {model_path}")

if __name__ == "__main__":
    test_usoil_brain_load()
