"""
Train XAU Dragon — End-to-end LSTM Training + Backtest for Gold AI Dragon Strategy.

Usage:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/train_xau_dragon.py --days 90 --equity 10000

Pipeline:
    1. Connect MT5 → Fetch XAU M5 candles (90 days)
    2. Train LSTM DeepLearner on XAU data
    3. Backtest GoldAIDragonStrategy with trained model
    4. Print comprehensive results
    5. Save to SQLite tournament table
"""

import sys
import os
import time
import argparse
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from math import sqrt

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pandas as pd
import numpy as np

# Load .env
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(env_path)

import MetaTrader5 as mt5

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.execution.backtester import Backtester, BacktestResult

logger = get_logger("TrainXAUDragon")


# ═════════════════════════════════════════════
# MT5 CONNECTION
# ═════════════════════════════════════════════

def connect_mt5() -> bool:
    """Connect to MT5 terminal."""
    mt5_path = (os.environ.get("MT5_PATH", "").strip()) or None
    login = os.environ.get("MT5_LOGIN", "").strip() or None
    password = os.environ.get("MT5_PASSWORD", "").strip() or None
    server = os.environ.get("MT5_SERVER", "").strip() or None

    init_args = {}
    if login:
        if mt5_path:
            init_args["path"] = mt5_path
        init_args["login"] = int(login)
        if password:
            init_args["password"] = password
        if server:
            init_args["server"] = server
    else:
        if mt5_path:
            init_args["path"] = mt5_path

    print(f"  🔌 MT5 init args: {list(init_args.keys())}")

    if not mt5.initialize(**init_args):
        if init_args and not login:
            if not mt5.initialize():
                err = mt5.last_error()
                print(f"❌ MT5 connection failed: {err}")
                return False
        else:
            err = mt5.last_error()
            print(f"❌ MT5 connection failed: {err}")
            return False

    info = mt5.account_info()
    if info:
        print(f"✅ MT5 Connected: Login={info.login} Server={info.server} "
              f"Balance={info.balance:.2f} Equity={info.equity:.2f}")
    return True


def fetch_candles(symbol: str, timeframe: str, n_candles: int) -> pd.DataFrame | None:
    """Fetch historical candles from MT5."""
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M5)
    rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, n_candles)

    if rates is None or len(rates) == 0:
        print(f"  ⚠️  No data for {symbol} ({timeframe})")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    if "tick_volume" not in df.columns:
        df["tick_volume"] = 0
    return df


# ═════════════════════════════════════════════
# STAGE 1: TRAIN LSTM
# ═════════════════════════════════════════════

def train_lstm(symbol: str = "XAUUSDc", timeframe: str = "M5", limit: int = 10000):
    """Train DeepLearner LSTM on XAU data."""
    print("\n" + "=" * 60)
    print("🧠 STAGE 1: LSTM TRAINING")
    print("=" * 60)

    from app.brain.data_loader import DLDataLoader
    from app.brain.deep_learner import DeepLearner, TORCH_AVAILABLE

    if not TORCH_AVAILABLE:
        print("❌ PyTorch not installed. Run: pip install torch")
        print("   Continuing without AI (Layer 7 will score 0)")
        return None

    # Load training data
    print(f"  📥 Loading {limit} candles for {symbol} {timeframe}...")
    loader = DLDataLoader()
    X, y = loader.load_training_data(symbol, timeframe, limit)

    if len(X) == 0:
        print("  ❌ No training data available!")
        return None

    pos_ratio = y.mean()
    print(f"  ✅ Training data ready: {len(X)} sequences")
    print(f"     Features: {X.shape[-1]}, Sequence Length: {X.shape[1]}")
    print(f"     Win ratio: {pos_ratio:.1%}")

    # Train
    print(f"\n  🚀 Training LSTM...")
    dl = DeepLearner()
    result = dl.train_session(X, y)

    if result.get("status") == "success":
        print(f"\n  ✅ Training Complete!")
        print(f"     Train Loss: {result['train_loss']:.4f}")
        print(f"     Val Loss:   {result['val_loss']:.4f}")
        print(f"     Val Acc:    {result['val_accuracy']:.1%}")
        print(f"     Best Val:   {result['best_val_loss']:.4f}")
        print(f"     Epochs:     {result['epochs_run']}")
        print(f"     Duration:   {result['duration_seconds']:.1f}s")
    else:
        print(f"  ⚠️  Training result: {result}")

    return dl


# ═════════════════════════════════════════════
# STAGE 2: BACKTEST
# ═════════════════════════════════════════════

def run_backtest(
    candles: pd.DataFrame,
    symbol: str = "XAUUSDc",
    initial_equity: float = 10000.0,
) -> BacktestResult | None:
    """Run backtest with GoldAIDragonStrategy."""
    print("\n" + "=" * 60)
    print("🐉 STAGE 2: DRAGON BACKTEST")
    print("=" * 60)

    from app.strategy.templates.gold_ai_dragon import GoldAIDragonStrategy
    from app.risk.cooldown_manager import CooldownManager
    from app.risk.risk_dampener import RiskDampener
    from app.risk.session_guard import SessionGuard

    strategy = GoldAIDragonStrategy()

    # Relaxed safety for backtest
    cooldown = CooldownManager(cooldown_minutes=0, max_consecutive_losses=999)
    dampener = RiskDampener()
    guard = SessionGuard(max_trades_per_session=999)

    bt = Backtester(
        strategy=strategy,
        initial_equity=initial_equity,
        risk_per_trade=0.02,
        commission_per_lot=0.0,
        slippage_points=0.02,  # 2 cents slippage for Gold
        warmup_bars=220,       # Need 200 EMA + 20 buffer
        cooldown_mgr=cooldown,
        risk_dampener=dampener,
        session_guard=guard,
        max_trades_per_session=999,
    )

    print(f"  📊 Running backtest: {len(candles):,} candles, ${initial_equity:,.0f} equity")
    start_t = time.monotonic()

    try:
        result = bt.run(
            candles=candles,
            symbol=symbol,
            contract_size=100.0,
            point=0.01,
        )
    except Exception as e:
        print(f"  ❌ Backtest failed: {e}")
        import traceback
        traceback.print_exc()
        return None

    elapsed = time.monotonic() - start_t
    print(f"  ⏱  Completed in {elapsed:.1f}s")

    return result


# ═════════════════════════════════════════════
# STAGE 3: RESULTS & ANALYSIS
# ═════════════════════════════════════════════

def print_results(result: BacktestResult, initial_equity: float = 10000.0):
    """Print comprehensive backtest results."""
    print("\n" + "=" * 60)
    print("📊 STAGE 3: RESULTS & ANALYSIS")
    print("=" * 60)

    if result.total_trades == 0:
        print("  ⚠️  No trades executed!")
        print("  Possible causes:")
        print("    - Score threshold too high")
        print("    - Session filter too strict")
        print("    - ADX filter blocking all signals")
        return

    # ─── Main Metrics ───
    pnl_pct = (result.final_equity - initial_equity) / initial_equity * 100
    print(f"""
  ╔══════════════════════════════════════════════╗
  ║  🐉 GOLD AI DRAGON — BACKTEST RESULTS       ║
  ╠══════════════════════════════════════════════╣
  ║  Symbol:     {result.symbol:<32}║
  ║  Strategy:   {result.strategy:<32}║
  ║  Period:     {result.start_date[:10]} → {result.end_date[:10]:<14}║
  ║  Bars:       {result.total_bars:<32,}║
  ╠══════════════════════════════════════════════╣
  ║  Total Trades:    {result.total_trades:<27}║
  ║  Winning:         {result.winning_trades:<27}║
  ║  Losing:          {result.losing_trades:<27}║
  ║  Win Rate:        {result.win_rate:<27.1f}║
  ║  Profit Factor:   {result.profit_factor:<27.2f}║
  ║  Total P&L:       ${result.total_profit_usd:<+26.2f}║
  ║  P&L %:           {pnl_pct:<+27.1f}║
  ║  Max Drawdown:    {result.max_drawdown_pct:<27.1f}║
  ║  Max DD (USD):    ${result.max_drawdown_usd:<26.2f}║
  ║  Expectancy:      ${result.expectancy:<26.2f}║
  ║  Avg R:R:         {result.avg_rr:<27.2f}║
  ║  Sharpe Ratio:    {result.sharpe_ratio:<27.2f}║
  ║  Avg Bars Held:   {result.avg_bars_held:<27.1f}║
  ║  Final Equity:    ${result.final_equity:<26.2f}║
  ╚══════════════════════════════════════════════╝""")

    # ─── QC Check ───
    print("\n  🔍 QUALITY CHECK:")
    checks = []
    wr_ok = result.win_rate >= 50
    pf_ok = result.profit_factor >= 1.3
    dd_ok = result.max_drawdown_pct <= 6
    trades_ok = result.total_trades >= 10

    checks.append(("Win Rate ≥ 50%", wr_ok, f"{result.win_rate:.1f}%"))
    checks.append(("Profit Factor ≥ 1.3", pf_ok, f"{result.profit_factor:.2f}"))
    checks.append(("Max DD ≤ 6%", dd_ok, f"{result.max_drawdown_pct:.1f}%"))
    checks.append(("Trades ≥ 10", trades_ok, f"{result.total_trades}"))

    all_pass = True
    for name, passed, value in checks:
        emoji = "✅" if passed else "❌"
        print(f"    {emoji} {name}: {value}")
        if not passed:
            all_pass = False

    if all_pass:
        print("\n  🟢 ALL QC CHECKS PASSED — LIVE CANDIDATE! 🐉🔥")
    else:
        print("\n  🟡 Some checks failed — needs more tuning or more data")

    # ─── Per-Regime Breakdown ───
    if result.per_regime:
        print("\n  📈 PER-REGIME BREAKDOWN:")
        print(f"    {'Regime':<20} {'Trades':>7} {'Win%':>7} {'P&L':>10}")
        print(f"    {'─' * 48}")
        for regime, stats in sorted(result.per_regime.items()):
            wr = stats.get("win_rate", 0)
            pnl = stats.get("pnl", 0)
            trades = stats.get("trades", 0)
            print(f"    {regime:<20} {trades:>7} {wr:>6.1f}% ${pnl:>+9.2f}")

    # ─── Trade Samples ───
    if result.trades:
        print(f"\n  📋 TRADE SAMPLES (first 10 of {len(result.trades)}):")
        print(f"    {'#':>4} {'Action':>6} {'Entry':>9} {'Exit':>9} {'P&L':>9} {'R:R':>5} {'Reason':>10} {'Bars':>5}")
        print(f"    {'─' * 65}")
        for t in result.trades[:10]:
            emoji = "🟢" if t["pnl"] > 0 else "🔴"
            print(f"    {t['id']:>4} {t['action']:>6} {t['entry']:>9.2f} {t['exit']:>9.2f} "
                  f"{emoji}${t['pnl']:>+7.2f} {t['rr']:>5.2f} {t['reason']:>10} {t['bars']:>5}")


# ═════════════════════════════════════════════
# SAVE RESULTS
# ═════════════════════════════════════════════

def save_results(result: BacktestResult, db_path: str = "data/sqlite/trading.db"):
    """Save results to SQLite."""
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS dragon_training_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                total_trades INTEGER,
                winning_trades INTEGER,
                losing_trades INTEGER,
                win_rate REAL,
                profit_factor REAL,
                total_profit_usd REAL,
                max_drawdown_pct REAL,
                expectancy REAL,
                avg_rr REAL,
                sharpe_ratio REAL,
                initial_equity REAL,
                final_equity REAL,
                per_regime TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            INSERT INTO dragon_training_results
            (symbol, strategy_name, total_trades, winning_trades, losing_trades,
             win_rate, profit_factor, total_profit_usd, max_drawdown_pct,
             expectancy, avg_rr, sharpe_ratio, initial_equity, final_equity,
             per_regime, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            result.symbol, result.strategy, result.total_trades,
            result.winning_trades, result.losing_trades,
            result.win_rate, result.profit_factor, result.total_profit_usd,
            result.max_drawdown_pct, result.expectancy, result.avg_rr,
            result.sharpe_ratio, result.initial_equity, result.final_equity,
            json.dumps(result.per_regime),
            datetime.now(timezone.utc).isoformat(),
        ))

        conn.commit()
        conn.close()
        print(f"\n  💾 Results saved to: {db_path}")
    except Exception as e:
        print(f"\n  ⚠️  Save failed: {e}")


# ═════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Train XAU Dragon — AI Gold Strategy")
    parser.add_argument("--symbol", default="XAUUSDc", help="Symbol (default: XAUUSDc)")
    parser.add_argument("--days", type=int, default=90, help="Days of history (default: 90)")
    parser.add_argument("--timeframe", default="M5", help="Timeframe (default: M5)")
    parser.add_argument("--equity", type=float, default=10000.0, help="Initial equity (default: $10,000)")
    parser.add_argument("--db", default="data/sqlite/trading.db", help="SQLite path")
    parser.add_argument("--skip-train", action="store_true", help="Skip LSTM training (use existing model)")
    parser.add_argument("--train-limit", type=int, default=10000, help="Max candles for LSTM training")
    args = parser.parse_args()

    print("=" * 60)
    print("🐉 GOLD AI DRAGON — TRAINING & BACKTEST PIPELINE")
    print("=" * 60)
    print(f"  Symbol:     {args.symbol}")
    print(f"  Period:     {args.days} days")
    print(f"  Timeframe:  {args.timeframe}")
    print(f"  Equity:     ${args.equity:,.2f}")
    print(f"  Skip Train: {args.skip_train}")

    # Connect MT5
    if not connect_mt5():
        print("\n❌ Cannot proceed without MT5 connection.")
        sys.exit(1)

    try:
        # ── Stage 1: Train LSTM ──
        if not args.skip_train:
            dl = train_lstm(args.symbol, args.timeframe, args.train_limit)
        else:
            print("\n⏭ Skipping LSTM training (using existing model)")

        # ── Fetch candles for backtest ──
        bars_per_day = {"M1": 1440, "M5": 288, "M15": 96, "M30": 48, "H1": 24}.get(args.timeframe, 288)
        n_candles = args.days * bars_per_day
        print(f"\n  📥 Fetching {n_candles:,} candles for backtest...")
        candles = fetch_candles(args.symbol, args.timeframe, n_candles)

        if candles is None or len(candles) < 300:
            print("❌ Not enough candle data!")
            sys.exit(1)

        print(f"  ✅ Got {len(candles):,} candles "
              f"({candles['time'].iloc[0]} → {candles['time'].iloc[-1]})")

        # ── Stage 2: Backtest ──
        result = run_backtest(candles, args.symbol, args.equity)

        if result is None:
            print("❌ Backtest failed!")
            sys.exit(1)

        # ── Stage 3: Results ──
        print_results(result, args.equity)

        # ── Save ──
        save_results(result, args.db)

        print("\n" + "=" * 60)
        print("🐉 DRAGON TRAINING PIPELINE COMPLETE!")
        print("=" * 60)

    finally:
        mt5.shutdown()
        print("🔌 MT5 disconnected.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⛔ Training cancelled by user.")
    except Exception:
        import traceback
        traceback.print_exc()
