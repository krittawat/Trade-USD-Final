import sqlite3
import json

db_path = r"d:\VibeCode\Trade\backend\data\sqlite\brain.db"
out_path = r"d:\VibeCode\Trade\backend\data\sqlite\schema_info.json"
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    
    info = {"tables": []}
    for table in tables:
        t_name = table[0]
        cursor.execute(f"PRAGMA table_info({t_name});")
        info["tables"].append({"name": t_name, "columns": cursor.fetchall()})
        
    cursor.execute("SELECT * FROM evolved_params LIMIT 1;")
    info["sample_data"] = cursor.fetchall()
    
    with open(out_path, 'w') as f:
        json.dump(info, f, default=str)
    print(f"Schema info written to {out_path}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
