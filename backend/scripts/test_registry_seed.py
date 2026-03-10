"""Quick test: verify factory auto-seeds strategy_registry in DB."""
from app.core.config import Settings
from app.db.sqlite import SQLiteStore
from app.strategy.factory import StrategyFactory

s = Settings()
db = SQLiteStore(s)
db.connect()

# Clear registry to simulate first startup
db._conn.execute("DELETE FROM strategy_registry")
db._conn.commit()
print("Cleared strategy_registry table")

# Run auto_register — should scan modules then auto-seed
f = StrategyFactory()
count = f.auto_register(db=db)
print(f"\nRegistered strategies: {count}")

# Verify registry was populated
rows = db.get_strategy_registry(active_only=False)
print(f"Registry rows in DB: {len(rows)}")
for r in rows:
    print(f"  {r['strategy_name']:25s} | {r.get('asset_class', '?'):6s} | prio={r.get('priority', 0):3d}")

# Second run — should load from DB (no module scan needed)
print("\n--- Second startup (should load from DB) ---")
f2 = StrategyFactory()
count2 = f2.auto_register(db=db)
print(f"Registered strategies: {count2}")
print(f"Registry cache size: {len(f2._registry_cache)}")
