"""
Silver Evolution Strategy 365-day Optimizer for XAG.
Goal: Find settings achieving high win rate (>60%) and steady profit.
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
from app.strategy.templates.silver_evolution import SilverEvolutionStrategy, SILVER_EVOLUTION_DEFAULTS
from scripts.backtest_silver_evolution import SilverEvolutionBacktester

logging.getLogger().handlers = []
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("OptimizeSilverEvolution365D")

TIMEFRAME = mt5.TIMEFRAME_M5
CONTRACT_SIZE = 5000.0  
POINT_VALUE = 0.001 
DEFAULT_DAYS = 360
DEFAULT_PORTFOLIOS = [3000.0, 10000.0]

# Expanded grid based on V5 configurations to find the optimal balance
PARAM_GRID = [
    # Safe settings (Higher TP, Standard SL)
    {
        "label": "SILVER_EVO_SAFE",
        "tp_atr_mult_ranging": 1.0,
        "tp_atr_mult_trending": 1.5,
        "sl_atr_mult": 2.2,
        "min_score_ranging": 60,
        "min_score_trending": 60,
        "min_score_high": 70,
        "adx_min": 18,
        "counter_trend_enabled": False
    },
    # Balanced settings (Lower TP, Wider SL for higher WR)
    {
        "label": "SILVER_EVO_BALANCED",
        "tp_atr_mult_ranging": 1.0,
        "tp_atr_mult_trending": 1.25,
        "sl_atr_mult": 2.5,
        "min_score_ranging": 60,
        "min_score_trending": 60,
        "min_score_high": 70,
        "adx_min": 18,
        "counter_trend_enabled": False
    },
    # High WR Target (Very wide SL, tight TP)
    {
        "label": "SILVER_EVO_HIGH_WR",
        "tp_atr_mult_ranging": 0.8,
        "tp_atr_mult_trending": 1.0,
        "sl_atr_mult": 2.8,
        "min_score_ranging": 65,
        "min_score_trending": 65,
        "min_score_high": 75,
        "adx_min": 20,
        "counter_trend_enabled": False
    },
    # Trend focus (Only trade strong trends)
    {
        "label": "SILVER_EVO_TREND_FOCUS",
        "tp_atr_mult_ranging": 1.5,
        "tp_atr_mult_trending": 2.0,
        "sl_atr_mult": 2.0,
        "min_score_ranging": 65,
        "min_score_trending": 60,
        "min_score_high": 70,
        "adx_min": 25,
        "counter_trend_enabled": False
    },
    # Adaptive lower threshold (More trades, slightly lower WR risk)
    {
        "label": "SILVER_EVO_MORE_TRADES",
        "tp_atr_mult_ranging": 1.0,
        "tp_atr_mult_trending": 1.3,
        "sl_atr_mult": 2.0,
        "min_score_ranging": 55,
        "min_score_trending": 55,
        "min_score_high": 65,
        "adx_min": 15,
        "counter_trend_enabled": True,
        "min_score_counter": 65,
        "tp_atr_mult_counter": 0.5
    },
]

def parse_args():
    parser = argparse.ArgumentParser(description="Silver Evolution 365-day optimizer")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Number of days to backtest")
    parser.add_argument("--symbol", type=str, default="XAGUSDc", help="Target Symbol e.g. XAGUSDc")
    parser.add_argument("--min-pf", type=float, default=1.3, help="Minimum profit factor threshold")
    parser.add_argument("--portfolios", type=str, default=",".join(str(int(v)) for v in DEFAULT_PORTFOLIOS))
    parser.add_argument("--csv", type=str, default="backend/data/optimization_silver_evolution.csv")
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

def score_run(result, daily_wr):
    if result.total_profit_usd <= 0:
        return -1e9 + result.total_profit_usd
    return (
        (result.total_profit_usd * 0.5)
        + ((result.profit_factor - 1.0) * 500.0) 
        + (result.win_rate * 5.0)
        + (daily_wr * 2.0)
        - (result.max_drawdown_pct * 15.0) 
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

def run_optimization(days, symbol, portfolios, csv_path, min_pf):
    df_m5 = None
    df_h1 = None
    
    # ── M5 Data ──
    pq_path_m5 = Path(f"backend/data/exports/mtf/{symbol}_M5.parquet")
    if pq_path_m5.exists():
        print(f"📦 Loading M5 from DuckDB Parquet: {pq_path_m5}...", end=" ", flush=True)
        try:
            import duckdb
            conn = duckdb.connect(":memory:")
            safe = str(pq_path_m5).replace("\\", "/")
            df_m5 = conn.execute(f"SELECT * FROM read_parquet('{safe}') ORDER BY time ASC").df()
            
            if "time" in df_m5.columns and not pd.api.types.is_datetime64_any_dtype(df_m5["time"]):
                df_m5["time"] = pd.to_datetime(df_m5["time"], unit="s", errors="coerce", utc=True)
            elif "time" in df_m5.columns and df_m5["time"].dt.tz is None:
                df_m5["time"] = pd.to_datetime(df_m5["time"]).dt.tz_localize("UTC")
            elif "time" in df_m5.columns:
                df_m5["time"] = pd.to_datetime(df_m5["time"]).dt.tz_convert("UTC")

            if days and len(df_m5) > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                df_m5 = df_m5[df_m5["time"] >= cutoff].reset_index(drop=True)
            print(f"✅ Loaded {len(df_m5)} bars")
            conn.close()
        except Exception as e:
            print(f"⚠️ Parquet M5 load error: {e}")
            df_m5 = None

    # ── H1 Data ──
    pq_path_h1 = Path(f"backend/data/exports/mtf/{symbol}_H1.parquet")
    if pq_path_h1.exists():
        print(f"📦 Loading H1 from DuckDB Parquet: {pq_path_h1}...", end=" ", flush=True)
        try:
            import duckdb
            conn = duckdb.connect(":memory:")
            safe = str(pq_path_h1).replace("\\", "/")
            df_h1 = conn.execute(f"SELECT * FROM read_parquet('{safe}') ORDER BY time ASC").df()
            
            if "time" in df_h1.columns and not pd.api.types.is_datetime64_any_dtype(df_h1["time"]):
                df_h1["time"] = pd.to_datetime(df_h1["time"], unit="s", errors="coerce", utc=True)
            elif "time" in df_h1.columns and df_h1["time"].dt.tz is None:
                df_h1["time"] = pd.to_datetime(df_h1["time"]).dt.tz_localize("UTC")
            elif "time" in df_h1.columns:
                df_h1["time"] = pd.to_datetime(df_h1["time"]).dt.tz_convert("UTC")

            if days and len(df_h1) > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                df_h1 = df_h1[df_h1["time"] >= cutoff].reset_index(drop=True)
            print(f"✅ Loaded {len(df_h1)} bars")
            conn.close()
        except Exception as e:
            print(f"⚠️ Parquet H1 load error: {e}")
            df_h1 = None
            
    if df_m5 is None or len(df_m5) == 0:
        print(f"📡 Loading from MT5 ({days} days)...", end=" ", flush=True)
        if not mt5.initialize():
            logger.error("mt5_initialize_failed")
            return 1
        utc_to = datetime.now(timezone.utc)
        utc_from = utc_to - timedelta(days=days)
        logger.info("fetch_data M5 symbol=%s days=%s", symbol, days)
        rates_m5 = mt5.copy_rates_range(symbol, TIMEFRAME, utc_from, utc_to)
        if rates_m5 is None or len(rates_m5) == 0:
            logger.error(f"no_data symbol={symbol}")
            mt5.shutdown()
            return 1
        df_m5 = pd.DataFrame(rates_m5)
        df_m5["time"] = pd.to_datetime(df_m5["time"], unit="s", utc=True)
        print(f"✅ Loaded {len(df_m5)} bars")
        
        logger.info("fetch_data H1 symbol=%s days=%s", symbol, days)
        rates_h1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, utc_from, utc_to)
        if rates_h1 is None or len(rates_h1) == 0:
            df_h1 = None
        else:
            df_h1 = pd.DataFrame(rates_h1)
            df_h1["time"] = pd.to_datetime(df_h1["time"], unit="s", utc=True)

    print("=" * 110)
    print(f"SILVER EVOLUTION OPTIMIZATION START | Symbol={symbol} | Days={days} | Bars={len(df_m5)}")
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

            strategy = SilverEvolutionStrategy()
            # Disable AI boost for faster grid search and fewer crashes
            strategy.p["ai_boost_enabled"] = False
            
            # Apply params
            for k, v in params.items():
                if k != "label":
                    strategy.p[k] = v

            bt = SilverEvolutionBacktester(strategy, initial_equity=portfolio, h1_candles=df_h1)

            try:
                result = bt.run(df_m5, symbol, contract_size=CONTRACT_SIZE, point=POINT_VALUE, digits=3)
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

            if row["profit_factor"] >= min_pf and row["pnl"] > 0 and row["win_rate"] >= 60.0:
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
                    strategy_name="silver_evolution",
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
