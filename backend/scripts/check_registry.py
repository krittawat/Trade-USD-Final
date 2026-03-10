import sqlite3
import pandas as pd

try:
    with sqlite3.connect(r'd:\VibeCode\Trade\backend\data\sqlite\trading.db') as conn:
        df = pd.read_sql_query("SELECT symbol, strategy_name, is_active, priority FROM strategy_registry WHERE symbol='XAUUSDc' AND is_active=1", conn)
        print(df)
except Exception as e:
    print(e)
