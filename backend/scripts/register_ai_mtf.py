"""Fix ai_mtf_v3 DB entry and restart-ready check."""
import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'sqlite', 'trading.db')

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Fix suitable_regimes to JSON format
regimes = json.dumps(["trending_up", "trending_down", "high_volatility", "breakout", "ranging", "low_volatility"])
cur.execute("UPDATE strategy_registry SET suitable_regimes=?, priority=110 WHERE strategy_name='ai_mtf_v3'", (regimes,))
conn.commit()

# Verify all entries
rows = cur.execute("SELECT strategy_name, priority, suitable_regimes, is_active FROM strategy_registry ORDER BY priority DESC").fetchall()
print(f"Total strategies: {len(rows)}")
print(f"\n{'Strategy':<30s} {'Pr':>3s} {'Active':>6s} Regimes")
print("-" * 80)
for r in rows:
    name, pri, reg_str, active = r
    # Test json parse
    try:
        regs = json.loads(reg_str)
        reg_ok = f"✅ {len(regs)} regimes"
    except:
        reg_ok = f"❌ INVALID JSON: {reg_str[:40]}"
    print(f"  {name:<28s} {pri:>3d} {active:>6d} {reg_ok}")

conn.close()
print("\nDone!")
