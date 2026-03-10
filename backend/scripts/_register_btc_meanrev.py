#!/usr/bin/env python3
"""
Register btc_mean_reversion + Run training on BTCUSDc.
Uses the training script's DB connection (not blocked by bot).
"""
import sys, os, asyncio, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5
from app.core.config import get_settings
from app.db.sqlite import SQLiteStore

async def main():
    settings = get_settings()
    db = SQLiteStore(settings)
    db.connect()
    
    # Register btc_mean_reversion if not exists
    now = datetime.datetime.utcnow().isoformat()
    try:
        db._conn.execute(
            """INSERT OR IGNORE INTO strategy_registry 
            (strategy_name, class_name, module_path, timeframe, 
             asset_class, suitable_regimes, priority, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ('btc_mean_reversion', 'BtcMeanReversion',
             'app.strategy.templates.btc_mean_reversion', 'M15',
             'crypto', 'ranging,low_volatility,unknown', 92, 1, now, now)
        )
        db._conn.commit()
        print("✅ btc_mean_reversion registered in DB")
    except Exception as e:
        print(f"⚠️  Registration: {e}")
    
    # Verify
    rows = db.get_strategy_registry(active_only=True)
    names = [r.get("strategy_name", "") for r in rows]
    print(f"📋 Registry: {len(rows)} strategies")
    if "btc_mean_reversion" in names:
        print("✅ btc_mean_reversion is in registry")
    else:
        print("❌ btc_mean_reversion NOT found")
    
    db.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
