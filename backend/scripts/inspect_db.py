
import sqlite3
import pandas as pd
import glob
import os

import sqlite3
import pandas as pd
import glob
import os

# Check the nested directory
DB_DIR = r"d:\VibeCode\Trade\backend\backend\data\sqlite"
db_files = glob.glob(os.path.join(DB_DIR, "*.db"))

print(f"Found DB files: {db_files}")

for db_path in db_files:
    print(f"\n{'='*30}")
    print(f"INSPECTING: {db_path}")
    print(f"{'='*30}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # List tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        table_names = [t[0] for t in tables]
        print("Tables:", table_names)
        
        target_tables = ['trade_journal', 'decision_traces', 'backtest_results']
        for table in target_tables:
            if table in table_names:
                print(f"\n--- Top 5 rows from {table} ---")
                try:
                    df = pd.read_sql_query(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 5", conn)
                    print(df.to_string())
                except Exception as e:
                    print(f"Error reading {table}: {e}")
        
        conn.close()

    except Exception as e:
        print(f"CRITICAL ERROR in {db_path}: {e}")
