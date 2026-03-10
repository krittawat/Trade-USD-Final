"""
M5 Rapid Scalper Strategy 365-day Optimizer for XAU and XAG.
Goal: Find the most efficient and profitable settings over a 1-year period.
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
from app.strategy.templates.m5_rapid_scalper import M5RapidScalperStrategy
from scripts.backtest_full import FullFeatureBacktester


logging.getLogger().handlers = []
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("OptimizeRapidScalper365D")


TIMEFRAME = mt5.TIMEFRAME_M5
CONTRACT_SIZE = 100.0  # Standard account size assumption
POINT_VALUE = 0.01 
DEFAULT_DAYS = 365
DEFAULT_PORTFOLIOS = [3000.0, 10000.0]  # Cent account baseline and expanded
SYMBOL_CANDIDATES = ["XAUUSDc", "XAUUSD", "GOLD", "XAGUSDc", "XAGUSD", "SILVER"]

# High performance rapid set
PARAM_GRID = [
    {
        "label": "RAPID_ULTRA_FAST_AGGR",
        "MA_PERIOD": 10,
        "SAR_AF": 0.02,
        "SAR_MAX": 0.2,
        "RSI_PERIOD": 14,
        "SL_ATR_MULT": 1.2,
        "TP_ATR_MULT": 2.0,
        "MIN_SL_DISTANCE": 1.5,
        "MACD_FAST": 12,
        "MACD_SLOW": 26,
        "MACD_SIGNAL": 9,
        "RISK_RAPID": 0.10, # 10% risk to allow 3000 USC account trading
    },
    {
        "label": "RAPID_AGGRESSIVE_TP",
        "MA_PERIOD": 12,
        "SAR_AF": 0.02,
        "SAR_MAX": 0.2,
        "RSI_PERIOD": 14,
        "SL_ATR_MULT": 1.5,
        "TP_ATR_MULT": 3.0,
        "MIN_SL_DISTANCE": 2.0,
        "MACD_FAST": 12,
        "MACD_SLOW": 26,
        "MACD_SIGNAL": 9,
        "RISK_RAPID": 0.15,
    },
    {
        "label": "RAPID_STANDARD_15_20",
        "MA_PERIOD": 12,
        "SAR_AF": 0.02,
        "SAR_MAX": 0.2,
        "RSI_PERIOD": 14,
        "SL_ATR_MULT": 1.5,
        "TP_ATR_MULT": 2.0,
        "MIN_SL_DISTANCE": 2.0,
        "MACD_FAST": 12,
        "MACD_SLOW": 26,
        "MACD_SIGNAL": 9,
        "RISK_RAPID": 0.05,
    },
    {
        "label": "RAPID_SAFE_WIDE_SL",
        "MA_PERIOD": 12,
        "SAR_AF": 0.02,
        "SAR_MAX": 0.2,
        "RSI_PERIOD": 14,
        "SL_ATR_MULT": 2.5,
        "TP_ATR_MULT": 2.0,
        "MIN_SL_DISTANCE": 3.0,
        "MACD_FAST": 12,
        "MACD_SLOW": 26,
        "MACD_SIGNAL": 9,
        "RISK_RAPID": 0.10,
    },
    {
        "label": "RAPID_TIGHT_SL_WIDE_TP",
        "MA_PERIOD": 12,
        "SAR_AF": 0.03,
        "SAR_MAX": 0.2,
        "RSI_PERIOD": 14,
        "SL_ATR_MULT": 1.0,
        "TP_ATR_MULT": 3.0,
        "MIN_SL_DISTANCE": 2.0,
        "MACD_FAST": 8,
        "MACD_SLOW": 21,
        "MACD_SIGNAL": 5,
        "RISK_RAPID": 0.15,
    },
    {
        "label": "RAPID_LONG_RSI",
        "MA_PERIOD": 12,
        "SAR_AF": 0.02,
        "SAR_MAX": 0.2,
        "RSI_PERIOD": 21,
        "SL_ATR_MULT": 1.5,
        "TP_ATR_MULT": 2.0,
        "MIN_SL_DISTANCE": 2.0,
        "MACD_FAST": 12,
        "MACD_SLOW": 26,
        "MACD_SIGNAL": 9,
        "RISK_RAPID": 0.10,
    },
    {
        "label": "RAPID_BERSERKER",
        "MA_PERIOD": 8,
        "SAR_AF": 0.03,
        "SAR_MAX": 0.2,
        "RSI_PERIOD": 9,
        "SL_ATR_MULT": 1.0,
        "TP_ATR_MULT": 4.0,
        "MIN_SL_DISTANCE": 1.5,
        "MACD_FAST": 8,
        "MACD_SLOW": 21,
        "MACD_SIGNAL": 5,
        "RISK_RAPID": 5.0, # Uses the absolute max allowed risk by the Risk Engine (HARD_MAX_RISK_PCT)
    },
]

def parse_args():
    parser = argparse.ArgumentParser(description="M5 Rapid Scalper 365-day optimizer")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Number of days to backtest")
    parser.add_argument("--symbol", type=str, required=True, help="Target Symbol e.g. XAUUSDc or XAGUSDc")
    parser.add_argument("--min-pf", type=float, default=1.1, help="Minimum profit factor threshold")
    parser.add_argument("--portfolios", type=str, default=",".join(str(int(v)) for v in DEFAULT_PORTFOLIOS))
    parser.add_argument("--csv", type=str, default="backend/data/optimization_rapid_scalper.csv")
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

def apply_params(strategy, params):
    for key, value in params.items():
        if key == "label": continue
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
    if clean.empty: return 0.0, 0.0

    daily_pnl = clean.groupby(clean["exit_time"].dt.date)[pnl_col].sum()
    if daily_pnl.empty: return 0.0, 0.0

    total_days = len(daily_pnl)
    winning_days = int((daily_pnl > 0).sum())
    daily_win_rate = (winning_days / total_days * 100.0) if total_days else 0.0

    avg_daily = float(daily_pnl.mean())
    std_daily = float(daily_pnl.std())
    consistency = (avg_daily / std_daily) if std_daily > 0 else 0.0

    return daily_win_rate, consistency

def detect_working_symbol(user_symbol, days):
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=max(7, min(days, 30)))
    
    candidates = [user_symbol] + SYMBOL_CANDIDATES
    symbols = mt5.symbols_get()
    
    if symbols:
        available = {item.name for item in symbols}
        for symbol in candidates:
            if symbol in available:
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
        + ((result.profit_factor - 1.0) * 300.0) 
        + (result.win_rate * 3.0)
        + (daily_wr * 1.5)
        - (result.max_drawdown_pct * 8.0) 
    )

def write_csv(path, rows):
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(
            file_obj,
            fieldnames=[
                "portfolio", "label", "win_rate", "profit_factor", "pnl",
                "roi_pct", "max_dd_pct", "daily_wr", "consistency", "trades", "score", "params"
            ]
        )
        writer.writeheader()
        for row in rows:
            formatted_row = {k: v for k, v in row.items() if k != 'result'}
            formatted_row["params"] = json.dumps(row["params"], separators=(",", ":"))
            writer.writerow(formatted_row)

def run_optimization(days, user_symbol, portfolios, csv_path, min_pf):
    if not mt5.initialize():
        logger.error("mt5_initialize_failed")
        return 1

    symbol = detect_working_symbol(user_symbol, days=days)
    if not symbol:
        logger.error(f"no_working_symbol_found for {user_symbol}")
        mt5.shutdown()
        return 1

    data = fetch_data(symbol, days)
    if data is None:
        logger.error(f"no_data symbol={symbol}")
        mt5.shutdown()
        return 1

    print("=" * 110)
    print(f"RAPID SCALPER OPTIMIZATION START | Symbol={symbol} | Days={days} | Bars={len(data)}")
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
            print(f"[{run_idx:02d}/{total_runs}] Portfolio=${portfolio:,.0f} | {label} ...", end=" ", flush=True)

            random.seed(42)
            np.random.seed(42)

            strategy = M5RapidScalperStrategy()
            apply_params(strategy, params)
            bt = FullFeatureBacktester(strategy, initial_equity=portfolio)

            try:
                result = bt.run(data, symbol, contract_size=CONTRACT_SIZE, point=POINT_VALUE, digits=2)
            except Exception as exc:
                print(f"FAILED ({exc})")
                continue

            trades_df = pd.DataFrame(result.trades) if (result.trades and isinstance(result.trades[0], dict)) else pd.DataFrame()

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

            print(f"WR={result.win_rate:.1f}% PF={result.profit_factor:.2f} PnL=${result.total_profit_usd:+.2f} ROI={roi_pct:+.2f}% DD={result.max_drawdown_pct:.1f}% Trades={result.total_trades}")

    mt5.shutdown()

    if not all_rows:
        logger.error("no_results")
        return 1

    elapsed = time.monotonic() - start
    write_csv(csv_path.replace('.csv', f'_{symbol}.csv'), all_rows)
    qualified_rows = [row for row in all_rows if row["profit_factor"] >= min_pf and row["pnl"] > 0]

    print("\n" + "=" * 110)
    print(f"BEST CONFIG BY PORTFOLIO (PF >= {min_pf:.2f})")
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
            print(f"Portfolio ${portfolio:>8,.0f} | NO CONFIG >= PF {min_pf:.2f} (fallback {fallback['label']} PF {fallback['profit_factor']:.2f})")

    winner_pool = qualified_rows if qualified_rows else all_rows
    if winner_pool:
        best_abs_pnl = max(winner_pool, key=lambda row: row["pnl"])
        best_roi = max(winner_pool, key=lambda row: row["roi_pct"])
        robust = max(winner_pool, key=lambda row: row["score"])

        print("\n" + "=" * 110)
        print("GLOBAL WINNERS")
        print("=" * 110)
        if not qualified_rows:
            print(f"WARNING: No configuration met PF >= {min_pf:.2f}. Showing best available fallback.")
        
        print(f"Highest Absolute Profit : Portfolio ${best_abs_pnl['portfolio']:,.0f} | {best_abs_pnl['label']} | PnL ${best_abs_pnl['pnl']:+.2f} | ROI {best_abs_pnl['roi_pct']:+.2f}% | Trades {best_abs_pnl['trades']}")
        print(f"Highest ROI             : Portfolio ${best_roi['portfolio']:,.0f} | {best_roi['label']} | PnL ${best_roi['pnl']:+.2f} | ROI {best_roi['roi_pct']:+.2f}% | Trades {best_roi['trades']}")
        print(f"Best Balanced Score     : Portfolio ${robust['portfolio']:,.0f} | {robust['label']} | PnL ${robust['pnl']:+.2f} | WR {robust['win_rate']:.1f}% | PF {robust['profit_factor']:.2f} | DD {robust['max_dd_pct']:.1f}%")
        print("-" * 110)
        print("Recommended Parameters:")
        print(json.dumps(robust["params"], indent=2))
        
        # Save to brain
        if robust["profit_factor"] >= min_pf:
            try:
                store = MemoryStore()
                store.connect()
                store.save_evolved_params(
                    strategy_name="M5_RAPID_SCALPER",
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
            user_symbol=args.symbol.strip(),
            portfolios=portfolios,
            csv_path=args.csv,
            min_pf=max(0.0, float(args.min_pf))
        )
    )
