"""Quick script to insert btc_momentum strategy into strategy_registry DB."""
import sqlite3
import json
from datetime import datetime, timezone

db_path = "backend/data/sqlite/trading.db"
conn = sqlite3.connect(db_path)
now = datetime.now(timezone.utc).isoformat()

try:
    conn.execute("""
        INSERT OR REPLACE INTO strategy_registry
        (strategy_name, class_name, module_path, timeframe, asset_class, suitable_regimes, priority, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "btc_momentum", "BtcMomentum", "app.strategy.templates.btc_momentum",
        "M5", "crypto",
        json.dumps(["trending_up", "trending_down", "high_volatility", "breakout", "unknown"]),
        95, 1, now, now
    ))
    conn.commit()

    # Verify
    cur = conn.execute("SELECT strategy_name, asset_class, priority FROM strategy_registry WHERE strategy_name = ?", ("btc_momentum",))
    row = cur.fetchone()
    print(f"OK: {row}")

    # Count total
    cur2 = conn.execute("SELECT COUNT(*) from strategy_registry")
    print(f"Total strategies: {cur2.fetchone()[0]}")
except Exception as e:
    print(f"ERROR: {e}")
finally:
    conn.close()
