import sqlite3
import os

# ใช้ path จาก Settings
db_path = "backend/data/sqlite/trading.db"
if not os.path.exists(db_path):
    db_path = "data/sqlite/trading.db"
if not os.path.exists(db_path):
    # ค้นหาอัตโนมัติ
    import glob
    candidates = glob.glob("**/*.db", recursive=True)
    print("DB files:", candidates)
    exit()

conn = sqlite3.connect(db_path)
print(f"Connected to: {db_path}\n")

# Tables
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

# Trade Journal
for t in ["trade_journal", "trades", "journal"]:
    if t in tables:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()]
        strat_col = next((c for c in cols if "strat" in c.lower()), None)
        profit_col = next((c for c in cols if "profit" in c.lower() or "pnl" in c.lower()), None)
        sym_col = next((c for c in cols if "symbol" in c.lower()), None)
        
        if strat_col and profit_col and sym_col:
            print("=" * 70)
            print(f"Real Trades ({t})")
            print("=" * 70)
            rows = conn.execute(f"""
                SELECT {strat_col}, {sym_col},
                    COUNT(*), 
                    SUM(CASE WHEN {profit_col} > 0 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN {profit_col} <= 0 THEN 1 ELSE 0 END),
                    ROUND(100.0*SUM(CASE WHEN {profit_col}>0 THEN 1 ELSE 0 END)/COUNT(*),1),
                    ROUND(SUM({profit_col}),4)
                FROM {t}
                WHERE {strat_col} IS NOT NULL AND {strat_col} != ''
                GROUP BY {strat_col}, {sym_col}
                ORDER BY COUNT(*) DESC
            """).fetchall()
            for r in rows:
                print(f"  {str(r[0]):25s} {str(r[1]):10s}  {r[2]:3d} trades  W={r[3]} L={r[4]}  WR={r[5]}%  PnL={r[6]}")
        break

# Shadow
if "shadow_trades" in tables:
    print("\n" + "=" * 70)
    print("Shadow Trades")
    print("=" * 70)
    rows = conn.execute("""
        SELECT strategy_name, symbol, COUNT(*),
            SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END),
            SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END)
        FROM shadow_trades WHERE strategy_name IS NOT NULL
        GROUP BY strategy_name, symbol ORDER BY COUNT(*) DESC
    """).fetchall()
    for r in rows:
        ev = r[3]+r[4]
        wr = round(100.0*r[3]/ev,1) if ev>0 else 0
        print(f"  {str(r[0]):25s} {str(r[1]):10s}  {r[2]:3d} total  W={r[3]} L={r[4]}  WR={wr}%")

# Strategy performance (brain memory)
if "strategy_performance" in tables:
    print("\n" + "=" * 70)
    print("Brain Memory — Strategy Performance")
    print("=" * 70)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(strategy_performance)").fetchall()]
    rows = conn.execute("SELECT * FROM strategy_performance ORDER BY total_trades DESC LIMIT 20").fetchall()
    print(f"  Cols: {cols}")
    for r in rows:
        print(f"  {r}")

conn.close()
