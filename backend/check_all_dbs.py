import sqlite3
import os

def check_db(db_path):
    if not os.path.exists(db_path):
        print(f"DB not found: {db_path}")
        return
    
    print(f"\nChecking DB: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"Tables: {tables}")
        
        for table in tables:
            if 'account' in table.lower() or 'state' in table.lower() or 'journal' in table.lower():
                print(f"\n--- Table: {table} ---")
                cursor.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 1;")
                row = cursor.fetchone()
                if row:
                    print(dict(row))
                else:
                    print("Empty table")
                    
    except Exception as e:
        print(f"Error checking {db_path}: {e}")
    finally:
        conn.close()

paths = [
    r'd:\VibeCode\Trade\backend\data\sqlite\trading.db',
    r'd:\VibeCode\Trade\backend\data\sqlite\trade.db',
    r'd:\VibeCode\Trade\backend\data\sqlite\trade_journal.db'
]

for p in paths:
    check_db(p)
