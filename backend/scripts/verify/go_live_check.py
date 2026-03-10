"""
Go-Live Readiness Check
"""
import sys, os, io, traceback

# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))

results = {}

# 1. Gold Elite Import + Pressure
print("=" * 60)
print("1. Gold Elite: Import + Pressure Check")
print("=" * 60)
try:
    from app.strategy.templates.gold_elite import GoldEliteStrategy
    strat = GoldEliteStrategy()
    assert hasattr(strat, '_score_pressure'), "_score_pressure method missing!"
    
    pressure_buy = {"buying_pressure": 0.8, "selling_pressure": 0.2, "score": 5, "is_climax": False}
    b, s, br, sr = strat._score_pressure(pressure_buy)
    assert b == 10, f"Expected buy boost 10, got {b}"
    assert s == 0, f"Expected sell boost 0, got {s}"
    print(f"  [PASS] _score_pressure: buy_boost={b}, sell_boost={s}, reasons={br}")
    
    pressure_climax = {"buying_pressure": 0.75, "selling_pressure": 0.25, "score": 5, "is_climax": True}
    b2, s2, br2, sr2 = strat._score_pressure(pressure_climax)
    assert b2 == 15, f"Expected buy boost 15, got {b2}"
    print(f"  [PASS] Climax boost: buy_boost={b2}")
    
    results["gold_elite_pressure"] = "PASS"
except Exception as e:
    print(f"  [FAIL] {e}")
    traceback.print_exc()
    results["gold_elite_pressure"] = f"FAIL: {e}"

# 2. Risk Gate
print("\n" + "=" * 60)
print("2. Risk Gate: Import")
print("=" * 60)
try:
    from app.risk.gate import PreTradeGate
    print("  [PASS] PreTradeGate imported")
    results["risk_gate"] = "PASS"
except Exception as e:
    print(f"  [FAIL] {e}")
    results["risk_gate"] = f"FAIL: {e}"

# 3. Strategy Factory
print("\n" + "=" * 60)
print("3. Strategy Factory: Registry")
print("=" * 60)
try:
    from app.strategy.factory import StrategyFactory
    factory = StrategyFactory()
    strats = list(factory._strategies.keys())
    print(f"  [PASS] {len(strats)} strategies: {strats[:8]}...")
    results["factory"] = f"PASS ({len(strats)} strats)"
except Exception as e:
    print(f"  [FAIL] {e}")
    traceback.print_exc()
    results["factory"] = f"FAIL: {e}"

# 4. Shadow Evaluator
print("\n" + "=" * 60)
print("4. Shadow Evaluator")
print("=" * 60)
try:
    from app.brain.shadow_evaluator import ShadowEvaluator
    evaluator = ShadowEvaluator()
    assert hasattr(evaluator, 'evaluate_pending')
    print("  [PASS] ShadowEvaluator ready")
    results["shadow_evaluator"] = "PASS"
except Exception as e:
    print(f"  [FAIL] {e}")
    results["shadow_evaluator"] = f"FAIL: {e}"

# 5. MasterLoop: shadow cache code check
print("\n" + "=" * 60)
print("5. MasterLoop: Shadow Cache Code")
print("=" * 60)
try:
    with open("app/master_loop.py", "r", encoding="utf-8") as f:
        content = f.read()
    has_cache = "_shadow_candle_cache[symbol] = m5_candles" in content
    status = "PASS" if has_cache else "FAIL"
    print(f"  [{status}] Shadow candle cache populated: {has_cache}")
    results["shadow_cache"] = status
except Exception as e:
    print(f"  [FAIL] {e}")
    results["shadow_cache"] = f"FAIL: {e}"

# 6. DB Connection
print("\n" + "=" * 60)
print("6. SQLite DB")
print("=" * 60)
try:
    import sqlite3
    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/sqlite/trading.db"))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    shadow_count = conn.execute("SELECT count(*) as cnt FROM shadow_trades").fetchone()["cnt"]
    pending = conn.execute("SELECT count(*) as cnt FROM shadow_trades WHERE outcome = 'PENDING' OR outcome IS NULL").fetchone()["cnt"]
    evaluated = conn.execute("SELECT count(*) as cnt FROM shadow_trades WHERE outcome IN ('WIN', 'LOSS', 'EXPIRED')").fetchone()["cnt"]
    
    print(f"  [PASS] DB connected: {db_path}")
    print(f"         Shadows: {shadow_count} total, {pending} pending, {evaluated} evaluated")
    conn.close()
    results["db"] = f"PASS (shadows: {shadow_count})"
except Exception as e:
    print(f"  [FAIL] {e}")
    results["db"] = f"FAIL: {e}"

# 7. Execution Pipeline
print("\n" + "=" * 60)
print("7. Execution Pipeline")
print("=" * 60)
try:
    from app.execution.pipeline import ExecutionPipeline
    print("  [PASS] ExecutionPipeline imported")
    results["pipeline"] = "PASS"
except Exception as e:
    print(f"  [FAIL] {e}")
    results["pipeline"] = f"FAIL: {e}"

# SUMMARY
print("\n" + "=" * 60)
print("GO-LIVE READINESS SUMMARY")
print("=" * 60)
all_pass = True
for k, v in results.items():
    tag = "[OK]" if "PASS" in v else "[XX]"
    print(f"  {tag} {k}: {v}")
    if "FAIL" in v:
        all_pass = False

print()
if all_pass:
    print("[GO] ALL CHECKS PASSED -- READY FOR LIVE (with caution)")
    print("     Start LIVE mode, monitor first 30 min closely.")
else:
    print("[STOP] SOME CHECKS FAILED -- DO NOT GO LIVE")
    print("       Fix failed checks before enabling LIVE mode.")
