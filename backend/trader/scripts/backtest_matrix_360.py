"""
Backtest Matrix (360D x All TFs)
--------------------------------
Runs OPUS backtest across all configured symbols/timeframes and ranks best settings
for LIVE readiness.

Usage:
  uv run python -m backend.trader.scripts.backtest_matrix_360
  uv run python -m backend.trader.scripts.backtest_matrix_360 --days 360 --equity 300 --max-bars-per-run 12000
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, UTC
from pathlib import Path

from backend.trader.data.mapper import mapper
from backend.trader.scripts.run_backtest import BacktestEngine, load_mt5_data


# Reduce very noisy strategy logs during matrix runs.
logging.disable(logging.INFO)


DEFAULT_BROKER_SYMBOLS = [
    "XAUUSDm", "BTCUSDm", "USOILm", "US30m", "USTECm", "EURUSDm", "GBPUSDm", "USDJPYm", "XAGUSDm"
]
DEFAULT_TIMEFRAMES = ["M3", "M5", "M6", "M12", "M15", "M30", "H1", "H2", "H4", "D1"]


def _to_standard_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip()
    if not raw:
        return ""
    std = mapper.to_standard(raw)
    if std == raw and raw.endswith(("m", "c", "M", "C")):
        std = raw[:-1]
    return std.upper()


def _score(metrics: dict, min_trades: int) -> float:
    trades = int(metrics.get("total_trades", 0) or 0)
    wr = float(metrics.get("win_rate", 0.0) or 0.0)
    pf = float(metrics.get("profit_factor", 0.0) or 0.0)
    dd = float(metrics.get("max_dd", 0.0) or 0.0)
    pnl = float(metrics.get("net_pnl", 0.0) or 0.0)

    # Composite score with overfit guard (few-trade penalty).
    score = (pnl * 1.0) + (wr * 0.60) + (min(pf, 6.0) * 8.0) - (dd * 0.70) + (min(trades, 50) * 0.15)
    if trades < min_trades:
        score -= 30.0
    return round(score, 4)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 360D backtest matrix on all symbols/timeframes")
    parser.add_argument("--days", type=int, default=360, help="Backtest window in days")
    parser.add_argument("--equity", type=float, default=300.0, help="Initial equity")
    parser.add_argument("--lookback", type=int, default=100, help="Engine lookback bars")
    parser.add_argument("--hold-bars", type=int, default=50, help="Engine hold bars")
    parser.add_argument("--min-trades", type=int, default=5, help="Minimum trades for reliable ranking")
    parser.add_argument(
        "--max-bars-per-run",
        type=int,
        default=12000,
        help="Safety cap per run (0 = no cap). Keep >0 to avoid extreme runtime on M3 over 360D.",
    )
    args = parser.parse_args()

    seen = set()
    symbols = []
    for broker_sym in DEFAULT_BROKER_SYMBOLS:
        std = _to_standard_symbol(broker_sym)
        if not std or std in seen:
            continue
        symbols.append(std)
        seen.add(std)

    rows = []
    total_jobs = len(symbols) * len(DEFAULT_TIMEFRAMES)
    job_idx = 0

    print("=" * 84)
    print(f"Backtest Matrix Start | days={args.days} | symbols={len(symbols)} | tfs={len(DEFAULT_TIMEFRAMES)}")
    print(f"Total jobs: {total_jobs} | equity={args.equity} | lookback={args.lookback} | hold={args.hold_bars}")
    print("=" * 84)

    for symbol in symbols:
        for tf in DEFAULT_TIMEFRAMES:
            job_idx += 1
            print(f"\n[{job_idx}/{total_jobs}] {symbol} {tf}")
            try:
                df = load_mt5_data(symbol=symbol, bars=0, timeframe=tf, days=args.days)
                raw_bars = len(df)
                truncated = False
                if args.max_bars_per_run and args.max_bars_per_run > 0 and raw_bars > args.max_bars_per_run:
                    df = df.tail(args.max_bars_per_run).copy()
                    truncated = True
                    print(f"  [CAP] bars {raw_bars} -> {len(df)} (max-bars-per-run)")

                if len(df) < (args.lookback + args.hold_bars + 20):
                    print(f"  [SKIP] insufficient bars: {len(df)}")
                    continue

                engine = BacktestEngine(symbol=symbol, initial_equity=args.equity)
                metrics = engine.run(df, lookback=args.lookback, hold_bars=args.hold_bars)
                metrics["symbol"] = symbol
                metrics["timeframe"] = tf
                metrics["bars_used"] = len(df)
                metrics["bars_raw"] = raw_bars
                metrics["truncated"] = truncated
                metrics["score"] = _score(metrics, args.min_trades)
                rows.append(metrics)
            except Exception as e:
                print(f"  [ERROR] {symbol} {tf}: {e}")

    if not rows:
        print("\nNo backtest rows generated.")
        return 1

    rows.sort(key=lambda x: x.get("score", -999999), reverse=True)

    best_by_symbol = {}
    for symbol in symbols:
        candidates = [r for r in rows if r["symbol"] == symbol]
        reliable = [r for r in candidates if int(r.get("total_trades", 0) or 0) >= args.min_trades]
        best = reliable[0] if reliable else (candidates[0] if candidates else None)
        if best:
            best_by_symbol[symbol] = best

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "days": args.days,
        "equity": args.equity,
        "lookback": args.lookback,
        "hold_bars": args.hold_bars,
        "min_trades": args.min_trades,
        "max_bars_per_run": args.max_bars_per_run,
        "timeframes": DEFAULT_TIMEFRAMES,
        "symbols": symbols,
        "best_by_symbol": best_by_symbol,
        "rows": rows,
    }

    out_dir = Path("d:/VibeCode/Trade/tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"backtest_matrix_360_{stamp}.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n" + "=" * 84)
    print("BEST BY SYMBOL (LIVE CANDIDATES)")
    print("=" * 84)
    for sym, b in best_by_symbol.items():
        print(
            f"{sym:>7} | TF={b.get('timeframe'):>3} | trades={b.get('total_trades',0):>4} | "
            f"WR={b.get('win_rate',0):>5}% | PF={b.get('profit_factor',0):>5} | "
            f"PnL={b.get('net_pnl',0):>9} | DD={b.get('max_dd',0):>5}% | score={b.get('score',0):>8}"
        )

    print("\nTOP 15 OVERALL")
    for r in rows[:15]:
        print(
            f"{r['symbol']:>7} {r['timeframe']:>3} | trades={r.get('total_trades',0):>4} | "
            f"WR={r.get('win_rate',0):>5}% | PF={r.get('profit_factor',0):>5} | "
            f"PnL={r.get('net_pnl',0):>9} | DD={r.get('max_dd',0):>5}% | score={r.get('score',0):>8}"
        )

    print(f"\nSaved report: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

