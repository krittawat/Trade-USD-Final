"""
Gold Strategy Maximum Profit Finder — 1-Year Optimization
==========================================================
Systematic grid search across GoldScalpPro + GoldScalpWR60 to find the
most profitable Gold configuration over 365 days.

Auto-generates parameter combinations, runs full backtests with regime
classification + dynamic sizing + ratchet trailing, ranks by composite
score, and saves top 3 to brain.db.

Usage:
    python backend/scripts/train_gold_best.py --days 365
    python backend/scripts/train_gold_best.py --days 365 --quick  # reduced grid
"""

import sys
import os
import time
import json
import logging
import logging.handlers
import random
import argparse
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from itertools import product
from typing import List, Dict, Any, Tuple, Optional
import pandas as pd
import numpy as np

# Adjust path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Initialize MT5
import MetaTrader5 as mt5

# Project imports
from app.core.logging import get_logger
from app.execution.backtester import Backtester, BacktestResult
from app.brain.memory_store import MemoryStore
from app.strategy.templates.gold_scalp_pro import GoldScalpProStrategy
from app.strategy.templates.gold_evolution import GoldEvolutionStrategy
from app.strategy.templates.gold_elite import GoldEliteStrategy
from scripts.backtest_full import FullFeatureBacktester

# ─── Logging: Console Only ───
logging.getLogger().handlers = []
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("TrainGoldBest")

def remove_file_handlers():
    for name in logging.root.manager.loggerDict:
        lg = logging.getLogger(name)
        to_remove = [h for h in lg.handlers if isinstance(h, (logging.FileHandler, logging.handlers.RotatingFileHandler))]
        for h in to_remove:
            lg.removeHandler(h)

remove_file_handlers()

import app.core.logging
_orig_get_logger = app.core.logging.get_logger
def _patched_get_logger(name, level="INFO"):
    lg = _orig_get_logger(name, level)
    for h in [h for h in lg.handlers if isinstance(h, (logging.FileHandler, logging.handlers.RotatingFileHandler))]:
        lg.removeHandler(h)
    return lg
app.core.logging.get_logger = _patched_get_logger

# Suppress noisy loggers
for log_name in ["app.core.logging", "app.risk", "app.risk.sizing",
                  "app.risk.trailing", "app.brain.regime", "FullBacktest"]:
    logging.getLogger(log_name).setLevel(logging.ERROR)

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════
SYMBOL = "XAUUSDc"
TIMEFRAME = mt5.TIMEFRAME_M5
CONTRACT_SIZE = 100.0
POINT_VALUE = 0.01
DIGITS = 2


def generate_evo_grid(quick: bool = False) -> List[Dict[str, Any]]:
    """Generate GoldEvolution parameter grid."""
    if quick:
        sl_atrs = [1.5, 2.0]
        tp_atrs = [1.5, 2.0]
        adx_mins = [18]
        min_scores = [65]
    else:
        sl_atrs = [1.2, 1.5, 2.0, 2.5]
        tp_atrs = [1.2, 1.5, 2.0, 3.0]
        adx_mins = [15, 18, 20]
        min_scores = [50, 60, 65, 70]

    combos = []
    for sl, tp, adx, min_s in product(sl_atrs, tp_atrs, adx_mins, min_scores):
        if tp < sl * 0.8:
            continue
        label = f"EVO_SL{sl}_TP{tp}_ADX{adx}_MS{min_s}"
        combos.append({
            "strategy_class": "GoldEvolution",
            "label": label,
            "params": {
                "sl_atr_mult": sl, "tp_atr_mult": tp,
                "adx_min": adx, "min_score_trade": min_s
            }
        })
    return combos

def generate_elite_grid(quick: bool = False) -> List[Dict[str, Any]]:
    """Generate GoldElite parameter grid."""
    if quick:
        sl_atrs = [1.5, 2.0]
        rr_stds = [1.5, 2.0]
        min_scores = [70]
    else:
        sl_atrs = [1.2, 1.5, 2.0, 2.5]
        rr_stds = [1.5, 1.8, 2.5, 3.0]
        min_scores = [60, 65, 70, 75]

    combos = []
    for sl, rr, min_s in product(sl_atrs, rr_stds, min_scores):
        label = f"ELITE_SL{sl}_RR{rr}_MS{min_s}"
        combos.append({
            "strategy_class": "GoldElite",
            "label": label,
            "params": {
                "sl_atr_mult": sl, "rr_standard": rr, "rr_aggressive": round(rr * 1.2, 2),
                "min_score_trade": min_s
            }
        })
    return combos


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def create_strategy(class_name: str):
    if class_name == "GoldEvolution":
        return GoldEvolutionStrategy()
    elif class_name == "GoldElite":
        return GoldEliteStrategy()
    else:
        raise ValueError(f"Unknown: {class_name}")


def apply_params(strategy, params: dict):
    for key, value in params.items():
        if hasattr(strategy, key):
            setattr(strategy, key, value)


def fetch_data(symbol: str, days: int):
    """Fetch M5 data from MT5 and return (DataFrame, account_balance)."""
    if not mt5.initialize():
        logger.error("MT5 Initialization failed")
        return None, 0.0

    account_info = mt5.account_info()
    if account_info:
        balance = account_info.balance
        currency = account_info.currency
        logger.info(f"MT5 Account: balance={balance:.2f} {currency}, equity={account_info.equity:.2f} {currency}")
    else:
        balance = 10000.0
        currency = "USD"
        logger.warning("Could not get account info")

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)

    logger.info(f"Fetching {days} days of M5 data for {symbol}...")
    rates = mt5.copy_rates_range(symbol, TIMEFRAME, start_date, end_date)

    if rates is None or len(rates) == 0:
        logger.error(f"No data fetched for {symbol}")
        mt5.shutdown()
        return None, 0.0

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    logger.info(f"Fetched {len(df):,} candles ({df['time'].min()} → {df['time'].max()})")

    mt5.shutdown()
    return df, balance


def calc_daily_stats(result: BacktestResult) -> Dict[str, float]:
    """Calculate daily stats from BacktestResult trades."""
    if not result.trades:
        return {"daily_wr": 0.0, "consistency": 0.0, "avg_daily_pnl": 0.0}

    try:
        if isinstance(result.trades[0], dict):
            trades_df = pd.DataFrame(result.trades)
        else:
            trades_df = pd.DataFrame([vars(t) for t in result.trades])

        if 'exit_time' not in trades_df.columns or trades_df.empty:
            return {"daily_wr": 0.0, "consistency": 0.0, "avg_daily_pnl": 0.0}

        trades_df['exit_time'] = pd.to_datetime(trades_df['exit_time'], errors='coerce')
        pnl_col = 'pnl' if 'pnl' in trades_df.columns else 'profit_usd'
        if pnl_col not in trades_df.columns:
            return {"daily_wr": 0.0, "consistency": 0.0, "avg_daily_pnl": 0.0}

        daily_pnl = trades_df.groupby(trades_df['exit_time'].dt.date)[pnl_col].sum()
        total_days = len(daily_pnl)
        winning_days = (daily_pnl > 0).sum()
        daily_wr = (winning_days / total_days * 100) if total_days > 0 else 0.0
        avg = daily_pnl.mean()
        std = daily_pnl.std()
        consistency = (avg / std) if std > 0 else 0.0

        return {"daily_wr": daily_wr, "consistency": consistency, "avg_daily_pnl": avg}
    except Exception:
        return {"daily_wr": 0.0, "consistency": 0.0, "avg_daily_pnl": 0.0}


def compute_score(result: BacktestResult, ds: Dict[str, float]) -> float:
    """
    Composite score — maximize profit while maintaining quality.
    
    Weights:
      - Profit Factor:  ×30  (most important: > 1.5 = good)
      - Win Rate:       ×1.5 (50%+ needed)
      - Total Profit:   scaled, capped at 50
      - Daily WR:       ×0.5 (bonus for consistency)
      - Max DD:         ×-2  (penalty)
      - Consistency:    ×10, capped at 20
    """
    if result.total_profit_usd < 0:
        return -100.0
    if result.total_trades < 15:  # Need enough trades for significance
        return -50.0

    score = 0.0
    score += min(result.profit_factor, 5.0) * 30     # PF weight
    score += result.win_rate * 1.5                     # WR weight
    score += ds.get("daily_wr", 0) * 0.5              # Daily consistency
    score -= result.max_drawdown_pct * 2.0             # DD penalty
    score += min(result.total_profit_usd / 100, 50)    # Profit bonus
    score += min(ds.get("consistency", 0) * 10, 20)    # Consistency
    return round(score, 2)


def save_to_brain(label: str, strategy_class: str, params: dict,
                  result: BacktestResult, ds: dict, score: float, rank: int):
    """Save configuration to brain.db (evolved_params — top strategies only)."""
    try:
        store = MemoryStore()
        store.connect()

        if strategy_class == "GoldScalpPro":
            strategy_name = "gold_scalp_pro"
        elif strategy_class == "GoldEvolution":
            strategy_name = "gold_evolution"
        else:
            strategy_name = "gold_elite"

        save_params = {k: v for k, v in params.items()}
        save_params["_label"] = label
        save_params["_rank"] = rank
        save_params["_strategy_class"] = strategy_class
        save_params["_win_rate"] = round(result.win_rate, 2)
        save_params["_profit_factor"] = round(result.profit_factor, 2)
        save_params["_total_profit"] = round(result.total_profit_usd, 2)
        save_params["_max_dd"] = round(result.max_drawdown_pct, 2)
        save_params["_total_trades"] = result.total_trades
        save_params["_daily_wr"] = round(ds.get("daily_wr", 0), 1)
        save_params["_trained_at"] = datetime.now(timezone.utc).isoformat()

        store.save_evolved_params(
            strategy_name=strategy_name,
            symbol=SYMBOL,
            regime="ALL",
            params=save_params,
            score=score
        )
        return True
    except Exception as e:
        logger.error(f"Failed to save: {e}")
        return False


def save_all_to_backtest_history(results: list, days: int, equity: float):
    """
    Save ALL tournament results to backtest_history table.
    This stores the complete optimization data for future analysis & improvement.
    """
    import sqlite3
    db_path = str(Path(__file__).resolve().parent.parent / "data" / "sqlite" / "brain.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)

    # Ensure table exists with enhanced schema
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT,
            config_json TEXT,
            win_rate REAL,
            profit_factor REAL,
            total_trades INTEGER,
            total_pnl REAL,
            max_dd REAL,
            run_at TEXT
        )
    """)

    # Add extra columns if they don't exist (ALTER TABLE is idempotent with try/except)
    for col, dtype in [("score", "REAL"), ("rank", "INTEGER"),
                        ("daily_wr", "REAL"), ("consistency", "REAL"),
                        ("avg_daily_pnl", "REAL"), ("equity_used", "REAL"),
                        ("data_days", "INTEGER"), ("label", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE backtest_history ADD COLUMN {col} {dtype}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    run_at = datetime.now(timezone.utc).isoformat()
    saved = 0

    for r in results:
        res = r["result"]
        ds = r["daily_stats"]
        cls = r["strategy_class"]
        strategy_name = "gold_scalp_pro" if cls == "GoldScalpPro" else "gold_evolution" if cls == "GoldEvolution" else "gold_elite"

        config = {
            "strategy_class": r["strategy_class"],
            **r["params"]
        }

        conn.execute("""
            INSERT INTO backtest_history
            (strategy_name, symbol, timeframe, config_json, win_rate, profit_factor,
             total_trades, total_pnl, max_dd, run_at, score, rank, daily_wr,
             consistency, avg_daily_pnl, equity_used, data_days, label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            strategy_name, SYMBOL, "M5", json.dumps(config),
            round(res.win_rate, 2), round(res.profit_factor, 2),
            res.total_trades, round(res.total_profit_usd, 2),
            round(res.max_drawdown_pct, 2), run_at,
            r["score"], r.get("rank", 0),
            round(ds.get("daily_wr", 0), 1),
            round(ds.get("consistency", 0), 4),
            round(ds.get("avg_daily_pnl", 0), 2),
            equity, days, r["label"]
        ))
        saved += 1

    conn.commit()
    conn.close()
    print(f"\n💾 Saved {saved} results to backtest_history table in brain.db")
    return saved


def export_csv(results: list, filepath: str):
    """Export all results to CSV (handles different param keys across strategies)."""
    if not results:
        return
    rows = []
    # Collect ALL unique keys across ALL results to avoid field mismatch
    all_param_keys = set()
    for r in results:
        all_param_keys.update(r["params"].keys())

    base_fields = ["rank", "label", "strategy", "score", "win_rate", "profit_factor",
                   "total_profit", "max_dd_pct", "total_trades", "daily_wr",
                   "consistency", "avg_daily_pnl"]
    all_fields = base_fields + sorted(all_param_keys)

    for r in results:
        res = r["result"]
        ds = r["daily_stats"]
        row = {
            "rank": r.get("rank", 0),
            "label": r["label"],
            "strategy": r["strategy_class"],
            "score": r["score"],
            "win_rate": round(res.win_rate, 2),
            "profit_factor": round(res.profit_factor, 2),
            "total_profit": round(res.total_profit_usd, 2),
            "max_dd_pct": round(res.max_drawdown_pct, 2),
            "total_trades": res.total_trades,
            "daily_wr": round(ds.get("daily_wr", 0), 1),
            "consistency": round(ds.get("consistency", 0), 3),
            "avg_daily_pnl": round(ds.get("avg_daily_pnl", 0), 2),
        }
        # Add all param keys with empty string as default for missing keys
        for k in sorted(all_param_keys):
            row[k] = r["params"].get(k, "")
        rows.append(row)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n📁 Results exported to: {filepath}")


def format_time(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"


# ═══════════════════════════════════════════════════════════════════
# MAIN TOURNAMENT
# ═══════════════════════════════════════════════════════════════════

def run_tournament(days: int = 365, initial_equity: float = 0.0, quick: bool = False):
    """Run systematic parameter optimization."""

    df, mt5_balance = fetch_data(SYMBOL, days)
    if df is None:
        print("❌ Failed to fetch data. Make sure MT5 is running and XAUUSDc is in Market Watch.")
        return

    if initial_equity <= 0:
        initial_equity = mt5_balance
    print(f"\n💰 Using equity: {initial_equity:,.2f} (from MT5 account)")

    # Generate grid
    evo_combos = generate_evo_grid(quick)
    elite_combos = generate_elite_grid(quick)
    all_contestants = evo_combos + elite_combos
    total = len(all_contestants)

    print(f"\n{'='*90}")
    print(f"🏆 GOLD MAXIMUM PROFIT FINDER — {SYMBOL} | {days} Days | Equity: {initial_equity:,.0f}")
    print(f"   Data: {len(df):,} M5 candles | Contestants: {total} (EVO: {len(evo_combos)}, ELITE: {len(elite_combos)})")
    print(f"{'='*90}\n")

    header = f"{'#':>4} {'Label':<35} {'WR%':>6} {'PF':>6} {'PnL':>10} {'DD%':>6} {'Trds':>5} {'DWR':>5} {'Score':>7} {'ETA':>6}"
    print(header)
    print("-" * len(header))

    results = []
    times = []  # Track time per run for ETA

    for i, contestant in enumerate(all_contestants):
        label = contestant["label"]
        cls = contestant["strategy_class"]
        params = contestant["params"]
        run_start = time.monotonic()

        try:
            strategy = create_strategy(cls)
            apply_params(strategy, params)

            random.seed(42)
            np.random.seed(42)

            bt = FullFeatureBacktester(strategy, initial_equity=initial_equity)
            result = bt.run(df, SYMBOL, contract_size=CONTRACT_SIZE, point=POINT_VALUE, digits=DIGITS)

            ds = calc_daily_stats(result)
            score = compute_score(result, ds)

            results.append({
                "label": label,
                "strategy_class": cls,
                "params": params,
                "result": result,
                "daily_stats": ds,
                "score": score,
            })

            elapsed = time.monotonic() - run_start
            times.append(elapsed)
            avg_time = sum(times) / len(times)
            remaining = (total - i - 1) * avg_time
            eta_str = format_time(remaining)

            pnl_str = f"${result.total_profit_usd:,.0f}"
            print(f"{i+1:>4} {label:<35} {result.win_rate:>5.1f}% {result.profit_factor:>5.2f} {pnl_str:>10} {result.max_drawdown_pct:>5.1f}% {result.total_trades:>5} {ds['daily_wr']:>4.0f}% {score:>7.1f} ~{eta_str}")

        except Exception as e:
            elapsed = time.monotonic() - run_start
            times.append(elapsed)
            print(f"{i+1:>4} {label:<35} ❌ ERROR: {e}")

    if not results:
        print("\n❌ No results.")
        return

    # Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)
    for rank, r in enumerate(results, 1):
        r["rank"] = rank

    # ─── CHAMPION ───
    best = results[0]
    best_res = best["result"]
    best_ds = best["daily_stats"]

    print(f"\n{'='*90}")
    print(f"🏆 CHAMPION: {best['label']} ({best['strategy_class']})")
    print(f"{'='*90}")
    print(f"  Win Rate       : {best_res.win_rate:.2f}%")
    print(f"  Profit Factor  : {best_res.profit_factor:.2f}")
    print(f"  Total Profit   : ${best_res.total_profit_usd:,.2f}")
    print(f"  Max Drawdown   : {best_res.max_drawdown_pct:.2f}%")
    print(f"  Total Trades   : {best_res.total_trades}")
    print(f"  Daily Win Rate : {best_ds['daily_wr']:.1f}%")
    print(f"  Consistency    : {best_ds['consistency']:.3f}")
    print(f"  Score          : {best['score']:.2f}")
    print(f"\n  Parameters:")
    for k, v in best["params"].items():
        print(f"    {k:20s} = {v}")

    # ─── TOP 10 ───
    print(f"\n{'─'*90}")
    print("📊 TOP 10:")
    print(f"  {'#':>3} {'Label':<35} {'WR%':>6} {'PF':>6} {'PnL':>10} {'DD%':>6} {'Score':>7}")
    for rank, r in enumerate(results[:10], 1):
        res = r["result"]
        emoji = ["🥇", "🥈", "🥉"][rank-1] if rank <= 3 else f" {rank}"
        pnl_str = f"${res.total_profit_usd:,.0f}"
        print(f"  {emoji:>3} {r['label']:<35} {res.win_rate:>5.1f}% {res.profit_factor:>5.2f} {pnl_str:>10} {res.max_drawdown_pct:>5.1f}% {r['score']:>7.1f}")

    # ─── WORST 3 (for reference) ───
    print(f"\n📉 WORST 3:")
    for r in results[-3:]:
        res = r["result"]
        pnl_str = f"${res.total_profit_usd:,.0f}"
        print(f"  ❌ {r['label']:<35} {res.win_rate:>5.1f}% {res.profit_factor:>5.2f} {pnl_str:>10} {r['score']:>7.1f}")

    # ─── SAVE TOP 3 TO BRAIN (evolved_params) ───
    print(f"\n💾 Saving top 3 to brain.db (evolved_params)...")
    for rank, r in enumerate(results[:3], 1):
        ok = save_to_brain(r["label"], r["strategy_class"], r["params"],
                          r["result"], r["daily_stats"], r["score"], rank)
        emoji = ["🥇", "🥈", "🥉"][rank-1]
        status = "✅" if ok else "❌"
        print(f"  {emoji} {status} {r['label']} (Score: {r['score']:.1f})")

    # ─── SAVE ALL TO BACKTEST_HISTORY ───
    save_all_to_backtest_history(results, days, initial_equity)

    # ─── EXPORT CSV ───
    csv_path = str(Path(__file__).resolve().parent.parent / "data" / "exports" / f"gold_optimization_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    export_csv(results, csv_path)

    total_time = sum(times)
    print(f"\n{'='*90}")
    print(f"✅ Training Complete! ({len(results)} combos tested in {format_time(total_time)})")
    print(f"   📊 {len(results)} results saved to backtest_history")
    print(f"   🏆 Top 3 saved to evolved_params")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gold Strategy Maximum Profit Finder")
    parser.add_argument("--days", type=int, default=365, help="Days of historical data (default: 365)")
    parser.add_argument("--equity", type=float, default=0.0, help="Initial equity (default: auto from MT5)")
    parser.add_argument("--quick", action="store_true", help="Quick mode: smaller parameter grid (~30 combos)")
    args = parser.parse_args()

    try:
        run_tournament(days=args.days, initial_equity=args.equity, quick=args.quick)
    except KeyboardInterrupt:
        print("\n⚠️ Tournament interrupted by user.")
    except Exception:
        import traceback
        traceback.print_exc()
