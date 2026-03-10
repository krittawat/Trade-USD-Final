"""Quick trade stats checker."""
import MetaTrader5 as mt5
from collections import defaultdict

mt5.initialize()

# Get deals using timestamps (Feb 20 to Feb 26, 2026)
deals = mt5.history_deals_get(1771400000, 1772100000)
if not deals:
    print("No deals found")
    mt5.shutdown()
    exit()

exits = [d for d in deals if d.entry == 1]
wins = [d for d in exits if d.profit > 0]
losses = [d for d in exits if d.profit <= 0]

tw = sum(d.profit for d in wins)
tl = sum(d.profit for d in losses)

print("=" * 50)
print("TRADE STATISTICS (Last 7 Days)")
print("=" * 50)
print(f"Total Closed: {len(exits)}")
print(f"Wins: {len(wins)}  Losses: {len(losses)}")

if exits:
    wr = len(wins) / len(exits) * 100
    print(f"Win Rate: {wr:.1f}%")

print(f"Total Win: +{tw:.0f} USC")
print(f"Total Loss: {tl:.0f} USC")
print(f"Net P/L: {tw + tl:.0f} USC")

if tl != 0:
    print(f"Profit Factor: {abs(tw / tl):.2f}")

if wins:
    aw = tw / len(wins)
    print(f"Avg Win: +{aw:.1f} USC")
if losses:
    al = tl / len(losses)
    print(f"Avg Loss: {al:.1f} USC")
    if wins:
        print(f"Avg R:R: {abs(aw / al):.2f}")

print()
print("--- Per Symbol ---")
syms = defaultdict(list)
for d in exits:
    syms[d.symbol].append(d)

for sym in sorted(syms):
    dd = syms[sym]
    w = len([d for d in dd if d.profit > 0])
    l = len([d for d in dd if d.profit <= 0])
    t = w + l
    swr = w / t * 100 if t > 0 else 0
    net = sum(d.profit for d in dd)
    print(f"  {sym}: {t} trades  WR={swr:.0f}%  Net={net:.0f} USC")

print()
print("--- Account ---")
ai = mt5.account_info()
if ai:
    print(f"Balance: {ai.balance:.0f} USC")
    print(f"Equity: {ai.equity:.0f} USC")
    print(f"Floating: {ai.equity - ai.balance:.0f} USC")

pos = mt5.positions_get()
print(f"Open positions: {len(pos) if pos else 0}")
if pos:
    for p in pos:
        ptype = "BUY" if p.type == 0 else "SELL"
        print(f"  {p.symbol} {ptype} {p.volume} @{p.price_open:.2f} now={p.price_current:.2f} P/L={p.profit:.0f}")

mt5.shutdown()
