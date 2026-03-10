import sqlite3
import os

db_path = r"d:\VibeCode\Trade\backend\data\sqlite\trading.db"
if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
else:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT symbol, COUNT(*) FROM candles GROUP BY symbol")
        rows = cursor.fetchall()
        for row in rows:
            print(f"{row[0]}: {row[1]}")
    except Exception as e:
        print(f"Error: {e}")
    conn.close()
