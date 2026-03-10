"""Run backtest for all 3 symbols — 90 day (25920 M5 bars)"""
import sys
sys.path.insert(0, "d:/VibeCode/Trade")

from backend.trader.scripts.run_backtest import load_mt5_data, BacktestEngine

symbols = ["XAUUSD", "BTCUSD", "XAGUSD"]
results = {}

for s in symbols:
    print(f"\n{'#'*60}")
    print(f"  BACKTEST: {s}")
    print(f"{'#'*60}")
    df = load_mt5_data(s, 25920)
    engine = BacktestEngine(s, initial_equity=4000.0)
    results[s] = engine.run(df)

# Portfolio summary
print(f"\n\n{'='*60}")
print(f"  PORTFOLIO SUMMARY (All Symbols)")
print(f"{'='*60}")
for s, r in results.items():
    print(f"  {s}: WR={r['win_rate']}% PF={r['profit_factor']} DD={r['max_dd']}% "
          f"Trades={r['total_trades']} PnL=${r['net_pnl']}")

tw = sum(r['wins'] for r in results.values())
tl = sum(r['losses'] for r in results.values())
total_pnl = sum(r['net_pnl'] for r in results.values())
if tw + tl > 0:
    print(f"\n  TOTAL: WR={tw/(tw+tl)*100:.1f}% Trades={tw+tl} PnL=${total_pnl:.2f}")
    max_dd = max(r['max_dd'] for r in results.values())
    print(f"  Live Gate: {'PASS' if tw/(tw+tl)*100 >= 50 and max_dd <= 6 else 'NEEDS TUNING'}")
else:
    print("  No trades generated")
