import sqlite3
import os

db_path = r"d:\VibeCode\Trade\backend\data\sqlite\trading.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
try:
    cursor.execute("PRAGMA table_info(ticks)")
    cols = cursor.fetchall()
    for col in cols:
        print(col)
    
    cursor.execute("SELECT symbol, COUNT(*) FROM ticks GROUP BY symbol")
    rows = cursor.fetchall()
    for row in rows:
        print(f"{row[0]}: {row[1]}")
except Exception as e:
    print(f"Error: {e}")
conn.close()
