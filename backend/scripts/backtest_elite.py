"""
Elite Strategy Backtester — ทดสอบ Gold Elite & Silver Elite.

Usage:
    python scripts/backtest_elite.py --symbol XAUUSDc --days 200
    python scripts/backtest_elite.py --symbol XAGUSDc --days 200
    python scripts/backtest_elite.py --all --days 200
"""
print("DEBUG: Elite Backtest Start", flush=True)

import sys
import os
import argparse
import time
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd

from app.core.logging import get_logger
from app.execution.backtester import Backtester, BacktestTrade, BacktestResult
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile, AccountState
from app.brain.regime import classify_regime
from app.risk.sizing import calculate_lot_size
from app.risk.trailing import TrailingManager
from app.core.config import get_settings
from scripts.backtest_full import FullFeatureBacktester, MockMT5Client

logger = get_logger("EliteBacktest")
settings = get_settings()

# ═════════════════════════════════════════════
# STRATEGY CONFIGS
# ═════════════════════════════════════════════

ELITE_CONFIGS = {
    "XAUUSDc": {
        "strategy_module": "app.strategy.templates.gold_elite",
        "strategy_class": "GoldEliteStrategy",
        "timeframe": mt5.TIMEFRAME_M15,
        "contract_size": 100.0,
        "point": 0.01,
        "digits": 2,
    },
    "XAGUSDc": {
        "strategy_module": "app.strategy.templates.silver_elite",
        "strategy_class": "SilverEliteStrategy",
        "timeframe": mt5.TIMEFRAME_M15,
        "contract_size": 5000.0,
        "point": 0.001,
        "digits": 3,
    },
    # Fallback for non-cent symbols
    "XAUUSDc": {
        "strategy_module": "app.strategy.templates.gold_elite",
        "strategy_class": "GoldEliteStrategy",
        "timeframe": mt5.TIMEFRAME_M15,
        "contract_size": 100.0,
        "point": 0.01,
        "digits": 2,
    },
}


def load_strategy(symbol):
    """Dynamically load the appropriate strategy for a symbol."""
    config = ELITE_CONFIGS.get(symbol)
    if not config:
        # Try partial match
        for key, val in ELITE_CONFIGS.items():
            if key[:4] in symbol.upper():  # XAU or XAG prefix match
                config = val
                break
    if not config:
        raise ValueError(f"No elite config for {symbol}")

    import importlib
    mod = importlib.import_module(config["strategy_module"])
    cls = getattr(mod, config["strategy_class"])
    return cls(), config


def run_backtest(symbol, days, initial_equity=10000.0):
    """Run backtest for a single symbol."""
    print(f"\n{'='*60}")
    print(f"  ELITE BACKTEST: {symbol} ({days} days)")
    print(f"{'='*60}")

    strategy, config = load_strategy(symbol)
    print(f"✅ Strategy: {strategy.name}")

    # Get data from MT5
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, config["timeframe"], utc_from, utc_to)

    if rates is None or len(rates) == 0:
        print(f"❌ No data for {symbol}")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"✅ Loaded {len(df)} bars (M15, {days} days)")

    # Run backtest
    bt = FullFeatureBacktester(strategy, initial_equity=initial_equity)
    start_time = time.monotonic()
    result = bt.run(
        df, symbol,
        contract_size=config["contract_size"],
        point=config["point"],
        digits=config["digits"]
    )
    elapsed = time.monotonic() - start_time

    # Print results
    print(f"\n{'─'*50}")
    print(f"  RESULT: {strategy.name} on {symbol}")
    print(f"{'─'*50}")
    print(f"  Total Trades:  {result.total_trades}")
    print(f"  Win Rate:      {result.win_rate}%")
    print(f"  Profit Factor: {result.profit_factor}")
    print(f"  P&L:           ${result.total_profit_usd:.2f}")
    print(f"  Max DD:        {result.max_drawdown_pct}%")
    print(f"  Time:          {elapsed:.1f}s")
    print(f"{'─'*50}")

    # Pass/Fail
    wr = float(result.win_rate) if result.win_rate else 0
    pf = float(result.profit_factor) if result.profit_factor else 0

    if wr >= 60 and pf >= 1.5:
        print(f"  ✅ PASS: WR={wr}% ≥ 60% AND PF={pf} ≥ 1.5")
    elif wr >= 60:
        print(f"  ⚠️ PARTIAL: WR={wr}% ≥ 60% but PF={pf} < 1.5")
    elif pf >= 1.5:
        print(f"  ⚠️ PARTIAL: PF={pf} ≥ 1.5 but WR={wr}% < 60%")
    else:
        print(f"  ❌ FAIL: WR={wr}% < 60% AND PF={pf} < 1.5")

    # Per-regime breakdown
    if hasattr(result, 'per_regime') and result.per_regime:
        print(f"\n  Per Regime:")
        for r, stats in result.per_regime.items():
            print(f"    {r:20s}: {stats.get('win_rate', 0)}% WR | ${stats.get('pnl', 0)} P&L | {stats.get('trades', 0)} trades")

    print(f"{'='*60}\n")
    return result


def save_results_to_db(symbol, strategy_name, result, days):
    """Save backtest results to brain.db."""
    db_path = Path(__file__).resolve().parent.parent / "data" / "sqlite" / "brain.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create table if needed
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS elite_backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            days INTEGER,
            total_trades INTEGER,
            win_rate REAL,
            profit_factor REAL,
            total_profit_usd REAL,
            max_drawdown_pct REAL,
            passed_wr60 INTEGER,
            passed_pf15 INTEGER
        )
    """)

    wr = float(result.win_rate) if result.win_rate else 0
    pf = float(result.profit_factor) if result.profit_factor else 0

    cursor.execute("""
        INSERT INTO elite_backtest_results
        (timestamp, symbol, strategy, days, total_trades, win_rate,
         profit_factor, total_profit_usd, max_drawdown_pct, passed_wr60, passed_pf15)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        symbol,
        strategy_name,
        days,
        result.total_trades,
        wr,
        pf,
        result.total_profit_usd,
        result.max_drawdown_pct,
        1 if wr >= 60 else 0,
        1 if pf >= 1.5 else 0,
    ))
    conn.commit()
    conn.close()
    print(f"💾 Results saved to {db_path}")


def main():
    parser = argparse.ArgumentParser(description="Elite Strategy Backtester")
    parser.add_argument("--symbol", type=str, default=None, help="Symbol to test (e.g. XAUUSDc)")
    parser.add_argument("--days", type=int, default=200, help="Days of data (default: 200)")
    parser.add_argument("--equity", type=float, default=10000.0, help="Initial equity (default: 10000)")
    parser.add_argument("--all", action="store_true", help="Test all elite symbols")
    args = parser.parse_args()

    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    symbols = []
    if args.all:
        symbols = list(ELITE_CONFIGS.keys())
    elif args.symbol:
        symbols = [args.symbol]
    else:
        symbols = ["XAUUSDc"]  # Default

    print(f"🚀 Elite Backtest Tournament")
    print(f"   Symbols: {symbols}")
    print(f"   Days: {args.days}")
    print(f"   Equity: ${args.equity}")
    print()

    results = {}
    for sym in symbols:
        try:
            result = run_backtest(sym, args.days, args.equity)
            if result:
                results[sym] = result
                strategy, _ = load_strategy(sym)
                save_results_to_db(sym, strategy.name, result, args.days)
        except Exception as e:
            print(f"❌ Error backtesting {sym}: {e}")
            import traceback
            traceback.print_exc()

    # Summary table
    if results:
        print(f"\n{'='*70}")
        print(f"  TOURNAMENT SUMMARY")
        print(f"{'='*70}")
        print(f"  {'Symbol':12s} {'Strategy':15s} {'Trades':>7s} {'WR%':>6s} {'PF':>6s} {'P&L':>10s} {'DD%':>6s} {'Status':>8s}")
        print(f"  {'─'*12} {'─'*15} {'─'*7} {'─'*6} {'─'*6} {'─'*10} {'─'*6} {'─'*8}")
        for sym, res in results.items():
            strategy, _ = load_strategy(sym)
            wr = float(res.win_rate) if res.win_rate else 0
            pf = float(res.profit_factor) if res.profit_factor else 0
            status = "✅ PASS" if wr >= 60 and pf >= 1.5 else ("⚠️ PART" if wr >= 60 or pf >= 1.5 else "❌ FAIL")
            print(f"  {sym:12s} {strategy.name:15s} {res.total_trades:7d} {wr:6.1f} {pf:6.2f} ${res.total_profit_usd:9.2f} {res.max_drawdown_pct:6.1f} {status:>8s}")
        print(f"{'='*70}")

    mt5.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
