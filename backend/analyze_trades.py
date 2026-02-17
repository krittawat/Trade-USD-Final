"""Deep trade history analysis — find SL hit patterns and RR ratios."""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "backend", "data", "sqlite", "trading.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# 1. Trade journal schema
cursor.execute("PRAGMA table_info(trade_journal)")
cols = [(r[1], r[2]) for r in cursor.fetchall()]
print(f"trade_journal columns: {cols}")

# 2. All trades from journal
cursor.execute("SELECT COUNT(*) FROM trade_journal")
total = cursor.fetchone()[0]
print(f"\nTotal trades: {total}")

# 3. Recent 30 trades with full details
cursor.execute("SELECT * FROM trade_journal ORDER BY id DESC LIMIT 30")
rows = cursor.fetchall()
print(f"\n--- Last 30 trades ---")
for row in rows:
    d = dict(row)
    symbol = d.get('symbol', '')
    action = d.get('action', '')
    entry = d.get('entry_price', 0)
    sl = d.get('stop_loss', 0)
    tp = d.get('take_profit', 0)
    strategy = d.get('strategy_name', '')
    risk_pct = d.get('risk_pct', 0)
    lot = d.get('lot_size', 0)
    ts = d.get('timestamp', '')
    mode = d.get('mode', '')
    
    sl_dist = abs(entry - sl) if entry and sl else 0
    tp_dist = abs(tp - entry) if entry and tp else 0
    rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0
    
    print(f"  {ts[:19]} | {symbol:10s} | {action:4s} | entry={entry:.5f} | SL={sl:.5f} | TP={tp:.5f} | SL_dist={sl_dist:.5f} | TP_dist={tp_dist:.5f} | RR={rr} | lot={lot} | risk={risk_pct}% | {strategy} | {mode}")

# 4. Stats per strategy
print(f"\n--- Per-strategy stats ---")
cursor.execute("""
    SELECT strategy_name, COUNT(*) as cnt, 
           AVG(risk_pct) as avg_risk,
           MIN(entry_price) as min_entry,
           MAX(entry_price) as max_entry
    FROM trade_journal 
    GROUP BY strategy_name
    ORDER BY cnt DESC
""")
for row in cursor.fetchall():
    d = dict(row)
    print(f"  {d['strategy_name']:30s} | trades={d['cnt']} | avg_risk={d['avg_risk']:.2f}%")

# 5. Stats per symbol
print(f"\n--- Per-symbol stats ---")
cursor.execute("""
    SELECT symbol, COUNT(*) as cnt, AVG(risk_pct) as avg_risk
    FROM trade_journal 
    GROUP BY symbol
    ORDER BY cnt DESC
""")
for row in cursor.fetchall():
    d = dict(row)
    print(f"  {d['symbol']:12s} | trades={d['cnt']} | avg_risk={d['avg_risk']:.2f}%")

# 6. Check MT5 for closed positions (SL hits)
print("\n\n--- Checking MT5 history for SL hits ---")
try:
    import MetaTrader5 as mt5
    if mt5.initialize():
        from datetime import datetime, timedelta
        now = datetime.now()
        deals = mt5.history_deals_get(now - timedelta(days=7), now)
        if deals:
            print(f"Total deals in last 7 days: {len(deals)}")
            # Filter for closed by SL
            sl_hits = 0
            tp_hits = 0
            total_closed = 0
            losses = []
            wins = []
            for deal in deals:
                if deal.entry == 1:  # exit deal
                    total_closed += 1
                    if deal.profit < 0:
                        sl_hits += 1
                        losses.append(deal)
                    elif deal.profit > 0:
                        tp_hits += 1
                        wins.append(deal)
            
            print(f"Closed positions: {total_closed}")
            print(f"SL hits (losses): {sl_hits}")
            print(f"TP hits (wins): {tp_hits}")
            if total_closed > 0:
                print(f"Win rate: {tp_hits/total_closed*100:.1f}%")
            
            total_loss = sum(d.profit for d in losses)
            total_win = sum(d.profit for d in wins)
            print(f"Total Loss: ${total_loss:.2f}")
            print(f"Total Win: ${total_win:.2f}")
            print(f"Net P/L: ${total_loss + total_win:.2f}")
            
            if losses:
                avg_loss = total_loss / len(losses)
                print(f"Avg Loss: ${avg_loss:.2f}")
            if wins:
                avg_win = total_win / len(wins)
                print(f"Avg Win: ${avg_win:.2f}")
            if losses and wins:
                print(f"RR Ratio (avg win/avg loss): {abs(avg_win/avg_loss):.2f}")
            
            # Show recent closed deals details
            print(f"\n--- Recent 20 closed deals ---")
            exit_deals = [d for d in deals if d.entry == 1]
            for deal in exit_deals[-20:]:
                reason_map = {0: "CLIENT", 1: "MOBILE", 2: "WEB", 3: "MT5", 4: "SL", 5: "TP", 6: "SO"}
                reason_str = reason_map.get(deal.reason, f"R{deal.reason}")
                print(f"  {datetime.fromtimestamp(deal.time).strftime('%m-%d %H:%M')} | {deal.symbol:12s} | P/L=${deal.profit:+.2f} | Vol={deal.volume} | Reason={reason_str} | Comment={deal.comment}")
            
            # Per-symbol breakdown
            print(f"\n--- Per-symbol P/L (7 days) ---")
            from collections import defaultdict
            sym_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
            for deal in deals:
                if deal.entry == 1:
                    s = sym_stats[deal.symbol]
                    s["pnl"] += deal.profit
                    if deal.profit > 0:
                        s["wins"] += 1
                    elif deal.profit < 0:
                        s["losses"] += 1
            for sym, s in sorted(sym_stats.items()):
                total = s["wins"] + s["losses"]
                wr = s["wins"]/total*100 if total > 0 else 0
                print(f"  {sym:12s} | W={s['wins']:2d} L={s['losses']:2d} | WR={wr:.0f}% | P/L=${s['pnl']:+.2f}")
        else:
            print("No deals found in history")
        mt5.shutdown()
    else:
        print("MT5 not available")
except Exception as e:
    print(f"MT5 Error: {e}")

conn.close()
