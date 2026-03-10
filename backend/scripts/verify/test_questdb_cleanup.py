
import asyncio
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from app.core.config import get_settings
from app.db.questdb import QuestDBClient

async def test_cleanup():
    settings = get_settings()
    qdb = QuestDBClient(settings)
    
    print(f"Connecting to QuestDB...")
    await qdb.connect()
    
    print("Running cleanup (simulate dropping old partitions)...")
    # Using 30 days safe default for test, or 0 to skip actual drop if we just want to test SQL syntax validity
    # But QuestDB might return error if no partitions match? 
    # Let's try 365 days to be safe (unlikely to have data older than year)
    await qdb.cleanup_old_data(365)
    
    await qdb.disconnect()
    print("Cleanup command executed.")

if __name__ == "__main__":
    asyncio.run(test_cleanup())
