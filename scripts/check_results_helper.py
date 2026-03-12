import sqlite3
import json
from pathlib import Path

def check_results():
    db_path = Path("backend/data/sqlite/trading.db")
    if not db_path.exists():
        print(f"DB not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    cur.execute("SELECT strategy_name, created_at, win_rate, profit_factor FROM tournament_results WHERE symbol='XAUUSDm' ORDER BY created_at DESC LIMIT 5")
    rows = cur.fetchall()
    
    print("Latest 5 Results for XAUUSDm:")
    for row in rows:
        print(f"Strategy: {row[0]} | Created: {row[1]} | WR: {row[2]}% | PF: {row[3]}")
        
    cur.execute("SELECT COUNT(*) FROM tournament_results")
    total = cur.fetchone()[0]
    print(f"\nTotal records in tournament_results: {total}")
    
    conn.close()

if __name__ == "__main__":
    check_results()
