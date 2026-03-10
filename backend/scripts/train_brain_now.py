
import asyncio
import logging
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(str(Path("d:/VibeCode/Trade/backend")))

from app.core.config import get_settings
from app.db.sqlite import SQLiteStore
from app.brain.memory_store import MemoryStore
from app.brain.trainer import Trainer

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger("trainer_script")
    
    # Init settings and stores
    os.chdir("d:/VibeCode/Trade/backend")
    settings = get_settings()
    
    db = SQLiteStore(settings)
    db.connect()
    
    memory = MemoryStore()
    memory.connect()
    
    trainer = Trainer(memory_store=memory, sqlite_store=db)
    
    logger.info("--- Starting AI Brain Training Cycle ---")
    
    # 1. Train from Real Trade Journal
    real_results = await trainer.run_training_cycle()
    logger.info(f"Real Trade Training Result: {real_results}")
    
    # 2. Train from Shadow Trades
    # Note: run_shadow_training_cycle evaluates PENDING shadows.
    # We reset some 'EXPIRED' to NULL/PENDING in the previous step to re-evaluate them.
    # This requires candles. We will try to fetch some from DB via master loop logic if possible,
    # but shadow_evaluator typically expects them passed in.
    
    # Let's see if we can trigger basic memory update from what's already evaluated too.
    # The current Trainer.run_training_cycle only looks at trade_journal.
    # We might need to manually feed shadow results if we want the brain to learn from them.
    
    logger.info("--- Brain Memory Updated ---")
    db.disconnect()
    memory.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
