import sqlite3
import json
import pandas as pd
from pathlib import Path
from datetime import datetime

# Path to the database
DB_PATH = Path("d:/VibeCode/Trade/trader/data/opus.db")

def extract_winners():
    """Extract and analyze winning shadow trades to find success patterns."""
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    
    # Query for winning shadow trades
    query = """
    SELECT * FROM shadow_experience 
    WHERE outcome = 1 
    ORDER BY timestamp DESC
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        print("No winning trades found in shadow_experience yet. Keep the dry run running!")
        return

    print(f"📊 Found {len(df)} Winning 'Success Patterns'")
    print("-" * 50)

    for idx, row in df.iterrows():
        try:
            feats = json.loads(row['features'])
            print(f"ID: {row['id']} | {row['symbol']} {row['side']} | Model: {row['model']} | R: {row['pnl_r']:.2f}")
            print(f"  Timestamp: {row['timestamp']}")
            print(f"  Key Features: RSI={feats.get('rsi', 0):.1f}, EMA_Gap={feats.get('ema_gap', 0):.4f}, NATR={feats.get('natr', 0):.4f}")
            print("-" * 30)
        except Exception as e:
            print(f"Error parsing features for ID {row['id']}: {e}")

if __name__ == "__main__":
    extract_winners()
