
import sys
import os
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import logging

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.brain.deep_learner import DeepLearner
from app.brain.outcome_analyzer import OutcomeAnalyzer, TradeContext
from app.db.sqlite import SQLiteStore
from app.core.config import Settings

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_dl_integration():
    print("=== Testing Deep Learning Integration ===")
    
    # 1. Initialize Deep Learner
    settings = Settings()
    dl = DeepLearner(settings)
    print(f"[OK] DeepLearner initialized. Enabled: {dl._enabled}")
    
    # 2. Mock Candles
    print("Generating mock candles...")
    dates = pd.date_range(end=datetime.now(), periods=200, freq="5min")
    data = {
        "time": dates,
        "open": np.random.rand(200) * 100 + 1900,
        "high": np.random.rand(200) * 100 + 1910,
        "low": np.random.rand(200) * 100 + 1890,
        "close": np.random.rand(200) * 100 + 1900,
        "tick_volume": np.random.randint(100, 1000, 200),
        "spread": np.random.randint(1, 10, 200),
    }
    df = pd.DataFrame(data)
    
    # 3. Predict from Candles
    print("Testing predict_from_candles...")
    prob = dl.predict_from_candles(df)
    print(f"Prediction result: {prob}")
    assert isinstance(prob, float)
    assert 0.0 <= prob <= 1.0
    print("[OK] Prediction successful")
    
    # 4. Outcome Analyzer Integration
    print("Testing OutcomeAnalyzer schema and storage...")
    db_path = "test_dl_outcome.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        
    store = SQLiteStore(db_path=db_path)
    analyzer = OutcomeAnalyzer(db=store)
    
    # 5. Record Trade with DL Prob
    ctx = TradeContext(
        ticket=12345,
        symbol="XAUUSDc",
        strategy_name="TEST_STRAT",
        regime="TRENDING",
        session="NY",
        confidence=0.85,
        ml_win_prob=0.6,
        dl_win_prob=0.75, # <--- Testing this field
        entry_price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        opened_at=datetime.now(timezone.utc).isoformat()
    )
    
    print("Recording trade context...")
    analyzer.record_trade_open(ctx)
    
    # 6. Verify DB
    conn = store._conn
    row = conn.execute("SELECT dl_win_prob FROM trade_context WHERE ticket=12345").fetchone()
    print(f"Retrieved dl_win_prob: {row[0]}")
    assert row[0] == 0.75, f"Expected 0.75, got {row[0]}"
    print("[OK] Storage verified")
    
    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)
        
    print("=== Deep Learning Integration Test PASSED ===")

if __name__ == "__main__":
    asyncio.run(test_dl_integration())
