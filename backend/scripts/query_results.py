import sqlite3
import pandas as pd
import os

db_path = r"c:\VibeCode\Trade\backend\data\sqlite\trading.db"

if not os.path.exists(db_path):
    print(f"Error: Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
query = """
SELECT symbol, strategy_name, total_trades, win_rate, profit_factor, total_profit_usd, composite_score 
FROM tournament_results 
ORDER BY symbol, composite_score DESC;
"""
df = pd.read_sql_query(query, conn)
if df.empty:
    print("No results found in tournament_results table.")
else:
    print(df.to_string(index=False))

print("\n--- Statistics ---")
count_df = pd.read_sql_query("SELECT COUNT(*) as total_results FROM tournament_results", conn)
print(count_df.to_string(index=False))

print("\n--- Symbols with Results ---")
sym_count_df = pd.read_sql_query("SELECT symbol, COUNT(*) as result_count FROM tournament_results GROUP BY symbol", conn)
print(sym_count_df.to_string(index=False))

print("\n--- Recent Logs (XAUUSDm check) ---")
# Just to check if anything is being logged
query_logs = "SELECT * FROM tournament_results WHERE symbol LIKE 'XAU%' LIMIT 5;"
logs_df = pd.read_sql_query(query_logs, conn)
print(logs_df.to_string(index=False))

conn.close()
