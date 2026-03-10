"""Quick DB schema inspector."""
import sqlite3

conn = sqlite3.connect(r"backend/data/sqlite/trade.db")
c = conn.cursor()

tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print(f"Tables ({len(tables)}):")
for t in tables:
    cols = c.execute(f"PRAGMA table_info({t[0]})").fetchall()
    col_names = [col[1] for col in cols]
    print(f"  {t[0]}: {col_names}")

conn.close()
