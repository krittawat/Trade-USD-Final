
import asyncio
import sys
import os
from datetime import datetime, timezone
import random

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.core.config import get_settings
from app.db.questdb import QuestDBClient
from app.core.logging import get_logger

logger = get_logger(__name__)

async def test_questdb_ticks():
    settings = get_settings()
    qdb = QuestDBClient(settings)
    
    print(f"Connecting to QuestDB at {settings.questdb_host}:{settings.questdb_http_port}...")
    await qdb.connect()
    
    if not qdb._connected:
        print("❌ Failed to connect to QuestDB")
        return

    print("✅ Connected to QuestDB")
    
    # 1. Create Table
    print("Creating 'ticks' table...")
    ok = await qdb.create_ticks_table()
    if ok:
        print("✅ Table 'ticks' verified/created.")
    else:
        print("❌ Failed to create table.")
        
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
        
    print(f"Ingesting {len(mock_ticks)} mock ticks for {symbol}...")
    await qdb.ingest_ticks(symbol, mock_ticks)
    print("✅ Ingest request sent (ILP is fire-and-forget, check QuestDB logs/console).")
    
    # 3. Query Verification (Wait a bit for consistency if immediate query is needed, but QuestDB is fast)
    await asyncio.sleep(1)
    
    print("Querying latest ticks...")
    # Note: timestamp in QuestDB is microsecond by default for query result usually
    rows = await qdb.query(f"SELECT * FROM ticks WHERE symbol = '{symbol}' ORDER BY ts DESC LIMIT 5")
    
    if rows:
        print(f"✅ Found {len(rows)} rows:")
        for r in rows:
            print(r)
    else:
        print("⚠️ No rows found immediately. Check QuestDB console later.")
        
    await qdb.disconnect()

if __name__ == "__main__":
    asyncio.run(test_questdb_ticks())
