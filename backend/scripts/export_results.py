import sqlite3
import pandas as pd
import os

db_path = r"c:\VibeCode\Trade\backend\data\sqlite\trading.db"
export_path = r"c:\VibeCode\Trade\backend\data\exports\tournament_results_full.csv"

if not os.path.exists(db_path):
    print(f"Error: Database not found at {db_path}")
    exit(1)

os.makedirs(os.path.dirname(export_path), exist_ok=True)

conn = sqlite3.connect(db_path)
df = pd.read_sql_query("SELECT * FROM tournament_results ORDER BY symbol, composite_score DESC;", conn)
df.to_csv(export_path, index=False)
conn.close()

print(f"Exported {len(df)} results to {export_path}")
