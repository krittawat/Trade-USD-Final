# Quick SL fix verification runner
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.scripts.backtest_sl_fix_compare import *

fetcher.connect()
df = load_data("BTCUSD", "M3", 8640)
if df is None:
    print("No data")
    sys.exit(1)

print(f"Loaded {len(df)} bars")

v1 = run_backtest(df, "BTCUSD", generate_signals_v1, "V1")
v2 = run_backtest(df, "BTCUSD", generate_signals_v2, "V2")

print("")
print("=== V1 (Tight SL 0.6x ATR) ===")
for k, v in v1.items():
    print(f"  {k}: {v}")

print("")
print("=== V2 (Wide SL 1.2x + Floor 1.5x ATR) ===")
for k, v in v2.items():
    print(f"  {k}: {v}")

print("")
print("=== COMPARISON ===")
print(f"  Win Rate:    V1={v1['win_rate']:.1f}%  V2={v2['win_rate']:.1f}%  delta={v2['win_rate']-v1['win_rate']:+.1f}%")
print(f"  PF:          V1={v1['profit_factor']:.2f}  V2={v2['profit_factor']:.2f}  delta={v2['profit_factor']-v1['profit_factor']:+.2f}")
print(f"  SL Hits:     V1={v1['sl_hits']}  V2={v2['sl_hits']}  delta={v2['sl_hits']-v1['sl_hits']:+d}")
print(f"  TP Hits:     V1={v1['tp_hits']}  V2={v2['tp_hits']}  delta={v2['tp_hits']-v1['tp_hits']:+d}")
print(f"  Max DD:      V1={v1['max_dd_pct']:.1f}%  V2={v2['max_dd_pct']:.1f}%")
print(f"  Total PnL:   V1=${v1['total_pnl']:.2f}  V2=${v2['total_pnl']:.2f}")
print(f"  Avg SL Dist: V1=${v1['avg_sl_dist']:.2f}  V2=${v2['avg_sl_dist']:.2f}")
print(f"  Final Eq:    V1=${v1['final_equity']:.2f}  V2=${v2['final_equity']:.2f}")

fetcher.disconnect()
