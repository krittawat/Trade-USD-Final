"""
Pullback V2 Strategy 365-day Optimizer for XAU and XAG.
Goal: Find the most efficient settings over a 1-year period targeting WR>60%.
"""

import argparse
import csv
import json
import logging
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.memory_store import MemoryStore
from scripts.backtest_pullback_v2 import run_backtest, PARAM_GRID, SYMBOL_CONFIGS, calc_ema, calc_rsi, calc_atr, calc_adx, calc_macd

logging.getLogger().handlers = []
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("OptimizePullbackV2365D")

TIMEFRAME = mt5.TIMEFRAME_M5
DEFAULT_DAYS = 360
DEFAULT_PORTFOLIOS = [3000.0, 10000.0]

# Expanded grid based on V2 configurations to find the optimal balance
EXPANDED_PARAM_GRID = PARAM_GRID + [
    # Adjusted SELL-biased (Slightly looser RSI for more trades)
    {"label": "V2_SELL_BIAS_MORE_TRADES", "ema_fast": 21, "ema_slow": 50, "rr": 2.0, "sl_atr": 1.5,
     "pb_pct": 0.3, "rsi_buy_max": 45, "rsi_sell_min": 45, "adx_min": 10, "need_2candle": False, "need_macd": True, "session_filter": False},
    # Highly filtered (Strict ADX and 2-candle to max WR)
    {"label": "V2_STRICT_ADX_BODY", "ema_fast": 21, "ema_slow": 50, "rr": 1.5, "sl_atr": 1.5,
     "pb_pct": 0.3, "rsi_buy_max": 40, "rsi_sell_min": 55, "adx_min": 15, "need_2candle": True, "need_macd": True, "session_filter": False},
    # RR 1.5 with looser RSI
    {"label": "V2_RR1.5_LOOSE", "ema_fast": 21, "ema_slow": 50, "rr": 1.5, "sl_atr": 1.5,
     "pb_pct": 0.35, "rsi_buy_max": 50, "rsi_sell_min": 50, "adx_min": 12, "need_2candle": False, "need_macd": True, "session_filter": False},
    # RR 1.2 for highest WR chance
    {"label": "V2_SCALPER_RR1.2", "ema_fast": 21, "ema_slow": 50, "rr": 1.2, "sl_atr": 1.5,
     "pb_pct": 0.3, "rsi_buy_max": 48, "rsi_sell_min": 52, "adx_min": 12, "need_2candle": False, "need_macd": True, "session_filter": False},
]

def parse_args():
    parser = argparse.ArgumentParser(description="Pullback V2 365-day optimizer")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Number of days to backtest")
    parser.add_argument("--symbol", type=str, default="XAUUSDc", help="Target Symbol e.g. XAUUSDc")
    parser.add_argument("--min-pf", type=float, default=1.3, help="Minimum profit factor threshold")
    parser.add_argument("--portfolios", type=str, default=",".join(str(int(v)) for v in DEFAULT_PORTFOLIOS))
    parser.add_argument("--csv", type=str, default="backend/data/optimization_pullback_v2.csv")
    return parser.parse_args()

def parse_portfolios(raw: str):
    values = []
    for token in raw.split(","):
        token = token.strip()
        if not token: continue
        try: val = float(token)
        except ValueError: continue
        if val > 0: values.append(val)
    return sorted(set(values))

def calculate_daily_consistency(trades_df):
    if not trades_df:
        return 0.0, 0.0

    df = pd.DataFrame(trades_df)
    if df.empty or "exit" not in df.columns or "pnl" not in df.columns:
        return 0.0, 0.0

    # pullbacks uses 'exit' but they aren't timestamped in the dictionary, only prices
    # We estimate based on ID/order if no timestamp. For true daily we need time.
    # The current run_backtest in pullback_v2 doesn't output exit_time, only exit_price in 'exit'
    return 0.0, 0.0 # Skipping accurate daily consistency because backtester limits.

def score_run(result):
    if result["net"] <= 0:
        return -1e9 + result["net"]
    return (
        (result["net"] * 0.5)
        + ((result["pf"] - 1.0) * 500.0) 
        + (result["wr"] * 5.0)
        - (result["dd"] * 15.0) 
    )

def write_csv(path, rows):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(
            file_obj,
            fieldnames=[
                "portfolio", "label", "win_rate", "profit_factor", "pnl",
                "roi_pct", "max_dd_pct", "trades", "score", "params"
            ]
        )
        writer.writeheader()
        for row in rows:
            formatted_row = {k: v for k, v in row.items() if k != 'result'}
            formatted_row["params"] = json.dumps(row["params"], separators=(",", ":"))
            writer.writerow(formatted_row)

def run_optimization(days, symbol, portfolios, csv_path, min_pf):
    config = SYMBOL_CONFIGS.get(symbol)
    if not config:
        logger.error(f"Unknown symbol: {symbol}")
        return 1

    df = None
    pq_path = Path(f"backend/data/exports/mtf/{symbol}_M5.parquet")
    if pq_path.exists():
        print(f"📦 Loading from DuckDB Parquet: {pq_path}...", end=" ", flush=True)
        try:
            import duckdb
            conn = duckdb.connect(":memory:")
            safe = str(pq_path).replace("\\", "/")
            df = conn.execute(f"SELECT * FROM read_parquet('{safe}') ORDER BY time ASC").df()
            conn.close()
            if "time" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["time"]):
                df["time"] = pd.to_datetime(df["time"], unit="s", errors="coerce", utc=True)
            elif "time" in df.columns and df["time"].dt.tz is None:
                df["time"] = df["time"].dt.tz_localize("UTC")
            elif "time" in df.columns:
                df["time"] = df["time"].dt.tz_convert("UTC")

            if days and len(df) > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                df = df[df["time"] >= cutoff].reset_index(drop=True)
            print(f"✅ Loaded {len(df)} bars")
        except Exception as e:
            print(f"⚠️ Parquet load error: {e}")
            df = None

    if df is None or len(df) == 0:
        print(f"📡 Loading from MT5 ({days} days)...", end=" ", flush=True)
        if not mt5.initialize():
            logger.error("mt5_initialize_failed")
            return 1
        utc_to = datetime.now(timezone.utc)
        utc_from = utc_to - timedelta(days=days)
        logger.info("fetch_data M5 symbol=%s days=%s", symbol, days)
        rates = mt5.copy_rates_range(symbol, TIMEFRAME, utc_from, utc_to)
        if rates is None or len(rates) == 0:
            logger.error(f"no_data symbol={symbol}")
            mt5.shutdown()
            return 1
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        print(f"✅ Loaded {len(df)} bars")

    print("=" * 110)
    print(f"PULLBACK V2 OPTIMIZATION START | Symbol={symbol} | Days={days} | Bars={len(df)}")
    print(f"Portfolios={portfolios} | Configs={len(EXPANDED_PARAM_GRID)} | MinPF={min_pf:.2f}")
    print("=" * 110)

    all_rows = []
    best_by_port = {}
    best_by_port_qualified = {}
    start = time.monotonic()

    total_runs = len(portfolios) * len(EXPANDED_PARAM_GRID)
    run_idx = 0

    for portfolio in portfolios:
        for params in EXPANDED_PARAM_GRID:
            run_idx += 1
            label = params.get("label", f"SET_{run_idx}")
            print(f"[{run_idx:02d}/{total_runs}] Portfolio=${portfolio:,.0f} | {label} ...", end=" ", flush=True)

            random.seed(42)
            np.random.seed(42)

            t0 = time.monotonic()
            try:
                result = run_backtest(df, symbol, config, params, equity_start=portfolio)
            except Exception as exc:
                print(f"FAILED ({exc})")
                continue

            roi_pct = (result["net"] / portfolio * 100.0) if portfolio > 0 else 0.0
            score = score_run(result)

            row = {
                "portfolio": portfolio,
                "label": label,
                "params": params,
                "win_rate": result["wr"],
                "profit_factor": result["pf"],
                "pnl": result["net"],
                "roi_pct": round(roi_pct, 2),
                "max_dd_pct": result["dd"],
                "trades": result["n"],
                "score": round(score, 4),
                "result": result,
            }
            all_rows.append(row)

            current_best = best_by_port.get(portfolio)
            if current_best is None or row["score"] > current_best["score"]:
                best_by_port[portfolio] = row

            if row["profit_factor"] >= min_pf and row["pnl"] > 0 and row["win_rate"] >= 60.0:
                qualified_best = best_by_port_qualified.get(portfolio)
                if qualified_best is None or row["score"] > qualified_best["score"]:
                    best_by_port_qualified[portfolio] = row

            el = time.monotonic() - t0
            print(f"WR={result['wr']:.1f}% PF={result['pf']:.2f} PnL=${result['net']:+.2f} ROI={roi_pct:+.2f}% DD={result['dd']:.1f}% Trades={result['n']} ({el:.1f}s)")

    mt5.shutdown()

    if not all_rows:
        logger.error("no_results")
        return 1

    elapsed = time.monotonic() - start
    write_csv(csv_path.replace('.csv', f'_{symbol}.csv'), all_rows)
    qualified_rows = [row for row in all_rows if row["profit_factor"] >= min_pf and row["pnl"] > 0 and row["win_rate"] >= 60.0]

    print("\n" + "=" * 110)
    print(f"BEST CONFIG BY PORTFOLIO (PF >= {min_pf:.2f}, WR >= 60%)")
    print("=" * 110)
    for portfolio in sorted(best_by_port):
        best = best_by_port_qualified.get(portfolio)
        if best:
            print(
                f"Portfolio ${portfolio:>8,.0f} | {best['label']:<20s} | "
                f"PnL ${best['pnl']:>+9.2f} | ROI {best['roi_pct']:>+6.2f}% | "
                f"WR {best['win_rate']:>5.1f}% | PF {best['profit_factor']:>4.2f} | DD {best['max_dd_pct']:>4.1f}%"
            )
            continue
        fallback = best_by_port.get(portfolio)
        if fallback:
            print(f"Portfolio ${portfolio:>8,.0f} | NO CONFIG MET CRITERIA (fallback {fallback['label']} PF {fallback['profit_factor']:.2f} WR {fallback['win_rate']:.1f}%)")

    winner_pool = qualified_rows if qualified_rows else all_rows
    if winner_pool:
        best_abs_pnl = max(winner_pool, key=lambda row: row["pnl"])
        best_roi = max(winner_pool, key=lambda row: row["roi_pct"])
        robust = max(winner_pool, key=lambda row: row["score"])

        print("\n" + "=" * 110)
        print("GLOBAL WINNERS")
        print("=" * 110)
        if not qualified_rows:
            print(f"WARNING: No configuration met requirements. Showing best available fallback.")
        
        print(f"Highest Absolute Profit : Portfolio ${best_abs_pnl['portfolio']:,.0f} | {best_abs_pnl['label']} | PnL ${best_abs_pnl['pnl']:+.2f} | ROI {best_abs_pnl['roi_pct']:+.2f}% | Trades {best_abs_pnl['trades']}")
        print(f"Highest ROI             : Portfolio ${best_roi['portfolio']:,.0f} | {best_roi['label']} | PnL ${best_roi['pnl']:+.2f} | ROI {best_roi['roi_pct']:+.2f}% | Trades {best_roi['trades']}")
        print(f"Best Balanced Score     : Portfolio ${robust['portfolio']:,.0f} | {robust['label']} | PnL ${robust['pnl']:+.2f} | WR {robust['win_rate']:.1f}% | PF {robust['profit_factor']:.2f} | DD {robust['max_dd_pct']:.1f}%")
        print("-" * 110)
        print("Recommended Parameters:")
        print(json.dumps(robust["params"], indent=2))
        
        # Save to brain
        if robust["profit_factor"] >= min_pf and robust["win_rate"] >= 60.0:
            try:
                store = MemoryStore()
                store.connect()
                store.save_evolved_params(
                    strategy_name="pullback_v2",
                    symbol=symbol,
                    regime="ALL",
                    params=robust["params"],
                    score=float(robust["score"]),
                )
                logger.info(f"saved_to_brain symbol={symbol} label={robust['label']}")
            except Exception as exc:
                logger.error(f"save_to_brain_failed: {exc}")

    print(f"CSV saved to: {csv_path.replace('.csv', f'_{symbol}.csv')}")
    print(f"Elapsed: {elapsed:.1f}s")

    return 0 if qualified_rows else 2

if __name__ == "__main__":
    args = parse_args()
    portfolios = parse_portfolios(args.portfolios)
    if not portfolios:
        portfolios = DEFAULT_PORTFOLIOS

    raise SystemExit(
        run_optimization(
            days=args.days,
            symbol=args.symbol.strip(),
            portfolios=portfolios,
            csv_path=args.csv,
            min_pf=max(0.0, float(args.min_pf))
        )
    )
