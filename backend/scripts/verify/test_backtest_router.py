"""
Test Backtest Router — verify data-driven strategy selection works correctly.

Tests:
    1. Router returns best strategy per regime
    2. Missing regime returns default (sniper)
    3. Unknown symbol returns default
    4. Seeding populates table correctly
    5. SQLite persistence works
"""
import sys
import os
import sqlite3
import tempfile
from pathlib import Path

# Add backend to path
_BACKEND = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _BACKEND)

from app.strategy.backtest_router import BacktestRouter

PASS = 0
FAIL = 0
ERRORS = []


def test(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL, ERRORS
    if condition:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ {name}: {detail}")
        FAIL += 1
        ERRORS.append(f"{name}: {detail}")


def main():
    global PASS, FAIL

    print("\n" + "=" * 60)
    print("  🧪 Test Backtest Router")
    print("=" * 60 + "\n")

    # Create temp DB
    tmp = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(tmp)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_routing (
            symbol TEXT NOT NULL,
            regime TEXT NOT NULL,
            strategy TEXT NOT NULL,
            profit_factor REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            total_trades INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            score REAL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (symbol, regime)
        )
    """)
    conn.commit()
    conn.close()

    # ─── Test 1: Empty router returns default ───
    router = BacktestRouter(db_path=tmp)
    result = router.get_best_strategy("XAUUSDc", "TRENDING_UP")
    test("Empty router returns default",
         result == "sniper",
         f"Expected 'sniper', got '{result}'")

    # ─── Test 2: Seeding from backtest results ───
    regime_results = [
        {"strategy": "gold_session_breakout", "regime": "TRENDING_UP",
         "profit_factor": 3.69, "win_rate": 66.7, "total_trades": 3, "total_pnl": 684.45, "score": 36.2},
        {"strategy": "sniper", "regime": "TRENDING_UP",
         "profit_factor": 2.33, "win_rate": 66.7, "total_trades": 3, "total_pnl": 94.54, "score": 32.0},
        {"strategy": "gold_precision", "regime": "TRENDING_DOWN",
         "profit_factor": 2.06, "win_rate": 41.2, "total_trades": 17, "total_pnl": 530.08, "score": 18.1},
        {"strategy": "sniper", "regime": "TRENDING_DOWN",
         "profit_factor": 2.33, "win_rate": 100.0, "total_trades": 1, "total_pnl": 35.86, "score": 32.0},
        {"strategy": "sniper", "regime": "RANGING",
         "profit_factor": 2.33, "win_rate": 50.0, "total_trades": 2, "total_pnl": 22.12, "score": 32.0},
    ]

    routes = router.seed_from_backtest("XAUUSDc", regime_results)
    test("Seeding saves routes",
         routes >= 3,
         f"Expected >= 3 routes, got {routes}")

    # ─── Test 3: Router picks best per regime ───
    pick_up = router.get_best_strategy("XAUUSDc", "TRENDING_UP")
    test("TRENDING_UP picks gold_session_breakout",
         pick_up == "gold_session_breakout",
         f"Got '{pick_up}'")

    pick_down = router.get_best_strategy("XAUUSDc", "TRENDING_DOWN")
    test("TRENDING_DOWN picks best strategy",
         pick_down in ("gold_precision", "sniper"),
         f"Got '{pick_down}'")

    pick_range = router.get_best_strategy("XAUUSDc", "RANGING")
    test("RANGING picks sniper",
         pick_range == "sniper",
         f"Got '{pick_range}'")

    # ─── Test 4: Unknown regime returns default ───
    pick_unknown = router.get_best_strategy("XAUUSDc", "LOW_VOLATILITY")
    test("Unknown regime returns default",
         pick_unknown == "sniper",
         f"Got '{pick_unknown}'")

    # ─── Test 5: Unknown symbol returns default ───
    pick_other = router.get_best_strategy("EURUSDc", "TRENDING_UP")
    test("Unknown symbol returns default",
         pick_other == "sniper",
         f"Got '{pick_other}'")

    # ─── Test 6: Routing table API ───
    table = router.get_routing_table("XAUUSDc")
    test("Routing table has routes",
         "routes" in table and len(table["routes"]) >= 3,
         f"Got {table}")
    test("Routing table has metrics",
         "metrics" in table and len(table["metrics"]) >= 3,
         f"Got {table}")

    # ─── Test 7: Persistence — reload from DB ───
    router2 = BacktestRouter(db_path=tmp)
    pick2 = router2.get_best_strategy("XAUUSDc", "TRENDING_UP")
    test("Reloaded router has same picks",
         pick2 == pick_up,
         f"Reloaded '{pick2}' != original '{pick_up}'")

    # ─── Test 8: Upsert — overwrite existing route ───
    new_results = [
        {"strategy": "scalping", "regime": "TRENDING_UP",
         "profit_factor": 5.0, "win_rate": 80.0, "total_trades": 50, "total_pnl": 1000.0, "score": 99.0},
    ]
    router.seed_from_backtest("XAUUSDc", new_results)
    pick_updated = router.get_best_strategy("XAUUSDc", "TRENDING_UP")
    test("Upsert overwrites with better strategy",
         pick_updated == "scalping",
         f"Got '{pick_updated}'")

    # Cleanup
    try:
        os.unlink(tmp)
    except:
        pass

    # ─── Results ───
    print(f"\n{'='*60}")
    print(f"  Results: {PASS}/{PASS+FAIL} passed, {FAIL} failed")
    print(f"{'='*60}\n")
    if ERRORS:
        print("  Failed tests:")
        for e in ERRORS:
            print(f"    - {e}")
    return FAIL == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
