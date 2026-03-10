import sqlite3
import pandas as pd
from datetime import datetime

# Connect to DB
try:
    conn = sqlite3.connect(r'd:\VibeCode\Trade\backend\data\sqlite\trading.db')
    
    print("=== TRADE JOURNAL (Last 20) ===")
    df_trades = pd.read_sql_query("SELECT symbol, action, entry_price, profit_usd, strategy_name, entry_time FROM trade_journal ORDER BY entry_time DESC LIMIT 20", conn)
    if not df_trades.empty:
        print(df_trades.to_string())
    else:
        print("No trades found.")

    print("\n=== BLOCKED DECISIONS (Last 20) ===")
    df_blocks = pd.read_sql_query("SELECT symbol, strategy_name, reason, details, timestamp FROM decision_traces WHERE result='blocked' ORDER BY timestamp DESC LIMIT 20", conn)
    if not df_blocks.empty:
        print(df_blocks.to_string())
    else:
        print("No blocked decisions found.")

    print("\n=== REGIME STATS ===")
    # Count regime types in recent traces
    df_regime = pd.read_sql_query("SELECT regime, COUNT(*) as count FROM decision_traces GROUP BY regime", conn)
    print(df_regime.to_string())
    
    conn.close()

except Exception as e:
    print(f"Error: {e}")
