import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.brain.memory_store import MemoryStore

berserker_params = {
    "MA_PERIOD": 8,
    "SAR_AF": 0.03,
    "SAR_MAX": 0.2,
    "RSI_PERIOD": 9,
    "SL_ATR_MULT": 1.0,
    "TP_ATR_MULT": 4.0,
    "MIN_SL_DISTANCE": 1.5,
    "MACD_FAST": 8,
    "MACD_SLOW": 21,
    "MACD_SIGNAL": 5,
    "RISK_RAPID": 5.0, # Push risk to the absolute cap
}

def main():
    print("🚀 Deploying RAPID_BERSERKER parameters to AI Brain...")
    store = MemoryStore()
    store.connect()

    target_symbols = ["XAUUSDc", "XAGUSDc"]
    
    for symbol in target_symbols:
        try:
            store.save_evolved_params(
                strategy_name="M5_RAPID_SCALPER",
                symbol=symbol,
                regime="ALL",
                params=berserker_params,
                score=9999.0  # Force it to the top of the leaderboard
            )
            print(f"✅ Successfully deployed aggressive parameters for {symbol}")
        except Exception as e:
            print(f"❌ Failed to deploy for {symbol}: {e}")

if __name__ == "__main__":
    main()
