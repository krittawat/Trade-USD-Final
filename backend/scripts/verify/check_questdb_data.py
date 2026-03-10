
import asyncio
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from app.core.config import get_settings
from app.db.questdb import QuestDBClient

async def check_data():
    settings = get_settings()
    qdb = QuestDBClient(settings)
    await qdb.connect()
    
    symbol = "TEST_USD"
    print(f"Querying ticks for {symbol}...")
    
    rows = await qdb.query(f"SELECT * FROM ticks WHERE symbol = '{symbol}' LIMIT 10")
    print(f"Found {len(rows)} rows.")
    for r in rows:
        print(r)
        
    await qdb.disconnect()

if __name__ == "__main__":
    asyncio.run(check_data())
