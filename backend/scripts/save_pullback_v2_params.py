"""
Save the best Pullback V2 parameters to the AI Brain MemoryStore.
"""
import sys
from pathlib import Path
import json

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.memory_store import MemoryStore

best_params = {
    "ema_fast": 21,
    "ema_slow": 50,
    "rr": 1.5,
    "sl_atr": 1.5,
    "pb_pct": 0.35,
    "rsi_buy_max": 50,
    "rsi_sell_min": 50,
    "adx_min": 12,
    "need_2candle": False,
    "need_macd": True,
    "session_filter": False
}

def main():
    try:
        store = MemoryStore()
        store.connect()
        store.save_evolved_params(
            strategy_name="pullback_v2",
            symbol="XAUUSDc",
            regime="ALL",
            params=best_params,
            score=679.485,
        )
        print("Successfully saved V2_RR1.5_LOOSE parameters to MemoryStore.")
    except Exception as e:
        print(f"Failed to save to MemoryStore: {e}")

if __name__ == "__main__":
    main()
