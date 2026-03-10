# -*- coding: utf-8 -*-
"""
verify_live_results.py
----------------------
Script ตรวจสอบผลการเทรดจริงจาก SQLite trade journal:
- แสดง trades ทั้งหมด (Win/Loss/Open)
- สถิติจริงแต่ละ symbol: WR, PF, Avg R:R
- สรุปกำไร/ขาดทุนสุทธิ

ใช้เพื่อพิสูจน์ว่าระบบทำงานถูกต้อง 100%
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "sqlite" / "trading.db"

def fmt(val, decimals=2):
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}"

def main():
    print("=" * 65)
    print("  ANTIGRAVITY — REAL TRADE VERIFICATION REPORT")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  DB: {DB_PATH}")
    print("=" * 65)

    if not DB_PATH.exists():
        print(f"\n  [!] Database not found: {DB_PATH}")
        print("      Bot has not yet written any trades.\n")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # ─── All closed trades ───────────────────────────────────────────
    rows = conn.execute("""
        SELECT symbol, action, entry_price, exit_price,
               stop_loss, take_profit, profit_usd,
               strategy_name, entry_time, exit_time,
               risk_usd, regime, session, mode
        FROM trade_journal
        WHERE exit_price IS NOT NULL AND profit_usd IS NOT NULL
        ORDER BY entry_time DESC
        LIMIT 50
    """).fetchall()

    if not rows:
        print("\n  No closed trades yet in journal.")
    else:
        print(f"\n  Recent Closed Trades ({len(rows)} shown):\n")
        print(f"  {'Symbol':<10} {'Side':<5} {'Result':>9} {'PnL USD':>9} {'Strategy':<20} {'Time'}")
        print("  " + "-" * 70)
        for r in rows:
            result = "WIN" if (r["profit_usd"] or 0) > 0 else "LOSS"
            ts = r["entry_time"][:16] if r["entry_time"] else "?"
            strat = (r["strategy_name"] or "?")[:20]
            print(f"  {r['symbol']:<10} {r['action']:<5} {result:>9} {fmt(r['profit_usd']):>9} {strat:<20} {ts}")

    # ─── Per-symbol stats ────────────────────────────────────────────
    stats = conn.execute("""
        SELECT
            symbol,
            COUNT(*) as total,
            SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN profit_usd <= 0 THEN 1 ELSE 0 END) as losses,
            SUM(profit_usd) as net_profit,
            SUM(CASE WHEN profit_usd > 0 THEN profit_usd ELSE 0 END) as gross_win,
            SUM(CASE WHEN profit_usd < 0 THEN ABS(profit_usd) ELSE 0 END) as gross_loss,
            AVG(risk_usd) as avg_risk
        FROM trade_journal
        WHERE exit_price IS NOT NULL AND profit_usd IS NOT NULL
        GROUP BY symbol
        ORDER BY net_profit DESC
    """).fetchall()

    if stats:
        print(f"\n\n  Performance by Symbol:\n")
        print(f"  {'Symbol':<12} {'Trades':>7} {'WR%':>7} {'PF':>7} {'Net USD':>10}")
        print("  " + "-" * 50)
        total_net = 0
        for s in stats:
            wr = (s["wins"] / s["total"] * 100) if s["total"] > 0 else 0
            pf = (s["gross_win"] / s["gross_loss"]) if s["gross_loss"] > 0 else float("inf")
            pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
            net = s["net_profit"] or 0
            total_net += net
            print(f"  {s['symbol']:<12} {s['total']:>7} {wr:>6.1f}% {pf_str:>7} {net:>10.2f}")
        print("  " + "-" * 50)
        print(f"  {'TOTAL':<12} {'':>7} {'':>7} {'':>7} {total_net:>10.2f}")

    # ─── Open positions ──────────────────────────────────────────────
    open_pos = conn.execute("""
        SELECT symbol, action, entry_price, stop_loss, take_profit,
               risk_usd, strategy_name, entry_time
        FROM trade_journal
        WHERE exit_price IS NULL
        ORDER BY entry_time DESC
    """).fetchall()

    if open_pos:
        print(f"\n\n  Open Positions ({len(open_pos)}):\n")
        print(f"  {'Symbol':<10} {'Side':<5} {'Entry':>10} {'SL':>10} {'TP':>10} {'Strategy'}")
        print("  " + "-" * 65)
        for p in open_pos:
            strat = (p["strategy_name"] or "?")[:20]
            print(f"  {p['symbol']:<10} {p['action']:<5} {fmt(p['entry_price'],5):>10} "
                  f"{fmt(p['stop_loss'],5):>10} {fmt(p['take_profit'],5):>10} {strat}")
    else:
        print("\n\n  No open positions in journal.")

    # ─── Summary ─────────────────────────────────────────────────────
    summary = conn.execute("""
        SELECT
            COUNT(*) as total_closed,
            SUM(CASE WHEN profit_usd > 0 THEN 1 ELSE 0 END) as total_wins,
            SUM(profit_usd) as total_net
        FROM trade_journal
        WHERE exit_price IS NOT NULL AND profit_usd IS NOT NULL
    """).fetchone()

    print("\n\n" + "=" * 65)
    if summary and summary["total_closed"] > 0:
        wr = summary["total_wins"] / summary["total_closed"] * 100
        print(f"  OVERALL SUMMARY")
        print(f"  Total Closed : {summary['total_closed']} trades")
        print(f"  Win Rate     : {wr:.1f}%")
        print(f"  Net PnL      : ${fmt(summary['total_net'])} USD")
        live_ready = wr >= 50 and (summary["total_net"] or 0) > 0
        status = "LIVE-READY" if live_ready else "NEEDS MORE DATA"
        print(f"  Status       : {status}")
    else:
        print("  No closed trades yet — bot is building trade history.")
    print("=" * 65 + "\n")

    conn.close()

if __name__ == "__main__":
    main()
