
import sqlite3
import pandas as pd

conn = sqlite3.connect("backend/data/sqlite/brain.db")
try:
    df = pd.read_sql("SELECT * FROM strategy_performance", conn)
    print(df.to_string())
except Exception as e:
    print(e)
finally:
    conn.close()
