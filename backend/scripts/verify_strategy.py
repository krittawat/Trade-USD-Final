import sqlite3
import json

db_path = r'd:\VibeCode\Trade\backend\data\sqlite\trading.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("--- Active Strategies (priority >= 50) ---")
cursor.execute("SELECT strategy_name, priority, is_active FROM strategy_registry WHERE is_active = 1 ORDER BY priority DESC")
for row in cursor.fetchall():
    print(row)

print("\n--- Strategy Params for XAUUSDc ---")
cursor.execute("SELECT strategy_name, params_json FROM strategy_params WHERE symbol = 'XAUUSDc' AND is_active = 1")
for row in cursor.fetchall():
    print(f"{row[0]}: {row[1]}")

conn.close()
