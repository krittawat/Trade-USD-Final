import sqlite3
from datetime import datetime, timezone

db_path = r'd:\VibeCode\Trade\backend\data\sqlite\trading.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    now = datetime.now(timezone.utc).isoformat()
    cursor.execute("""
        INSERT INTO strategy_registry 
        (strategy_name, class_name, module_path, timeframe, asset_class, suitable_regimes, priority, is_active, created_at, updated_at)
        VALUES 
        ('pullback_v2', 'PullbackV2Strategy', 'app.strategy.templates.pullback_v2', 'M5', '*', '["TRENDING_UP", "TRENDING_DOWN", "STRONG_TREND", "WEAK_TREND"]', 100, 1, ?, ?)
        ON CONFLICT(strategy_name) 
        DO UPDATE SET priority = 100, is_active = 1, updated_at = excluded.updated_at
    """, (now, now))
    print(f"Registered/Prioritized pullback_v2: {cursor.rowcount} rows affected")
    conn.commit()
except Exception as e:
    print(f"Error: {e}")
    conn.rollback()
finally:
    conn.close()
