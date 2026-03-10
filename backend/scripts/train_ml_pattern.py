"""
ML Pattern Training Pipeline — Train .joblib per-symbol models. 

Usage:
    python scripts/train_ml_pattern.py --symbols XAUUSDc,XAGUSDc
"""

import sys
import os
import time
import asyncio
import logging
import argparse
from pathlib import Path
import pandas as pd

# Adjust path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.config import get_settings
from app.db.sqlite import SQLiteStore
from app.brain.memory_store import MemoryStore
from app.brain.ml_pattern_learner import MLPatternLearner
import MetaTrader5 as mt5_lib

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger("TrainMLPattern")

async def run_training(symbols: list[str]):
    try:
        settings = get_settings()
        
        # 1. Connect DBs
        print("Connecting to databases...")
        db = SQLiteStore(settings)
        db.connect()
        memory = MemoryStore()
        memory.connect()
        memory.set_trading_db(db)

        # 2. Init DuckDB for fast Parquet training
        print("Initializing DuckDB Analytics Engine...")
        from app.db.duckdb import DuckDBStore
        duckdb_store = DuckDBStore(settings)
        # We manually connect to an in-memory db to avoid locking the main analytics DB
        import duckdb
        duckdb_store._conn = duckdb.connect(':memory:')

        # 3. Init Learner
        print("Initializing MLPatternLearner...")
        learner = MLPatternLearner(memory_store=memory, settings=settings)
        learner._enabled = True

        for symbol in symbols:
            print(f"\\n{'='*60}")
            print(f"  Training {symbol}...")
            print(f"{'='*60}")

            print(f"Fetching candles from DuckDB for {symbol}...")
            # We use M5 as base for ML Pattern Training
            df = duckdb_store.get_candles(symbol, 'M5')
            candles_dict = {}
            if df is not None and len(df) > 0:
                # Ensure time is datetime for learner logic
                df['time'] = pd.to_datetime(df['time'], utc=True)
                candles_dict[symbol] = df
                print(f"Fetched {len(df):,} candles from DuckDB.")
            else:
                print(f"[WARN] No candles fetched for {symbol} in DuckDB Parquet exports.")

            # Run training
            t0 = time.time()
            result = await learner.train(candles_by_symbol=candles_dict, symbol=symbol)
            elapsed = time.time() - t0

            print(f"\\nResult for {symbol}:")
            print(f"  Status:   {result.get('status', 'unknown')}")
            print(f"  Samples:  {result.get('samples', 0)}")
            print(f"  Accuracy: {result.get('accuracy', 0.0):.2%} ({result.get('accuracy', 0.0)})")
            print(f"  Time:     {elapsed:.1f}s")
            
        print(f"\\n{'='*60}")
        print("  Process Finished")
        print(f"{'='*60}\\n")

    finally:
        if 'duckdb_store' in locals():
            duckdb_store.disconnect()
        if 'memory' in locals():
            memory.disconnect()
        if 'db' in locals():
            db.disconnect()

def main():
    parser = argparse.ArgumentParser(description="ML Pattern Per-Symbol Training Pipeline")
    parser.add_argument("--symbols", type=str, default="XAUUSDc,XAGUSDc",
                        help="Comma-separated symbols")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]

    print("=" * 60)
    print("  ML Pattern Learner (.joblib) Training")
    print("=" * 60)
    print(f"  Symbols: {', '.join(symbols)}")
    print("=" * 60)

    asyncio.run(run_training(symbols))

if __name__ == "__main__":
    main()
