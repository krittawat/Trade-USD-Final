import sqlite3
import json

try:
    conn = sqlite3.connect('backend/data/sqlite/brain.db')
    cursor = conn.cursor()
    # Check column names
    cursor.execute("PRAGMA table_info(evolved_params)")
    cols = [info[1] for info in cursor.fetchall()]
    print(f"Columns: {cols}")
    
    cursor.execute("SELECT params FROM evolved_params WHERE strategy_name='gold_scalp_pro' ORDER BY updated_at DESC LIMIT 1")
    row = cursor.fetchone()
    if row:
        params = row[0]
        print(f"✅ Found Params:")
        print(json.dumps(json.loads(params), indent=2))
    else:
        print("❌ No params found for gold_scalp_pro")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
