"""Read brain.db results and analyze daily profit potential."""
import sys; sys.path.insert(0, 'backend')
import sqlite3
import json

db = sqlite3.connect("backend/data/sqlite/brain.db")
db.row_factory = sqlite3.Row

# Read evolved params for Gold
rows = db.execute("""
    SELECT strategy_name, symbol, regime, params, score, updated_at
    FROM evolved_params
    WHERE symbol LIKE '%XAUUSD%'
    ORDER BY score DESC
    LIMIT 10
""").fetchall()

print(f"=== Top Gold Strategies in brain.db ({len(rows)} results) ===\n")
for i, row in enumerate(rows, 1):
    params = json.loads(row['params']) if isinstance(row['params'], str) else row['params']
    label = params.get('_label', 'unknown')
    wr = params.get('_win_rate', 0)
    pf = params.get('_profit_factor', 0)
    profit = params.get('_total_profit', 0)
    dd = params.get('_max_dd', 0)
    trades = params.get('_total_trades', 0)
    daily_wr = params.get('_daily_wr', 0)
    cls = params.get('_strategy_class', '')
    rank = params.get('_rank', '')
    
    # Calculate daily profit
    # Assume 200 trading days = profit / 200
    # 140 unique trading days in 200 calendar days (Mon-Fri)
    trading_days = 140
    daily_avg = profit / trading_days if trading_days > 0 else 0
    weekly_avg = daily_avg * 5
    
    print(f"  #{i} [{label}] ({cls})")
    print(f"     Score={row['score']:.1f} | WR={wr}% | PF={pf} | Profit=${profit:.2f}")
    print(f"     DD={dd}% | Trades={trades} | DailyWR={daily_wr}%")
    print(f"     📊 Avg Daily: ${daily_avg:.2f} (~{daily_avg * 35:.0f} THB) | Weekly: ${weekly_avg:.2f} (~{weekly_avg * 35:.0f} THB)")
    print(f"     SL_ATR={params.get('SL_ATR_MULT')} | TP_ATR={params.get('TP_ATR_MULT')} | ST={params.get('SUPERTREND_LEN')}x{params.get('SUPERTREND_MUL')}")
    print()

# Also check how many params are saved total
total = db.execute("SELECT COUNT(*) FROM evolved_params WHERE symbol LIKE '%XAUUSD%'").fetchone()[0]
print(f"Total Gold entries in brain.db: {total}")

db.close()
