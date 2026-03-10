"""Quick verification of GBPUSDc backtest DB persistence."""
import sys
sys.path.insert(0, "backend")

from app.core.config import get_settings
from app.db.sqlite import SQLiteStore

db = SQLiteStore(get_settings())
db.connect()

# 1. Audit trail
results = db.get_backtest_results(symbol="GBPUSDc")
print(f"📋 Audit trail: {len(results)} records")
for r in results[:5]:
    label = r.get("label", "?")
    print(f"  {label:25s} WR={r['win_rate']}% PF={r['profit_factor']} PnL=${r['total_pnl']}")
if len(results) > 5:
    print(f"  ... and {len(results) - 5} more")

# 2. Active params
params = db.get_strategy_params("GBPUSDc", "gbp_session_breakout")
print(f"\n🏆 Active strategy params:")
if params:
    for k, v in params.items():
        print(f"  {k}: {v}")
else:
    print("  ❌ None found!")

db.disconnect()
