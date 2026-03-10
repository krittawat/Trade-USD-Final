"""Dump trade-level details to file for analysis"""
import sys
sys.path.insert(0, "d:/VibeCode/Trade")

from backend.trader.scripts.run_backtest import load_mt5_data, BacktestEngine

symbols = ["XAUUSD", "BTCUSD", "XAGUSD"]
all_trades = []
out = []

for s in symbols:
    df = load_mt5_data(s, 25920)
    engine = BacktestEngine(s, initial_equity=4000.0)
    engine.run(df)
    for t in engine.trades:
        t['symbol'] = s
    all_trades.extend(engine.trades)

# Per-model stats
out.append("\n=== PER MODEL STATS ===")
models = set(t['model'] for t in all_trades)
for model in sorted(models):
    mt = [t for t in all_trades if t['model'] == model]
    wins = [t for t in mt if t['pnl_usd'] > 0]
    losses = [t for t in mt if t['pnl_usd'] <= 0]
    net_pnl = sum(t['pnl_usd'] for t in mt)
    gp = sum(t['pnl_usd'] for t in wins) if wins else 0
    gl = abs(sum(t['pnl_usd'] for t in losses)) if losses else 1e-9
    wr = len(wins) / len(mt) * 100 if mt else 0
    avg_win = gp / len(wins) if wins else 0
    avg_loss = gl / len(losses) if losses else 0
    sl_count = len([t for t in mt if t['result'] == 'SL'])
    tp_count = len([t for t in mt if t['result'] == 'TP1'])
    to_count = len([t for t in mt if t['result'] == 'TIMEOUT'])
    
    out.append(f"\n[{model}]")
    out.append(f"  Trades={len(mt)} Win={len(wins)} Loss={len(losses)} WR={wr:.1f}%")
    out.append(f"  PF={gp/gl:.2f} Net=${net_pnl:.4f}")
    out.append(f"  AvgWin=${avg_win:.4f} AvgLoss=${avg_loss:.4f}")
    out.append(f"  Outcomes: TP1={tp_count} SL={sl_count} TIMEOUT={to_count}")

out.append("\n\n=== ALL TRADES ===")
for i, t in enumerate(all_trades):
    e = "W" if t['pnl_usd'] > 0 else "L"
    r_dist = abs(t['entry'] - t['sl'])
    t_dist = abs(t['tp1'] - t['entry'])
    rr = t_dist / r_dist if r_dist > 0 else 0
    out.append(f"  {i+1}. [{e}] {t['symbol']} {t['side']} {t['model']} "
          f"Conf={t['confidence']:.2f} "
          f"Entry={t['entry']:.2f} SL={t['sl']:.2f} TP1={t['tp1']:.2f} "
          f"Exit={t['exit']:.2f} R:R={rr:.1f} "
          f"Result={t['result']} PnL=${t['pnl_usd']:.4f}")

result = "\n".join(out)
with open("d:/VibeCode/Trade/backtest_results.txt", "w") as f:
    f.write(result)
print(result)
print("\nSaved to d:/VibeCode/Trade/backtest_results.txt")
