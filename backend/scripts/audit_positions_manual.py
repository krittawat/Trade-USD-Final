import sqlite3
import os

db_path = 'trader/data/opus.db'
if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
else:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    # Using correct schema names: ticket, symbol, side, lot_size, entry_price, sl, status, opened_at, pnl
    c.execute('SELECT ticket, symbol, side, lot_size, entry_price, sl, status, opened_at, pnl FROM trades WHERE status="OPEN"')
    rows = c.fetchall()
    
    print("\n=== CURRENT OPEN POSITIONS (Opus DB) ===")
    if not rows:
        print("No open positions found in opus.db.")
    else:
        for row in rows:
            print(dict(row))
    conn.close()
