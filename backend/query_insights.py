import sqlite3
import os

db_path = r'd:\VibeCode\Trade\backend\data\sqlite\trading.db'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("\n--- trade_insights (All 12 rows) ---")
cursor.execute("SELECT * FROM trade_insights")
for row in cursor.fetchall():
    print(dict(row))

print("\n--- trade_journal (Last 10 rows) ---")
cursor.execute("SELECT * FROM trade_journal ORDER BY rowid DESC LIMIT 10")
for row in cursor.fetchall():
    print(dict(row))

conn.close()
