import sqlite3
import os

db_path = r'd:\VibeCode\Trade\backend\data\sqlite\trading.db'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = [row[0] for row in cursor.fetchall()]
print(f"Tables in trading.db: {tables}")

for table in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    print(f"Table {table}: {count} rows")
    if count > 0:
        cursor.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 1")
        print(f"Latest row in {table}: {dict(cursor.fetchone())}")

conn.close()
