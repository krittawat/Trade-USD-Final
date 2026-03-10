
import sqlite3
import os

dbs = [
    r"d:\VibeCode\Trade\backend\data\sqlite\brain.db",
    r"d:\VibeCode\Trade\backend\data\sqlite\trading.db"
]

for db_path in dbs:
    if not os.path.exists(db_path):
        print(f"\n[!] Skip: {db_path} (not found)")
        continue
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"\n--- Auditing DB: {os.path.basename(db_path)} ---")
        
        # Look for outcomes or results tables
        found = False
        for table in ['outcomes', 'backtest_results', 'practice_results', 'trades']:
            if table in tables:
                found = True
                print(f"[{table}] (Latest 5):")
                cursor.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 5")
                rows = cursor.fetchall()
                for row in rows:
                    d = dict(row)
                    # Try to extract common fields
                    symbol = d.get('symbol', d.get('symbol_name', 'N/A'))
                    strategy = d.get('strategy_name', d.get('strategy', 'N/A'))
                    pf = d.get('pf', d.get('profit_factor', 0))
                    dd = d.get('dd', d.get('max_drawdown', 0))
                    trades = d.get('trades', d.get('total_trades', 0))
                    pl = d.get('pl', d.get('profit', 0))
                    created = d.get('created_at', d.get('timestamp', 'N/A'))
                    print(f"  {symbol:10} | {strategy:20} | PF: {pf:4.2f} | DD: {dd:4.2f}% | P/L: {pl:7.2f} | {created}")
        
        if not found:
             print(f"No performance tables found. Available: {tables}")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()
