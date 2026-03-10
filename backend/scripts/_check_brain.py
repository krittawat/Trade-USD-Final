"""Quick check: what's saved in brain.db for Gold"""
import sys; sys.path.insert(0, 'backend')
from app.brain.memory_store import MemoryStore

store = MemoryStore()
store.connect()

# Check latest evolved params for gold
results = store.get_evolved_params('gold_scalp_pro', 'XAUUSDc', 'ALL')
print(f"=== gold_scalp_pro / XAUUSDc / ALL: {len(results) if results else 0} results ===")
if results:
    # Results is likely a list or dict-like object
    for i, r in enumerate(list(results)[:3]):
        print(f"  [{i}] {r}")

results2 = store.get_evolved_params('gold_scalp_wr60', 'XAUUSDc', 'ALL')
print(f"\n=== gold_scalp_wr60 / XAUUSDc / ALL: {len(results2) if results2 else 0} results ===")
if results2:
    for i, r in enumerate(list(results2)[:3]):
        print(f"  [{i}] {r}")
