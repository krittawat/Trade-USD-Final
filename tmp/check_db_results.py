import sqlite3
import pandas as pd

db_path = "d:/VibeCode/Trade/trader/data/opus.db"
conn = sqlite3.connect(db_path)

try:
    df = pd.read_sql_query("SELECT * FROM tournament_results", conn)
    if df.empty:
        print("Table 'tournament_results' is empty.")
    else:
        print("--- Tournament Results from DB ---")
        print(df.to_string())
except Exception as e:
    print(f"Error reading DB: {e}")
finally:
    conn.close()
