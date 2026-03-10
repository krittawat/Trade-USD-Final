"""Check SL hits since bot restart — FIXED reason codes."""
import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone

mt5.initialize()

now = datetime.now(timezone.utc)
since = now - timedelta(hours=2)

deals = mt5.history_deals_get(since, now)
if not deals:
    print("No deals in last 2 hours")
    mt5.shutdown()
    exit()

print(f"=== Deals in last 2 hours ({len(deals)} total) ===\n")

# MT5 Reason codes (correct!):
# 0=CLIENT, 1=MOBILE, 2=WEB, 3=EXPERT, 4=SL, 5=TP, 6=SO
REASON_MAP = {0: "CLIENT", 1: "MOBILE", 2: "WEB", 3: "EXPERT", 4: "SL", 5: "TP", 6: "SO"}

sl_hits = 0
tp_hits = 0
total_profit = 0.0
wins = []
losses = []

for d in deals:
    if d.entry == 1:  # DEAL_ENTRY_OUT (close)
        profit = d.profit + d.commission + d.swap
        total_profit += profit
        reason = REASON_MAP.get(d.reason, f"R={d.reason}")

        if d.reason == 4:   # SL
            sl_hits += 1
            losses.append(profit)
        elif d.reason == 5:  # TP
            tp_hits += 1
            wins.append(profit)
        else:
            if profit >= 0:
                wins.append(profit)
            else:
                losses.append(profit)

        t = datetime.fromtimestamp(d.time)
        emoji = "+" if profit >= 0 else "-"
        print(f"  {emoji} {t.strftime('%H:%M')} {d.symbol:12s} {reason:7s} ${profit:+.2f}  vol={d.volume}")

print()
total_closed = sl_hits + tp_hits
print("--- Summary (Last 2 Hours) ---")
print(f"SL hits:  {sl_hits}")
print(f"TP hits:  {tp_hits}")
print(f"Net P/L:  ${total_profit:+.2f}")
if wins:
    avg_w = sum(wins) / len(wins)
    print(f"Avg Win:  ${avg_w:+.2f} ({len(wins)} trades)")
if losses:
    avg_l = sum(losses) / len(losses)
    print(f"Avg Loss: ${avg_l:+.2f} ({len(losses)} trades)")
if wins and losses:
    rr = abs(avg_w / avg_l)
    print(f"Actual RR:  {rr:.2f}")
    print(f"Old RR:     0.45 (was avg win $0.88 vs avg loss $1.97)")
if total_closed > 0:
    wr = tp_hits / total_closed * 100
    print(f"Win Rate (SL/TP only): {wr:.0f}%")

mt5.shutdown()
