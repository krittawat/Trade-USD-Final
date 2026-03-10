
import sqlite3
import os

def check_trades():
    # Hardcoded path based on project structure
    # script is in backend/scripts/verify
    # db is in backend/data/sqlite/trading.db
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    db_path = os.path.join(base_dir, "data", "sqlite", "trading.db")
    
    print(f"Checking DB at: {db_path}")
    
    if not os.path.exists(db_path):
        print("❌ DB file not found!")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Check trade_journal table
    try:
        cursor.execute("SELECT count(*) FROM trade_journal")
        real_count = cursor.fetchone()[0]
        print(f"Real Trades (trade_journal): {real_count}")
        
        if real_count > 0:
            cursor.execute("SELECT * FROM trade_journal ORDER BY entry_time DESC LIMIT 5")
            print("\n--- Latest Real Trades ---")
            for row in cursor.fetchall():
                print(dict(row))
    except Exception as e:
        print(f"Error checking trade_journal: {e}")

    # Check shadow_trades table
    try:
        cursor.execute("SELECT count(*) FROM shadow_trades")
        shadow_count = cursor.fetchone()[0]
        print(f"Shadow Trades: {shadow_count}")
        
        if shadow_count > 0:
            cursor.execute("SELECT * FROM shadow_trades WHERE outcome != 'PENDING' LIMIT 5")
            evaluated = cursor.fetchall()
            print(f"\n--- Evaluated Shadow Trades ({len(evaluated)}) ---")
            for row in evaluated:
                print(dict(row))
                
    except Exception as e:
        print(f"Error checking shadow_trades: {e}")

    conn.close()

if __name__ == "__main__":
    check_trades()
