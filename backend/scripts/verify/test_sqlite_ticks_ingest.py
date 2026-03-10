import asyncio
import sys
import os
from datetime import datetime, timezone
import random

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.core.config import get_settings
from app.db.sqlite import SQLiteStore
from app.core.logging import get_logger

logger = get_logger(__name__)

async def test_sqlite_ticks():
    settings = get_settings()
    db = SQLiteStore(settings)
    
    print(f"Connecting to SQLite at {settings.sqlite_db_path}...")
    db.connect()
    
    if not db.health_check():
        print("❌ Failed to connect to SQLite")
        return

    print("✅ Connected to SQLite")
    
    # 2. Ingest Mock Ticks
    symbol = "TEST_USD"
    now_ts = datetime.now(timezone.utc).timestamp()
    
    mock_ticks = []
    for i in range(10):
        mock_ticks.append({
            "time": now_ts + i,
            "bid": 1000.0 + random.random(),
            "ask": 1000.5 + random.random(),
            "last": 1000.2 + random.random(),
            "volume": int(100 + random.random() * 50),
            "flags": 0
        })
        
    print(f"Ingesting {len(mock_ticks)} mock ticks for {symbol} to SQLite...")
    
    # Run in thread since ingest_ticks is synchronous
    await asyncio.to_thread(db.ingest_ticks, symbol, mock_ticks)
    
    print("✅ Ingest request completed.")
    
    # 3. Query Verification
    print("Querying latest ticks...")
    rows = await asyncio.to_thread(
        lambda: db._conn.execute(
            f"SELECT * FROM ticks WHERE symbol = '{symbol}' ORDER BY ts DESC LIMIT 5"
        ).fetchall()
    )
    
    if rows:
        print(f"✅ Found {len(rows)} rows:")
        for r in rows:
            print(dict(r))
    else:
        print("⚠️ No rows found. Ingestion might have failed.")
        
    db.disconnect()

if __name__ == "__main__":
    asyncio.run(test_sqlite_ticks())
