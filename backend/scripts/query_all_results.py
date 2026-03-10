"""Query all backtest results and strategy params from DB to inform daily profit plan."""
import sys
sys.path.insert(0, "backend")

from app.core.config import get_settings
from app.db.sqlite import SQLiteStore

db = SQLiteStore(get_settings())
db.connect()

# 1. All active strategy params 
print("=" * 70)
print("📋 ACTIVE STRATEGY PARAMS IN DB")
print("=" * 70)
try:
    rows = db._conn.execute(
        "SELECT symbol, strategy_name, win_rate, profit_factor, total_pnl, total_trades, backtest_days, label, params_json FROM strategy_params WHERE is_active=1"
    ).fetchall()
    for r in rows:
        print(f"  {r[0]:12s} | {r[1]:25s} | WR={r[2]}% PF={r[3]} PnL=${r[4]} Trades={r[5]} Days={r[6]} | {r[7]}")
        print(f"    Params: {r[8][:100]}")
except Exception as e:
    print(f"  Error: {e}")

# 2. Best results per symbol
print("\n" + "=" * 70)
print("📊 BEST BACKTEST RESULTS PER SYMBOL (top 3 by PnL)")
print("=" * 70)
try:
    symbols = db._conn.execute(
        "SELECT DISTINCT symbol FROM backtest_results"
    ).fetchall()
    for (sym,) in symbols:
        rows = db._conn.execute(
            "SELECT strategy_name, label, win_rate, profit_factor, total_pnl, total_trades, backtest_days FROM backtest_results WHERE symbol=? ORDER BY total_pnl DESC LIMIT 3",
            (sym,)
        ).fetchall()
        print(f"\n  {sym}:")
        for r in rows:
            print(f"    {r[0]:25s} {r[1]:20s} WR={r[2]:>5}% PF={r[3]:>5} PnL=${r[4]:>8} Trades={r[5]:>3} Days={r[6]}")
except Exception as e:
    print(f"  Error: {e}")

# 3. Count total results per symbol
print("\n" + "=" * 70)
print("📈 TOTAL RESULTS PER SYMBOL")
print("=" * 70)
try:
    rows = db._conn.execute(
        "SELECT symbol, COUNT(*), MAX(win_rate), MAX(total_pnl) FROM backtest_results GROUP BY symbol"
    ).fetchall()
    for r in rows:
        print(f"  {r[0]:12s}: {r[1]:>3} configs tested | Best WR={r[2]}% | Best PnL=${r[3]}")
except Exception as e:
    print(f"  Error: {e}")

db.disconnect()
