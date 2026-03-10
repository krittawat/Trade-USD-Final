"""Quick script to read XAG training results from brain.db"""
import sqlite3, json, sys

conn = sqlite3.connect("backend/data/sqlite/brain.db")
c = conn.cursor()
c.execute(
    "SELECT strategy_name, symbol, params, score, updated_at "
    "FROM evolved_params WHERE symbol LIKE '%XAG%' ORDER BY score DESC"
)
rows = c.fetchall()
print(f"\n=== XAG Training Results in brain.db ({len(rows)} entries) ===\n")

for i, r in enumerate(rows, 1):
    p = json.loads(r[2]) if isinstance(r[2], str) else r[2]
    label = p.get("_label", "N/A")
    wr = p.get("_win_rate", 0)
    pf = p.get("_profit_factor", 0)
    pnl = p.get("_total_profit", 0)
    dd = p.get("_max_dd", 0)
    trades = p.get("_total_trades", 0)
    daily_wr = p.get("_daily_wr", 0)
    trained = p.get("_trained_at", "N/A")
    
    print(f"[{i}] {r[0]} ({label})")
    print(f"    Score    : {r[3]:.1f}")
    print(f"    WR       : {wr:.1f}%  |  PF: {pf:.2f}  |  PnL: ${pnl:.2f}")
    print(f"    DD       : {dd:.1f}%  |  Trades: {trades}  |  DailyWR: {daily_wr:.1f}%")
    print(f"    Trained  : {trained}")
    
    clean = {k: v for k, v in p.items() if not k.startswith("_")}
    if clean:
        print(f"    Params   : {json.dumps(clean)}")
    print()

conn.close()
