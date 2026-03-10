import sqlite3
import pandas as pd

print("=== Strategy Performance (brain.db) ===")
try:
    with sqlite3.connect(r'd:\VibeCode\Trade\backend\data\sqlite\brain.db') as conn:
        df = pd.read_sql_query("SELECT strategy_name, symbol, regime, session, win_rate, total_trades, profit_factor FROM strategy_performance WHERE symbol='XAUUSDc' ORDER BY win_rate DESC", conn)
        print(df)
except Exception as e:
    print(e)
    
print("\n=== Shadow Scoreboard (trading.db) ===")
try:    
    with sqlite3.connect(r'd:\VibeCode\Trade\backend\data\sqlite\trading.db') as conn:
        df2 = pd.read_sql_query("SELECT strategy_name, symbol, regime, win_rate, total_signals FROM shadow_scoreboard WHERE symbol='XAUUSDc' ORDER BY win_rate DESC", conn)
        print(df2)
except Exception as e:
    print(e)
