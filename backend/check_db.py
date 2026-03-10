import sqlite3
import os

db_path = r'd:\VibeCode\Trade\backend\data\sqlite\trading.db'
if not os.path.exists(db_path):
    print(f"DB not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

try:
    # Check tables first
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"Tables: {tables}")

    if 'account_state' in tables:
        cursor.execute("SELECT * FROM account_state ORDER BY timestamp DESC LIMIT 1;")
        row = cursor.fetchone()
        if row:
            print("Account State:")
            print(dict(row))
        else:
            print("No data in account_state")
    
    if 'journal' in tables:
        cursor.execute("SELECT * FROM journal ORDER BY timestamp DESC LIMIT 5;")
        rows = cursor.fetchall()
        print("\nRecent Journal entries:")
        for r in rows:
            print(dict(r))

except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
