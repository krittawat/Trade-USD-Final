import sqlite3
import pandas as pd

print("=== Strategy Performance (brain.db nested) ===")
try:
    with sqlite3.connect(r'd:\VibeCode\Trade\backend\backend\data\sqlite\brain.db') as conn:
        df = pd.read_sql_query("SELECT strategy_name, symbol, regime, session, win_rate, total_trades, profit_factor FROM strategy_performance WHERE symbol='XAUUSDc' ORDER BY win_rate DESC", conn)
        print(df)
        
        cursor = conn.cursor()
        cursor.execute("DELETE FROM strategy_performance WHERE strategy_name = 'ai_mtf_v3'")
        print(f"Cleared {cursor.rowcount} rows for ai_mtf_v3 from nested brain.db (strategy_performance)")
        conn.commit()
except Exception as e:
    print(e)
