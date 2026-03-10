"""
1-Year Backtest Runner — ทดสอบ + ปรับจูน strategy ย้อนหลัง 1 ปี.

คุณสมบัติ:
    - ดึง OHLCV ย้อนหลัง 1 ปีจาก MT5 สำหรับทุก symbol
    - รัน backtest bar-by-bar (SL/TP simulation) ด้วย Backtester engine
    - ทดสอบทุก strategy ที่ register ใน TEMPLATE_REGISTRY
    - คำนวณ metrics: win_rate, profit_factor, max_dd, expectancy, sharpe
    - จัดอันดับ strategy ตาม market regime
    - เก็บผลใน SQLite (backtest_results + strategy_rankings)
    - ปรับจูน: เก็บ best strategy per symbol per regime

Usage:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/run_backtest_1y.py
    python scripts/run_backtest_1y.py --symbols XAUUSDc,EURUSDc --equity 500
"""

import sys
import os
import json
import time
import sqlite3
import argparse
import importlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd

# =============================================
# pandas_ta Python 3.14 compat (source files patched via fix_pandas_ta.py)
# Safety-net: suppress .category AttributeError on Series
# =============================================
_orig_setattr = pd.Series.__setattr__
def _safe_setattr(self, name, value):
    if name == "category" and isinstance(value, str):
        try:
            _orig_setattr(self, name, value)
        except (AttributeError, ValueError):
            pass
    else:
        _orig_setattr(self, name, value)
pd.Series.__setattr__ = _safe_setattr
# =============================================

from app.core.logging import get_logger
from app.execution.backtester import Backtester, BacktestResult
from app.strategy.templates import _SEED_REGISTRY
from app.domain.enums import RegimeType

# Rebuild TEMPLATE_REGISTRY compatibility map
TEMPLATE_REGISTRY = {}
for item in _SEED_REGISTRY:
    TEMPLATE_REGISTRY[item["strategy_name"]] = (
        item["class_name"],
        item["module_path"],
        item.get("timeframe", "M5"),
        item.get("suitable_regimes", []),
    )

logger = get_logger("Backtest1Y")

# =============================================
# Suppress noisy loggers during backtest
# =============================================
import logging

def suppress_strategy_loggers():
    """
    Suppress all strategy/brain loggers to WARNING.
    
    Must be called AFTER strategies are loaded (because get_logger
    uses propagate=False with individual handlers).
    """
    suppress_prefixes = [
        "app.strategy", "app.brain", "Backtester",
        "scalping", "sniper", "trend_rider", "gold_scalp",
    ]
    for name, log in logging.Logger.manager.loggerDict.items():
        if isinstance(log, logging.Logger):
            if any(prefix in name for prefix in suppress_prefixes):
                log.setLevel(logging.WARNING)
    
    # Also set root to WARNING to catch any new loggers
    # (except our own Backtest1Y logger)
    logging.getLogger().setLevel(logging.WARNING)


# =============================================
# MT5 Data Fetcher
# =============================================

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

# Contract sizes for common symbols (Exness micro account)
CONTRACT_SIZES = {
    "XAUUSDc": 100.0,
    "EURUSDc": 100000.0,
    "GBPUSDc": 100000.0,
    "USDJPYc": 100000.0,
    "AUDUSDc": 100000.0,
    "USDCADc": 100000.0,
    "NZDUSDc": 100000.0,
    "BTCUSDc": 1.0,
}

POINT_VALUES = {
    "XAUUSDc": 0.01,
    "EURUSDc": 0.00001,
    "GBPUSDc": 0.00001,
    "USDJPYc": 0.001,
    "AUDUSDc": 0.00001,
    "USDCADc": 0.00001,
    "NZDUSDc": 0.00001,
    "BTCUSDc": 0.01,
}


def fetch_ohlcv(symbol: str, timeframe: str, days: int = 365) -> pd.DataFrame | None:
    """ดึง OHLCV จาก MT5 ย้อนหลัง N วัน."""
    tf = TIMEFRAME_MAP.get(timeframe, mt5.TIMEFRAME_M5)
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)

    rates = mt5.copy_rates_range(symbol, tf, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        logger.warning(f"No data for {symbol} {timeframe} (last {days}d)")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    # Ensure required columns
    for col in ["open", "high", "low", "close", "tick_volume"]:
        if col not in df.columns:
            logger.error(f"Missing column '{col}' in {symbol} data")
            return None

    logger.info(f"Fetched {len(df)} bars for {symbol} {timeframe}")
    return df


# =============================================
# SQLite Storage
# =============================================

def init_backtest_db(db_path: str) -> sqlite3.Connection:
    """สร้าง/เปิด SQLite database สำหรับเก็บผล backtest."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS backtest_1y_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            start_date TEXT,
            end_date TEXT,
            total_bars INTEGER,
            total_trades INTEGER,
            winning_trades INTEGER,
            losing_trades INTEGER,
            win_rate REAL,
            profit_factor REAL,
            total_profit_usd REAL,
            max_drawdown_pct REAL,
            max_drawdown_usd REAL,
            expectancy REAL,
            avg_rr REAL,
            sharpe_ratio REAL,
            avg_bars_held REAL,
            initial_equity REAL,
            final_equity REAL,
            per_regime_json TEXT,
            duration_seconds REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS strategy_rankings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            regime TEXT NOT NULL,
            rank INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            score REAL NOT NULL,
            win_rate REAL,
            profit_factor REAL,
            max_drawdown_pct REAL,
            total_trades INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS backtest_routing (
            symbol TEXT NOT NULL,
            regime TEXT NOT NULL,
            strategy TEXT NOT NULL,
            profit_factor REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            total_trades INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            score REAL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (symbol, regime)
        );

        CREATE INDEX IF NOT EXISTS idx_bt_results_symbol ON backtest_1y_results(symbol);
        CREATE INDEX IF NOT EXISTS idx_bt_results_strategy ON backtest_1y_results(strategy);
        CREATE INDEX IF NOT EXISTS idx_bt_rankings_symbol ON strategy_rankings(symbol, regime);
    """)
    conn.commit()
    return conn


def save_result(conn: sqlite3.Connection, run_id: str, result: BacktestResult, timeframe: str):
    """บันทึกผล backtest 1 รายการ."""
    conn.execute("""
        INSERT INTO backtest_1y_results (
            run_id, symbol, strategy, timeframe,
            start_date, end_date, total_bars, total_trades,
            winning_trades, losing_trades, win_rate, profit_factor,
            total_profit_usd, max_drawdown_pct, max_drawdown_usd,
            expectancy, avg_rr, sharpe_ratio, avg_bars_held,
            initial_equity, final_equity, per_regime_json, duration_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_id, result.symbol, result.strategy, timeframe,
        result.start_date, result.end_date, result.total_bars, result.total_trades,
        result.winning_trades, result.losing_trades, result.win_rate, result.profit_factor,
        result.total_profit_usd, result.max_drawdown_pct, result.max_drawdown_usd,
        result.expectancy, result.avg_rr, result.sharpe_ratio, result.avg_bars_held,
        result.initial_equity, result.final_equity,
        json.dumps(result.per_regime, ensure_ascii=False),
        result.duration_seconds,
    ))
    conn.commit()


def compute_score(result: BacktestResult) -> float:
    """
    คำนวณ composite score สำหรับจัดอันดับ strategy.
    
    Score weights:
        - Profit Factor (40%): ยิ่งสูงยิ่งดี
        - Win Rate (20%): ≥50% = bonus
        - Sharpe Ratio (20%): risk-adjusted return
        - Max DD penalty (20%): ยิ่ง DD มากยิ่งแย่
    """
    if result.total_trades < 5:
        return -999.0  # ไม่มีสถิติเพียงพอ

    pf_score = min(result.profit_factor, 5.0) * 20.0          # max 100
    wr_score = result.win_rate                                 # max 100
    sharpe_score = min(max(result.sharpe_ratio, -2), 5) * 20   # -40 to 100
    dd_penalty = min(result.max_drawdown_pct, 50) * 2          # max 100 penalty

    score = (pf_score * 0.4) + (wr_score * 0.2) + (sharpe_score * 0.2) - (dd_penalty * 0.2)
    return round(score, 2)


def save_rankings(conn: sqlite3.Connection, run_id: str, rankings: list[dict]):
    """บันทึก ranking ลง SQLite."""
    for r in rankings:
        conn.execute("""
            INSERT INTO strategy_rankings (
                run_id, symbol, regime, rank, strategy, score,
                win_rate, profit_factor, max_drawdown_pct, total_trades
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id, r["symbol"], r["regime"], r["rank"], r["strategy"], r["score"],
            r.get("win_rate", 0), r.get("profit_factor", 0),
            r.get("max_drawdown_pct", 0), r.get("total_trades", 0),
        ))

    # Update backtest_routing (upsert best per symbol+regime)
    for r in rankings:
        if r["rank"] == 1 and r["score"] > 0:
            conn.execute("""
                INSERT OR REPLACE INTO backtest_routing (
                    symbol, regime, strategy, profit_factor, win_rate,
                    total_trades, total_pnl, score, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                r["symbol"], r["regime"], r["strategy"],
                r.get("profit_factor", 0), r.get("win_rate", 0),
                r.get("total_trades", 0), r.get("total_profit_usd", 0),
                r["score"]
            ))

    conn.commit()


# =============================================
# Strategy Loader
# =============================================

def load_strategy(name: str):
    """โหลด strategy จาก TEMPLATE_REGISTRY."""
    if name not in TEMPLATE_REGISTRY:
        return None

    class_name, module_path, timeframe, regimes = TEMPLATE_REGISTRY[name]

    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name, None)
        if cls is None:
            return None
        instance = cls()
        return instance, timeframe, regimes
    except Exception as e:
        logger.debug(f"Skip {name}: {e}")
        return None


# =============================================
# Main Runner
# =============================================

def run_backtest(
    symbols: list[str],
    initial_equity: float = 1000.0,
    risk_pct: float = 0.02,
    days: int = 365,
    db_path: str | None = None,
):
    """
    รัน backtest 1 ปีสำหรับทุก symbol × ทุก strategy.
    
    ผลลัพธ์:
        - เก็บผลใน SQLite
        - จัดอันดับ strategy ตาม regime
        - แสดงสรุป top strategies
    """
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    if db_path is None:
        db_path = os.path.join(
            os.path.dirname(__file__), "..", "data", "sqlite", "trading.db"
        )
    db_path = os.path.abspath(db_path)

    print(f"\n{'='*70}")
    print(f"  ANTIGRAVITY 1-YEAR BACKTEST")
    print(f"  Run ID: {run_id}")
    print(f"  Symbols: {', '.join(symbols)}")
    print(f"  Equity: ${initial_equity:.2f}  |  Risk: {risk_pct*100:.1f}%")
    print(f"  Period: {days} days  |  DB: {db_path}")
    print(f"{'='*70}\n")

    # Connect MT5
    if not mt5.initialize():
        print(f"[ERROR] MT5 not connected: {mt5.last_error()}")
        return

    # Get actual account info for margin calculation
    acct = mt5.account_info()
    if acct:
        print(f"  Account: {acct.login} | Balance: ${acct.balance:.2f} | Leverage: 1:{acct.leverage}")
        if initial_equity > acct.equity:
            print(f"  [INFO] Simulated equity (${initial_equity:.0f}) > actual (${acct.equity:.2f}) — using simulated")
    print()

    # Init DB
    conn = init_backtest_db(db_path)

    # Load all strategies
    strategies = {}
    for name in TEMPLATE_REGISTRY:
        loaded = load_strategy(name)
        if loaded:
            strategies[name] = loaded

    print(f"  Loaded {len(strategies)}/{len(TEMPLATE_REGISTRY)} strategies\n")

    # Suppress verbose strategy loggers now that they're loaded
    suppress_strategy_loggers()

    # ─── Run backtest for each symbol × strategy ───
    all_results: dict[str, list[tuple[str, BacktestResult, float]]] = {}  # symbol -> [(name, result, score)]

    for symbol in symbols:
        print(f"\n{'─'*60}")
        print(f"  Symbol: {symbol}")
        print(f"{'─'*60}")

        contract_size = CONTRACT_SIZES.get(symbol, 100000.0)
        point_val = POINT_VALUES.get(symbol, 0.00001)

        # Also try to get from MT5
        sym_info = mt5.symbol_info(symbol)
        if sym_info:
            contract_size = sym_info.trade_contract_size
            point_val = sym_info.point

        symbol_results = []

        strat_count = 0
        strat_total = len(strategies)

        for strat_name, (strat_instance, timeframe, regimes) in strategies.items():
            strat_count += 1
            # Check if strategy has analyze method
            if not hasattr(strat_instance, 'analyze'):
                continue

            # Skip M1 for Gold — too swingy, wastes time
            is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
            if is_gold and timeframe == "M1":
                print(f"  [{strat_count}/{strat_total}] {strat_name}... ⏭️ SKIP (M1 Gold too swingy)", flush=True)
                continue

            # Fetch data for this timeframe
            candles = fetch_ohlcv(symbol, timeframe, days=days)
            if candles is None or len(candles) < 100:
                continue

            # Show progress
            print(f"  [{strat_count}/{strat_total}] Testing {strat_name}... ", end="", flush=True)

            try:
                bt = Backtester(
                    strategy=strat_instance,
                    initial_equity=initial_equity,
                    risk_per_trade=risk_pct,
                    warmup_bars=50,
                )
                result = bt.run(
                    candles=candles,
                    symbol=symbol,
                    contract_size=contract_size,
                    point=point_val,
                )

                score = compute_score(result)

                # Save to DB
                save_result(conn, run_id, result, timeframe)

                symbol_results.append((strat_name, result, score))

                # Compact log
                status = "✅" if result.profit_factor > 1.0 and result.win_rate > 40 else "❌"
                print(f"{status} "
                      f"Trades:{result.total_trades:4d} | "
                      f"WR:{result.win_rate:5.1f}% | "
                      f"PF:{result.profit_factor:5.2f} | "
                      f"DD:{result.max_drawdown_pct:5.1f}% | "
                      f"P&L:${result.total_profit_usd:8.2f} | "
                      f"Score:{score:6.1f}", flush=True)

            except BaseException as e:
                if isinstance(e, SystemExit):
                    raise  # Don't catch intentional exits
                print(f"❌ ERROR: {type(e).__name__}: {str(e)[:60]}", flush=True)
                continue

        all_results[symbol] = symbol_results

    # ─── Rank strategies per symbol × regime ───
    print(f"\n\n{'='*70}")
    print(f"  STRATEGY RANKINGS (Best per Symbol × Regime)")
    print(f"{'='*70}\n")

    all_rankings = []

    for symbol, results in all_results.items():
        if not results:
            continue

        # Sort by score
        results.sort(key=lambda x: x[2], reverse=True)

        print(f"\n  📊 {symbol} — Top 5:")
        for rank, (name, result, score) in enumerate(results[:5], 1):
            print(f"     #{rank} {name:30s} Score:{score:6.1f} "
                  f"WR:{result.win_rate:.1f}% PF:{result.profit_factor:.2f} DD:{result.max_drawdown_pct:.1f}%")

            all_rankings.append({
                "symbol": symbol,
                "regime": "ALL",
                "rank": rank,
                "strategy": name,
                "score": score,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "max_drawdown_pct": result.max_drawdown_pct,
                "total_trades": result.total_trades,
                "total_profit_usd": result.total_profit_usd,
                "timeframe": TEMPLATE_REGISTRY.get(name, ("", "", "M5", []))[2],
            })

        # Per-regime ranking
        regime_scores: dict[str, list[tuple[str, float, BacktestResult]]] = {}
        for name, result, score in results:
            for regime_name, regime_stats in result.per_regime.items():
                if regime_stats.get("trades", 0) < 3:
                    continue
                if regime_name not in regime_scores:
                    regime_scores[regime_name] = []
                # Score per regime: simple win_rate + pnl combo
                r_score = regime_stats.get("win_rate", 0) * 0.5 + (1 if regime_stats.get("pnl", 0) > 0 else -10)
                regime_scores[regime_name].append((name, r_score, result))

        for regime_name, scored in regime_scores.items():
            scored.sort(key=lambda x: x[1], reverse=True)
            if scored:
                best_name, best_score, best_result = scored[0]
                all_rankings.append({
                    "symbol": symbol,
                    "regime": regime_name,
                    "rank": 1,
                    "strategy": best_name,
                    "score": best_score,
                    "win_rate": best_result.win_rate,
                    "profit_factor": best_result.profit_factor,
                    "max_drawdown_pct": best_result.max_drawdown_pct,
                    "total_trades": best_result.total_trades,
                    "total_profit_usd": best_result.total_profit_usd,
                    "timeframe": TEMPLATE_REGISTRY.get(best_name, ("", "", "M5", []))[2],
                })

    # Save rankings
    if all_rankings:
        save_rankings(conn, run_id, all_rankings)
        print(f"\n  💾 Rankings saved to: {db_path}")
        print(f"     Tables: backtest_1y_results, strategy_rankings, backtest_routing")

    # ─── Summary ───
    total_tests = sum(len(r) for r in all_results.values())
    profitable = sum(
        1 for results in all_results.values()
        for _, r, _ in results
        if r.profit_factor > 1.0
    )

    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"  Total tests: {total_tests}")
    print(f"  Profitable strategies: {profitable} ({profitable/max(total_tests,1)*100:.0f}%)")
    print(f"  Results saved in: {db_path}")
    print(f"{'='*70}\n")

    conn.close()
    mt5.shutdown()


# =============================================
# CLI
# =============================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Antigravity 1-Year Backtest Runner")
    parser.add_argument("--symbols", type=str, default="XAUUSDc,EURUSDc,GBPUSDc,USDJPYc,AUDUSDc,USDCADc,NZDUSDc",
                        help="Comma-separated symbols")
    parser.add_argument("--equity", type=float, default=0,
                        help="Initial equity ($). 0 = use account equity")
    parser.add_argument("--risk", type=float, default=0.02,
                        help="Risk per trade (0.02 = 2%%)")
    parser.add_argument("--days", type=int, default=365,
                        help="Backtest period (days)")
    parser.add_argument("--db", type=str, default=None,
                        help="SQLite DB path")

    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    equity = args.equity if args.equity > 0 else 0

    # If equity is 0, we'll get it from account
    if equity == 0:
        mt5.initialize()
        acct = mt5.account_info()
        if acct:
            equity = acct.equity
        else:
            equity = 1000.0
            print(f"[WARN] Cannot get account equity, using default ${equity}")
        mt5.shutdown()

    run_backtest(
        symbols=symbols,
        initial_equity=equity,
        risk_pct=args.risk,
        days=args.days,
        db_path=args.db,
    )
