import sqlite3

# 1. Clear brain_memory (strategy_performance)
brain_db_path = r'd:\VibeCode\Trade\backend\data\sqlite\brain.db'
try:
    with sqlite3.connect(brain_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM strategy_performance WHERE strategy_name = 'ai_mtf_v3'")
        print(f"Cleared {cursor.rowcount} rows for ai_mtf_v3 from brain.db (strategy_performance)")
        conn.commit()
except sqlite3.OperationalError as e:
    print(f"brain.db Error: {e}")

# 2. Clear shadow_scoreboard from trading.db
trading_db_path = r'd:\VibeCode\Trade\backend\data\sqlite\trading.db'
try:
    with sqlite3.connect(trading_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM shadow_scoreboard WHERE strategy_name = 'ai_mtf_v3'")
        print(f"Cleared {cursor.rowcount} rows for ai_mtf_v3 from trading.db (shadow_scoreboard)")
        conn.commit()
except sqlite3.OperationalError as e:
    print(f"trading.db Error: {e}")

print("AI Brain override for 'ai_mtf_v3' cleared.")
