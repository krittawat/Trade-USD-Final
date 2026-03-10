import sqlite3
import os

db_path = os.path.join("data", "sqlite", "trading.db")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM strategy_registry WHERE module_path LIKE '%ema210_mtf_bounce%' OR strategy_name LIKE '%ema210_mtf_bounce%'")
    conn.commit()
    print(f"Deleted {cur.rowcount} rows related to ema210_mtf_bounce")
    conn.close()
else:
    print("DB not found at", db_path)
