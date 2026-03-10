"""Quick script to pull win rate per symbol from MT5 history."""
import MetaTrader5 as mt5
from datetime import datetime, timedelta
from collections import defaultdict
import sys

if not mt5.initialize():
    print("MT5 init failed:", mt5.last_error())
    sys.exit(1)

# Get deals for last 30 days
now = datetime.now()
deals = mt5.history_deals_get(now - timedelta(days=30), now)
if not deals or len(deals) == 0:
    print("No deals found in last 30 days")
    mt5.shutdown()
    sys.exit(0)

# Group by symbol — only count closing deals (entry=1 = out)
stats = defaultdict(lambda: {"wins": 0, "losses": 0, "profit": 0.0, "be": 0})
for d in deals:
    if d.symbol == "" or d.profit == 0 and d.entry != 1:
        continue
    if d.entry == 1:  # deal OUT = position closed
        sym = d.symbol
        stats[sym]["profit"] += d.profit
        if d.profit > 0:
            stats[sym]["wins"] += 1
        elif d.profit < 0:
            stats[sym]["losses"] += 1
        else:
            stats[sym]["be"] += 1

print(f"{'Symbol':<15} {'Trades':>7} {'W':>5} {'L':>5} {'BE':>4} {'WR%':>7} {'PnL':>12}")
print("-" * 60)
for sym in sorted(stats.keys()):
    s = stats[sym]
    total = s["wins"] + s["losses"] + s["be"]
    wr = (s["wins"] / (s["wins"] + s["losses"]) * 100) if (s["wins"] + s["losses"]) > 0 else 0
    print(f"{sym:<15} {total:>7} {s['wins']:>5} {s['losses']:>5} {s['be']:>4} {wr:>6.1f}% {s['profit']:>12.2f}")

total_w = sum(s["wins"] for s in stats.values())
total_l = sum(s["losses"] for s in stats.values())
total_be = sum(s["be"] for s in stats.values())
total_t = total_w + total_l + total_be
total_wr = (total_w / (total_w + total_l) * 100) if (total_w + total_l) > 0 else 0
total_p = sum(s["profit"] for s in stats.values())
print("-" * 60)
print(f"{'TOTAL':<15} {total_t:>7} {total_w:>5} {total_l:>5} {total_be:>4} {total_wr:>6.1f}% {total_p:>12.2f}")

# Also show open positions
positions = mt5.positions_get()
if positions:
    print(f"\n--- Open Positions ({len(positions)}) ---")
    print(f"{'Symbol':<15} {'Type':>5} {'Lots':>6} {'Price':>10} {'SL':>10} {'TP':>10} {'Profit':>10}")
    print("-" * 72)
    for p in positions:
        ptype = "BUY" if p.type == 0 else "SELL"
        print(f"{p.symbol:<15} {ptype:>5} {p.volume:>6.2f} {p.price_open:>10.2f} {p.sl:>10.2f} {p.tp:>10.2f} {p.profit:>10.2f}")

mt5.shutdown()
