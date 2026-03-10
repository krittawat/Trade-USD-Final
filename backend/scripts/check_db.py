import sqlite3
import pandas as pd

try:
    conn = sqlite3.connect('d:/VibeCode/Trade/backend/data/sqlite/trading.db')
    
    try:
        print("--- backtest_routing for XAUUSD(c) ---")
        df = pd.read_sql_query("SELECT * FROM backtest_routing WHERE symbol LIKE 'XAU%'", conn)
        print(df.to_string())
    except Exception as e:
        print(f"Error: {e}")
        
    try:
        print("\n--- shadow_scoreboard for XAUUSD(c) ---")
        df = pd.read_sql_query("SELECT * FROM shadow_scoreboard WHERE symbol LIKE 'XAU%'", conn)
        print(df.to_string())
    except Exception as e:
        print(f"Error: {e}")
        
    try:
        print("\n--- strategy_registry (all) ---")
        df = pd.read_sql_query("SELECT strategy_name, priority, asset_class, is_active FROM strategy_registry", conn)
        print(df.to_string())
    except Exception as e:
        print(f"Error: {e}")
        
except Exception as e:
    print(e)
