import sqlite3
import pandas as pd
from pathlib import Path

db_path = Path("d:/VibeCode/Trade/backend/trader/data/replay_180d.db")
if not db_path.exists():
    print(f"DB not found at {db_path}")
else:
    conn = sqlite3.connect(str(db_path))
    try:
        df = pd.read_sql_query("SELECT * FROM replay_summary", conn)
        print("--- REPLAY SUMMARY ---")
        print(df.to_string())
        
        # Also check trades count
        df_trades = pd.read_sql_query("SELECT symbol, timeframe, count(*) as trades FROM replay_trades GROUP BY symbol, timeframe", conn)
        print("\n--- TRADES COUNT ---")
        print(df_trades.to_string())
    except Exception as e:
        print(f"Error reading DB: {e}")
    conn.close()
