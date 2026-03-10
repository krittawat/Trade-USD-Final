import sqlite3
try:
    conn = sqlite3.connect('backend/data/sqlite/trading.db', timeout=3)
    cur = conn.cursor()
    cur.execute('SELECT symbol, volume_max FROM profiles WHERE symbol IN ("XAUUSDc", "XAGUSDc", "BTCUSDc")')
    rows = cur.fetchall()
    for r in rows:
        print(f'{r[0]}: {r[1]} lots')
    if not rows:
        print("No rows found")
    conn.close()
except Exception as e:
    print('Failed:', e)
