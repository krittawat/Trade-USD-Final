"""Test DB save for winning params."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.db.sqlite import SQLiteStore

settings = get_settings()
print(f"DB path: {settings.sqlite_db_path}")

db = SQLiteStore(settings)
db.connect()
print("✅ Connected")

# Try saving strategy params
try:
    db.save_strategy_params(
        symbol="XAUUSDc",
        strategy_name="GOLD_SCALP_WR60",
        params={
            "SL_ATR_MULT": 2.5,
            "TP_ATR_MULT": 1.0,
            "SUPERTREND_LEN": 7,
            "SUPERTREND_MUL": 2.0,
            "MIN_SL_DISTANCE": 3.5,
        },
        win_rate=73.0,
        profit_factor=1.04,
        total_pnl=264.28,
        max_drawdown_pct=6.2,
        total_trades=614,
        backtest_days=200,
        label="SL_2.5_TP_1.0",
    )
    print("✅ Strategy params saved!")
except Exception as e:
    print(f"❌ Save failed: {e}")
    import traceback; traceback.print_exc()

# Try reading back
try:
    result = db.get_strategy_params("XAUUSDc", "GOLD_SCALP_WR60")
    if result:
        print(f"✅ Retrieved: WR={result['win_rate']}% PF={result['profit_factor']} Label={result['label']}")
        print(f"   Params: {result['params']}")
    else:
        print("⚠️ No params found")
except Exception as e:
    print(f"❌ Get failed: {e}")

# Save a test backtest result
try:
    db.save_backtest_result(
        symbol="XAUUSDc",
        strategy_name="GOLD_SCALP_WR60",
        label="SL_2.5_TP_1.0",
        params={"SL_ATR_MULT": 2.5, "TP_ATR_MULT": 1.0},
        win_rate=73.0,
        profit_factor=1.04,
        total_pnl=264.28,
        max_drawdown_pct=6.2,
        total_trades=614,
        winning_trades=448,
        losing_trades=166,
        backtest_days=200,
        expectancy=0.43,
        per_regime={"TRENDING_UP": {"wins": 238, "losses": 0, "pnl": 461.30}},
    )
    print("✅ Backtest result saved!")
except Exception as e:
    print(f"❌ Backtest save failed: {e}")
    import traceback; traceback.print_exc()

# Check all strategy_params
try:
    all_params = db.get_all_strategy_params()
    print(f"\n📋 All strategy_params ({len(all_params)}):")
    for p in all_params:
        print(f"  {p['symbol']} | {p['strategy_name']} | WR={p['win_rate']}% | Label={p['label']}")
except Exception as e:
    print(f"❌ Get all failed: {e}")

db.disconnect()
print("\n✅ Done!")
