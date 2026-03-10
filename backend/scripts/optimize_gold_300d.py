"""
XAUUSD 300-day optimization with portfolio-aware ranking.

Goal:
- Find the highest-profit trading configuration for gold over 300 days
- Report winners by portfolio size (money in account)
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
from app.strategy.templates.gold_scalp_pro import GoldScalpProStrategy
from scripts.backtest_full import FullFeatureBacktester


logging.getLogger().handlers = []
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("OptimizeGold300D")


TIMEFRAME = mt5.TIMEFRAME_M5
CONTRACT_SIZE = 100.0
POINT_VALUE = 0.01
DEFAULT_DAYS = 300
DEFAULT_PORTFOLIOS = [1000.0, 3000.0, 10000.0, 30000.0]
GOLD_SYMBOL_CANDIDATES = ["XAUUSDc", "XAUUSDc", "XAUUSD", "GOLD"]


PARAM_GRID = [
    {
        "label": "VOL_LOOSE_08",
        "SL_ATR_MULT": 2.5,
        "TP_ATR_MULT": 0.9,
        "MIN_SL_DISTANCE": 2.0,
        "SUPERTREND_LEN": 14,
        "SUPERTREND_MUL": 3.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 2.0,
        "PROFIT_LOCK_1": 0.4,
        "PROFIT_LOCK_2": 0.7,
        "use_volume_filter": True,
        "min_rvol": 0.8,
    },
    {
        "label": "VOL_STD_10",
        "SL_ATR_MULT": 2.5,
        "TP_ATR_MULT": 0.9,
        "MIN_SL_DISTANCE": 2.0,
        "SUPERTREND_LEN": 14,
        "SUPERTREND_MUL": 3.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 2.0,
        "PROFIT_LOCK_1": 0.4,
        "PROFIT_LOCK_2": 0.7,
        "use_volume_filter": True,
        "min_rvol": 1.0,
    },
    {
        "label": "VOL_STRICT_13",
        "SL_ATR_MULT": 2.5,
        "TP_ATR_MULT": 0.9,
        "MIN_SL_DISTANCE": 2.0,
        "SUPERTREND_LEN": 14,
        "SUPERTREND_MUL": 3.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 2.0,
        "PROFIT_LOCK_1": 0.4,
        "PROFIT_LOCK_2": 0.7,
        "use_volume_filter": True,
        "min_rvol": 1.3,
    },
    {
        "label": "WIDE_SL_TIGHT_TP",
        "SL_ATR_MULT": 2.0,
        "TP_ATR_MULT": 1.0,
        "MIN_SL_DISTANCE": 3.0,
        "SUPERTREND_LEN": 7,
        "SUPERTREND_MUL": 2.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.8,
        "PROFIT_LOCK_1": 0.8,
        "PROFIT_LOCK_2": 1.5,
        "use_volume_filter": True,
        "min_rvol": 1.0,
    },
    {
        "label": "WIDE_SL_TP_0.9",
        "SL_ATR_MULT": 2.0,
        "TP_ATR_MULT": 0.9,
        "MIN_SL_DISTANCE": 3.0,
        "SUPERTREND_LEN": 7,
        "SUPERTREND_MUL": 2.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.8,
        "PROFIT_LOCK_1": 0.8,
        "PROFIT_LOCK_2": 1.5,
        "use_volume_filter": True,
        "min_rvol": 1.0,
    },
    {
        "label": "WIDE_SL_TP_1.1",
        "SL_ATR_MULT": 2.0,
        "TP_ATR_MULT": 1.1,
        "MIN_SL_DISTANCE": 3.0,
        "SUPERTREND_LEN": 7,
        "SUPERTREND_MUL": 2.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.8,
        "PROFIT_LOCK_1": 0.8,
        "PROFIT_LOCK_2": 1.5,
        "use_volume_filter": True,
        "min_rvol": 1.0,
    },
    {
        "label": "WIDE_SL_TP_1.15",
        "SL_ATR_MULT": 2.0,
        "TP_ATR_MULT": 1.15,
        "MIN_SL_DISTANCE": 3.0,
        "SUPERTREND_LEN": 7,
        "SUPERTREND_MUL": 2.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.8,
        "PROFIT_LOCK_1": 0.8,
        "PROFIT_LOCK_2": 1.5,
        "use_volume_filter": True,
        "min_rvol": 1.0,
    },
    {
        "label": "WIDE_SL_1.8_TP_1.0",
        "SL_ATR_MULT": 1.8,
        "TP_ATR_MULT": 1.0,
        "MIN_SL_DISTANCE": 3.0,
        "SUPERTREND_LEN": 7,
        "SUPERTREND_MUL": 2.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.8,
        "PROFIT_LOCK_1": 0.8,
        "PROFIT_LOCK_2": 1.5,
        "use_volume_filter": True,
        "min_rvol": 1.0,
    },
    {
        "label": "WIDE_SL_2.2_TP_1.0",
        "SL_ATR_MULT": 2.2,
        "TP_ATR_MULT": 1.0,
        "MIN_SL_DISTANCE": 3.0,
        "SUPERTREND_LEN": 7,
        "SUPERTREND_MUL": 2.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.8,
        "PROFIT_LOCK_1": 0.8,
        "PROFIT_LOCK_2": 1.5,
        "use_volume_filter": True,
        "min_rvol": 1.0,
    },
    {
        "label": "WIDE_SL_2.5_TP_1.0",
        "SL_ATR_MULT": 2.5,
        "TP_ATR_MULT": 1.0,
        "MIN_SL_DISTANCE": 3.5,
        "SUPERTREND_LEN": 7,
        "SUPERTREND_MUL": 2.0,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 2.0,
        "PROFIT_LOCK_1": 0.8,
        "PROFIT_LOCK_2": 1.6,
        "use_volume_filter": True,
        "min_rvol": 1.0,
    },
    {
        "label": "WIDE_SL_ST10_TP_1.1",
        "SL_ATR_MULT": 2.0,
        "TP_ATR_MULT": 1.1,
        "MIN_SL_DISTANCE": 3.0,
        "SUPERTREND_LEN": 10,
        "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.8,
        "PROFIT_LOCK_1": 0.8,
        "PROFIT_LOCK_2": 1.5,
        "use_volume_filter": True,
        "min_rvol": 1.0,
    },
    {
        "label": "BALANCED_FAST",
        "SL_ATR_MULT": 1.8,
        "TP_ATR_MULT": 1.2,
        "MIN_SL_DISTANCE": 2.5,
        "SUPERTREND_LEN": 10,
        "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 12,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 1.8,
        "PROFIT_LOCK_1": 0.7,
        "PROFIT_LOCK_2": 1.3,
        "use_volume_filter": True,
        "min_rvol": 0.9,
    },
    {
        "label": "NO_VOL_FILTER",
        "SL_ATR_MULT": 2.0,
        "TP_ATR_MULT": 1.0,
        "MIN_SL_DISTANCE": 2.5,
        "SUPERTREND_LEN": 10,
        "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 14,
        "ADX_PERIOD": 14,
        "ATR_PERIOD": 14,
        "CHANDELIER_MULT": 2.0,
        "PROFIT_LOCK_1": 0.8,
        "PROFIT_LOCK_2": 1.6,
        "use_volume_filter": False,
        "min_rvol": 0.0,
    },
]


def parse_args():
    parser = argparse.ArgumentParser(description="Gold 300-day optimizer (portfolio aware)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Number of days to backtest")
    parser.add_argument("--symbol", type=str, default="", help="Gold symbol override, e.g. XAUUSDc")
    parser.add_argument(
        "--min-pf",
        type=float,
        default=1.2,
        help="Minimum profit factor threshold for qualifying configs",
    )
    parser.add_argument(
        "--portfolios",
        type=str,
        default=",".join(str(int(v)) for v in DEFAULT_PORTFOLIOS),
        help="Comma-separated portfolio sizes, e.g. 1000,3000,10000",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default="backend/data/optimization_gold_300d_portfolio.csv",
        help="Output CSV path",
    )
    return parser.parse_args()


def parse_portfolios(raw: str):
    values = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            val = float(token)
        except ValueError:
            continue
        if val > 0:
            values.append(val)
    return sorted(set(values))


def apply_params(strategy, params):
    for key, value in params.items():
        if key == "label":
            continue
        if hasattr(strategy, key):
            setattr(strategy, key, value)


def calculate_daily_consistency(trades_df):
    if trades_df.empty or "exit_time" not in trades_df.columns:
        return 0.0, 0.0

    if not pd.api.types.is_datetime64_any_dtype(trades_df["exit_time"]):
        trades_df["exit_time"] = pd.to_datetime(trades_df["exit_time"], errors="coerce")

    pnl_col = "pnl" if "pnl" in trades_df.columns else "profit_usd"
    if pnl_col not in trades_df.columns:
        return 0.0, 0.0

    clean = trades_df.dropna(subset=["exit_time"])
    if clean.empty:
        return 0.0, 0.0

    daily_pnl = clean.groupby(clean["exit_time"].dt.date)[pnl_col].sum()
    if daily_pnl.empty:
        return 0.0, 0.0

    total_days = len(daily_pnl)
    winning_days = int((daily_pnl > 0).sum())
    daily_win_rate = (winning_days / total_days * 100.0) if total_days else 0.0

    avg_daily = float(daily_pnl.mean())
    std_daily = float(daily_pnl.std())
    consistency = (avg_daily / std_daily) if std_daily > 0 else 0.0

    return daily_win_rate, consistency


def detect_gold_symbol(user_symbol, days):
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=max(7, min(days, 30)))

    candidates = []
    if user_symbol:
        candidates.append(user_symbol)
    candidates.extend(GOLD_SYMBOL_CANDIDATES)

    symbols = mt5.symbols_get()
    if symbols:
        existing_upper = {c.upper() for c in candidates}
        for item in symbols:
            name = item.name
            if "XAU" in name.upper() and name.upper() not in existing_upper:
                candidates.append(name)
                existing_upper.add(name.upper())

    for symbol in candidates:
        try:
            mt5.symbol_select(symbol, True)
            rates = mt5.copy_rates_range(symbol, TIMEFRAME, start_date, end_date)
            if rates is not None and len(rates) > 200:
                return symbol
        except Exception:
            continue
    return None


def fetch_data(symbol, days):
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    logger.info("fetch_data symbol=%s days=%s", symbol, days)
    rates = mt5.copy_rates_range(symbol, TIMEFRAME, start_date, end_date)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def score_run(result, daily_wr):
    if result.total_profit_usd <= 0:
        return -1e9 + result.total_profit_usd
    return (
        (result.total_profit_usd * 1.0)
        + ((result.profit_factor - 1.0) * 200.0)
        + (result.win_rate * 2.0)
        + (daily_wr * 0.8)
        - (result.max_drawdown_pct * 5.0)
    )


def save_to_brain(symbol, best_row):
    try:
        store = MemoryStore()
        store.connect()
        store.save_evolved_params(
            strategy_name="gold_scalp_pro",
            symbol=symbol,
            regime="ALL",
            params=best_row["params"],
            score=float(best_row["score"]),
        )
        logger.info("saved_to_brain symbol=%s label=%s", symbol, best_row["label"])
    except Exception as exc:
        logger.error("save_to_brain_failed: %s", exc)


def write_csv(path, rows):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(
            file_obj,
            fieldnames=[
                "portfolio",
                "label",
                "win_rate",
                "profit_factor",
                "pnl",
                "roi_pct",
                "max_dd_pct",
                "daily_wr",
                "consistency",
                "trades",
                "score",
                "params",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "portfolio": row["portfolio"],
                    "label": row["label"],
                    "win_rate": row["win_rate"],
                    "profit_factor": row["profit_factor"],
                    "pnl": row["pnl"],
                    "roi_pct": row["roi_pct"],
                    "max_dd_pct": row["max_dd_pct"],
                    "daily_wr": row["daily_wr"],
                    "consistency": row["consistency"],
                    "trades": row["trades"],
                    "score": row["score"],
                    "params": json.dumps(row["params"], separators=(",", ":")),
                }
            )


def run_optimization(days, user_symbol, portfolios, csv_path, min_pf):
    if not mt5.initialize():
        logger.error("mt5_initialize_failed")
        return 1

    symbol = detect_gold_symbol(user_symbol=user_symbol, days=days)
    if not symbol:
        logger.error("no_working_gold_symbol_found candidates=%s", GOLD_SYMBOL_CANDIDATES)
        mt5.shutdown()
        return 1

    data = fetch_data(symbol, days)
    if data is None:
        logger.error("no_data symbol=%s", symbol)
        mt5.shutdown()
        return 1

    print("=" * 110)
    print(f"GOLD OPTIMIZATION START | Symbol={symbol} | Days={days} | Bars={len(data)}")
    print(f"Portfolios={portfolios} | Configs={len(PARAM_GRID)} | MinPF={min_pf:.2f}")
    print("=" * 110)

    all_rows = []
    best_by_port = {}
    best_by_port_qualified = {}
    start = time.monotonic()

    total_runs = len(portfolios) * len(PARAM_GRID)
    run_idx = 0

    for portfolio in portfolios:
        for params in PARAM_GRID:
            run_idx += 1
            label = params.get("label", f"SET_{run_idx}")
            print(
                f"[{run_idx:02d}/{total_runs}] Portfolio=${portfolio:,.0f} | {label} ...",
                end=" ",
                flush=True,
            )

            random.seed(42)
            np.random.seed(42)

            strategy = GoldScalpProStrategy()
            apply_params(strategy, params)
            bt = FullFeatureBacktester(strategy, initial_equity=portfolio)

            try:
                result = bt.run(
                    data,
                    symbol,
                    contract_size=CONTRACT_SIZE,
                    point=POINT_VALUE,
                    digits=2,
                )
            except Exception as exc:
                print(f"FAILED ({exc})")
                continue

            if result.trades and isinstance(result.trades[0], dict):
                trades_df = pd.DataFrame(result.trades)
            else:
                trades_df = pd.DataFrame()

            daily_wr, consistency = calculate_daily_consistency(trades_df)
            roi_pct = (result.total_profit_usd / portfolio * 100.0) if portfolio > 0 else 0.0
            score = score_run(result, daily_wr)

            row = {
                "portfolio": portfolio,
                "label": label,
                "params": params,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "pnl": result.total_profit_usd,
                "roi_pct": round(roi_pct, 2),
                "max_dd_pct": result.max_drawdown_pct,
                "daily_wr": round(daily_wr, 2),
                "consistency": round(consistency, 4),
                "trades": result.total_trades,
                "score": round(score, 4),
                "result": result,
            }
            all_rows.append(row)

            current_best = best_by_port.get(portfolio)
            if current_best is None or row["score"] > current_best["score"]:
                best_by_port[portfolio] = row

            if row["profit_factor"] >= min_pf and row["pnl"] > 0:
                qualified_best = best_by_port_qualified.get(portfolio)
                if qualified_best is None or row["score"] > qualified_best["score"]:
                    best_by_port_qualified[portfolio] = row

            print(
                f"WR={result.win_rate:.1f}% PF={result.profit_factor:.2f} "
                f"PnL=${result.total_profit_usd:+.2f} ROI={roi_pct:+.2f}% DD={result.max_drawdown_pct:.1f}%"
            )

    mt5.shutdown()

    if not all_rows:
        logger.error("no_results")
        return 1

    elapsed = time.monotonic() - start

    write_csv(csv_path, all_rows)
    qualified_rows = [row for row in all_rows if row["profit_factor"] >= min_pf and row["pnl"] > 0]

    print("\n" + "=" * 110)
    print(f"BEST CONFIG BY PORTFOLIO (PF >= {min_pf:.2f})")
    print("=" * 110)
    for portfolio in sorted(best_by_port):
        best = best_by_port_qualified.get(portfolio)
        if best:
            print(
                f"Portfolio ${portfolio:>8,.0f} | {best['label']:<18s} | "
                f"PnL ${best['pnl']:>+9.2f} | ROI {best['roi_pct']:>+6.2f}% | "
                f"WR {best['win_rate']:>5.1f}% | PF {best['profit_factor']:>4.2f} | DD {best['max_dd_pct']:>4.1f}%"
            )
            continue

        fallback = best_by_port.get(portfolio)
        if fallback:
            print(
                f"Portfolio ${portfolio:>8,.0f} | NO CONFIG >= PF {min_pf:.2f} "
                f"(fallback {fallback['label']} PF {fallback['profit_factor']:.2f})"
            )

    winner_pool = qualified_rows if qualified_rows else all_rows
    best_abs_pnl = max(winner_pool, key=lambda row: row["pnl"])
    best_roi = max(winner_pool, key=lambda row: row["roi_pct"])
    robust = max(winner_pool, key=lambda row: row["score"])

    print("\n" + "=" * 110)
    print("GLOBAL WINNERS")
    print("=" * 110)
    if not qualified_rows:
        print(f"WARNING: No configuration met PF >= {min_pf:.2f}. Showing best available fallback.")
    print(
        f"Highest Absolute Profit : Portfolio ${best_abs_pnl['portfolio']:,.0f} | {best_abs_pnl['label']} | "
        f"PnL ${best_abs_pnl['pnl']:+.2f} | ROI {best_abs_pnl['roi_pct']:+.2f}%"
    )
    print(
        f"Highest ROI             : Portfolio ${best_roi['portfolio']:,.0f} | {best_roi['label']} | "
        f"PnL ${best_roi['pnl']:+.2f} | ROI {best_roi['roi_pct']:+.2f}%"
    )
    print(
        f"Best Balanced Score     : Portfolio ${robust['portfolio']:,.0f} | {robust['label']} | "
        f"PnL ${robust['pnl']:+.2f} | WR {robust['win_rate']:.1f}% | PF {robust['profit_factor']:.2f} | "
        f"DD {robust['max_dd_pct']:.1f}%"
    )
    print("-" * 110)
    print("Recommended Parameters:")
    print(json.dumps(robust["params"], indent=2))
    print(f"CSV saved to: {csv_path}")
    print(f"Qualified configs (PF >= {min_pf:.2f}): {len(qualified_rows)}/{len(all_rows)}")
    print(f"Elapsed: {elapsed:.1f}s")

    if robust["profit_factor"] >= min_pf:
        save_to_brain(symbol=symbol, best_row=robust)
    else:
        logger.warning(
            "skip_save_to_brain_below_pf_threshold robust_pf=%.2f min_pf=%.2f",
            robust["profit_factor"],
            min_pf,
        )
    return 0 if qualified_rows else 2


if __name__ == "__main__":
    args = parse_args()
    portfolios = parse_portfolios(args.portfolios)
    if not portfolios:
        portfolios = DEFAULT_PORTFOLIOS

    raise SystemExit(
        run_optimization(
            days=args.days,
            user_symbol=args.symbol.strip(),
            portfolios=portfolios,
            csv_path=args.csv,
            min_pf=max(0.0, float(args.min_pf)),
        )
    )
