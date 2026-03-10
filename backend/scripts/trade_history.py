"""Deep trade analysis — TP losses + Big losses."""
import MetaTrader5 as mt5
from datetime import datetime, timedelta
import pandas as pd

def main():
    mt5.initialize()
    now = datetime.now()
    deals = mt5.history_deals_get(now - timedelta(days=7), now)
    mt5.shutdown()

    if not deals:
        print("No deals")
        return

    df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())
    closed = df[df["entry"] == 1].copy()
    closed["pnl"] = closed["profit"] + closed["commission"] + closed["swap"]
    reason_map = {0: "CLIENT", 1: "EXPERT", 2: "DEALER", 3: "SL", 4: "TP", 5: "STOP_OUT"}
    closed["close_reason"] = closed["reason"].map(reason_map).fillna("OTHER")
    closed["time_str"] = pd.to_datetime(closed["time"], unit="s").dt.strftime("%m/%d %H:%M")

    # TP trades that lost money
    tp_loss = closed[(closed["close_reason"] == "TP") & (closed["pnl"] < 0)]
    print("=== TP TRADES THAT LOST MONEY ===")
    print(f"Count: {len(tp_loss)}")
    if len(tp_loss) > 0:
        print(f"Total loss: ${tp_loss['pnl'].sum():.2f}")
        for _, r in tp_loss.iterrows():
            sym = str(r["symbol"])[:10].ljust(10)
            print(f"  {r['time_str']} | {sym} | Vol:{r['volume']:.2f} | Price:{r['price']:.2f} | PnL: ${r['pnl']:+.2f}")
    print()

    # OTHER trades
    other = closed[closed["close_reason"] == "OTHER"]
    if len(other) > 0:
        print("=== OTHER CLOSE REASON TRADES ===")
        for _, r in other.iterrows():
            sym = str(r["symbol"])[:10].ljust(10)
            print(f"  {r['time_str']} | {sym} | Vol:{r['volume']:.2f} | reason_code:{r['reason']} | PnL: ${r['pnl']:+.2f}")
    print()

    # Big losses
    big_loss = closed[closed["pnl"] < -500]
    if len(big_loss) > 0:
        print("=== BIG LOSSES (> $500) ===")
        for _, r in big_loss.iterrows():
            sym = str(r["symbol"])[:10].ljust(10)
            print(f"  {r['time_str']} | {sym} | {r['close_reason']:6s} | Vol:{r['volume']:.2f} | PnL: ${r['pnl']:+.2f}")
    print()

    # P&L by day
    closed["day"] = pd.to_datetime(closed["time"], unit="s").dt.strftime("%m/%d")
    print("=== DAILY P&L ===")
    daily = closed.groupby("day").agg(
        trades=("pnl", "count"),
        total_pnl=("pnl", "sum"),
        wins=("pnl", lambda x: (x > 0).sum()),
    )
    for day, row in daily.iterrows():
        wr = row["wins"] / row["trades"] * 100 if row["trades"] > 0 else 0
        print(f"  {day}: {int(row['trades']):3d} trades | WR: {wr:.0f}% | P&L: ${row['total_pnl']:+.2f}")

    # P&L by symbol
    print()
    print("=== P&L BY SYMBOL ===")
    by_sym = closed.groupby("symbol").agg(
        trades=("pnl", "count"),
        total_pnl=("pnl", "sum"),
        wins=("pnl", lambda x: (x > 0).sum()),
        avg_lot=("volume", "mean"),
    )
    for sym, row in by_sym.iterrows():
        wr = row["wins"] / row["trades"] * 100 if row["trades"] > 0 else 0
        print(f"  {sym:12s}: {int(row['trades']):3d} trades | WR: {wr:.0f}% | P&L: ${row['total_pnl']:+.2f} | AvgLot: {row['avg_lot']:.2f}")

if __name__ == "__main__":
    main()
