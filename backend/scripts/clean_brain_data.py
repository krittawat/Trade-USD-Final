"""
Clean brain.db — fix garbage data quality issues.

Problems:
    1. PF = raw USD instead of ratio when total_loss = 0 (e.g. pf=86.89)
    2. All-win records (total_losses = 0) with few trades = overfit

Fix:
    1. Cap PF at 10.0 for all rows
    2. Recalculate PF from total_profit / total_loss
    3. Delete rows with total_trades < 5 (too few to be meaningful)
"""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data/sqlite/brain.db")
MAX_PF_CAP = 10.0

def main():
    if not DB_PATH.exists():
        print(f"❌ {DB_PATH} not found")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    print("=== BEFORE CLEANUP ===")
    rows = conn.execute("""
        SELECT id, strategy_name, symbol, regime, session,
               win_rate, profit_factor, total_trades, total_wins, total_losses,
               total_profit, total_loss
        FROM strategy_performance
        ORDER BY profit_factor DESC
    """).fetchall()

    print(f"Total rows: {len(rows)}")
    garbage = []
    for r in rows:
        d = dict(r)
        pf = d['profit_factor']
        flags = []
        if pf > MAX_PF_CAP:
            flags.append(f"PF={pf:.2f}>10")
        if d['total_losses'] == 0 and d['total_trades'] > 0:
            flags.append("ALL_WINS")
        if d['total_trades'] < 5:
            flags.append(f"FEW_TRADES={d['total_trades']}")
        if flags:
            garbage.append(d)
            print(f"  ⚠️ [{', '.join(flags)}] {d['strategy_name']}|{d['symbol']}|"
                  f"{d['regime']}|{d['session']} "
                  f"pf={pf:.2f} wr={d['win_rate']:.2f} trades={d['total_trades']} "
                  f"W={d['total_wins']} L={d['total_losses']} "
                  f"profit={d['total_profit']:.2f} loss={d['total_loss']:.2f}")

    if not garbage:
        print("✅ No garbage data found!")
        conn.close()
        return

    print(f"\n🔧 Found {len(garbage)} problematic rows")

    # --- Fix 1: Recalculate PF with cap ---
    fixed = 0
    for d in rows:
        d = dict(d)
        old_pf = d['profit_factor']
        total_profit = d['total_profit']
        total_loss = d['total_loss']
        total_wins = d['total_wins']

        if total_loss > 0:
            new_pf = min(MAX_PF_CAP, total_profit / total_loss)
        else:
            # No losses: PF = capped based on wins count
            new_pf = min(MAX_PF_CAP, total_wins * 1.0)

        if abs(new_pf - old_pf) > 0.01:
            conn.execute("""
                UPDATE strategy_performance
                SET profit_factor = ?
                WHERE id = ?
            """, (round(new_pf, 4), d['id']))
            fixed += 1
            print(f"  ✏️ {d['strategy_name']}|{d['symbol']}|{d['regime']} "
                  f"PF {old_pf:.2f} → {new_pf:.2f}")

    # --- Fix 2: Delete rows with < 3 trades ---
    deleted = conn.execute("""
        DELETE FROM strategy_performance
        WHERE total_trades < 3
    """).rowcount

    conn.commit()

    print(f"\n=== CLEANUP RESULTS ===")
    print(f"  PF recalculated: {fixed} rows")
    print(f"  Deleted (< 3 trades): {deleted} rows")

    # Show final state
    remaining = conn.execute("""
        SELECT COUNT(*) FROM strategy_performance
    """).fetchone()[0]
    print(f"  Remaining rows: {remaining}")

    # Show top strategies now
    print(f"\n=== TOP STRATEGIES AFTER CLEANUP ===")
    top = conn.execute("""
        SELECT strategy_name, symbol, regime, session,
               profit_factor, win_rate, total_trades, total_wins, total_losses
        FROM strategy_performance
        WHERE total_trades >= 5 AND total_losses > 0
        ORDER BY (profit_factor * win_rate) DESC
        LIMIT 10
    """).fetchall()
    for r in top:
        d = dict(r)
        print(f"  ✅ {d['strategy_name']}|{d['symbol']}|{d['regime']}|{d['session']} "
              f"pf={d['profit_factor']:.2f} wr={d['win_rate']:.2f} "
              f"trades={d['total_trades']} W={d['total_wins']} L={d['total_losses']}")

    if not top:
        print("  ⚠️ No qualified strategies! Bot will use fallback recommendations.")

    conn.close()
    print("\n✅ Cleanup done — restart bot to use cleaned data")


if __name__ == "__main__":
    main()
