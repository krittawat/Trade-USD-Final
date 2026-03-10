"""
Backtest JTG Zone FVG Strategy — Focused Gold M15 Test.

Usage:
    python scripts/backtest_jtg.py
    python scripts/backtest_jtg.py --days 90
    python scripts/backtest_jtg.py --symbol XAUUSDc --days 60
"""

import sys
import os
import time
import argparse
import traceback
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import SymbolProfile, Decision
from app.execution.backtester import Backtester, BacktestResult
from app.brain.regime import classify_regime
from app.strategy.templates.jtg_zone_fvg import JTGZoneFVGStrategy

logger = get_logger("BacktestJTG")


# ═══════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════
SYMBOL_CONFIGS = {
    "XAUUSDc": {
        "contract_size": 100.0,
        "point": 0.01,
        "digits": 2,
        "asset_class": "gold",
    },
}


def connect_mt5() -> bool:
    """Connect to MT5."""
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(env_path)

    mt5_path = os.environ.get("MT5_PATH", "").strip() or None
    login = os.environ.get("MT5_LOGIN", "").strip() or None
    password = os.environ.get("MT5_PASSWORD", "").strip() or None
    server = os.environ.get("MT5_SERVER", "").strip() or None

    init_args = {}
    if mt5_path:
        init_args["path"] = mt5_path
    if login:
        init_args["login"] = int(login)
    if password:
        init_args["password"] = password
    if server:
        init_args["server"] = server

    if not mt5.initialize(**init_args):
        if not mt5.initialize():
            print(f"❌ MT5 connection failed: {mt5.last_error()}")
            return False

    info = mt5.account_info()
    if info:
        print(f"✅ MT5 Connected: Login={info.login} Balance=${info.balance:.2f}")
    return True


def fetch_candles(symbol: str, timeframe: str, n_candles: int) -> pd.DataFrame | None:
    """Fetch historical candles from MT5."""
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M15)
    rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, n_candles)

    if rates is None or len(rates) == 0:
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    if "tick_volume" not in df.columns and "real_volume" in df.columns:
        df["tick_volume"] = df["real_volume"]
    elif "tick_volume" not in df.columns:
        df["tick_volume"] = 0
    return df


def run_backtest(
    candles: pd.DataFrame,
    symbol: str,
    config: dict,
    initial_equity: float = 10000.0,
) -> BacktestResult | None:
    """Run backtest with JTG strategy."""
    try:
        from app.risk.cooldown_manager import CooldownManager
        from app.risk.risk_dampener import RiskDampener
        from app.risk.session_guard import SessionGuard

        strategy = JTGZoneFVGStrategy()

        # Relaxed safety for backtest
        cooldown = CooldownManager(cooldown_minutes=0, max_consecutive_losses=999)
        dampener = RiskDampener()
        guard = SessionGuard(max_trades_per_session=999)

        bt = Backtester(
            strategy=strategy,
            initial_equity=initial_equity,
            risk_per_trade=0.02,
            commission_per_lot=0.0,
            slippage_points=config["point"] * 2,
            warmup_bars=250,  # JTG needs EMA200 warmup
            cooldown_mgr=cooldown,
            risk_dampener=dampener,
            session_guard=guard,
            max_trades_per_session=999,
        )

        result = bt.run(
            candles=candles,
            symbol=symbol,
            contract_size=config["contract_size"],
            point=config["point"],
        )
        return result

    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        traceback.print_exc()
        return None


def print_results(result: BacktestResult, symbol: str, elapsed: float):
    """Print formatted results."""
    print("\n" + "=" * 60)
    print("📊 JTG ZONE FVG — BACKTEST RESULTS")
    print("=" * 60)

    # ─── Core Metrics ───
    print(f"\n  Symbol:        {symbol}")
    print(f"  Timeframe:     M15")
    print(f"  Strategy:      jtg_zone_fvg")
    print(f"  Duration:      {elapsed:.1f}s")
    print(f"\n  {'─' * 50}")

    print(f"  Total Trades:  {result.total_trades}")
    print(f"  Winners:       {result.winning_trades}")
    print(f"  Losers:        {result.losing_trades}")
    wr = result.win_rate
    pf = result.profit_factor
    dd = result.max_drawdown_pct

    # Color-coded status
    wr_emoji = "✅" if wr >= 50 else "❌"
    pf_emoji = "✅" if pf >= 1.3 else "❌"
    dd_emoji = "✅" if dd <= 6 else "❌"

    print(f"\n  {'─' * 50}")
    print(f"  {wr_emoji} Win Rate:    {wr:.1f}%  (target: ≥50%)")
    print(f"  {pf_emoji} PF:          {pf:.2f}   (target: ≥1.3)")
    print(f"  {dd_emoji} Max DD:      {dd:.1f}%  (target: ≤6%)")
    print(f"\n  P&L:           ${result.total_profit_usd:+.2f}")
    print(f"  Equity:        ${result.initial_equity:.2f} → ${result.final_equity:.2f}")

    if hasattr(result, "expectancy"):
        print(f"  Expectancy:    ${result.expectancy:+.2f}")
    if hasattr(result, "avg_rr"):
        print(f"  Avg RR:        {result.avg_rr:.2f}")
    if hasattr(result, "sharpe_ratio"):
        print(f"  Sharpe:        {result.sharpe_ratio:.2f}")
    if hasattr(result, "avg_bars_held"):
        print(f"  Avg Hold:      {result.avg_bars_held:.0f} bars")

    # ─── Live-Ready Check ───
    live_ready = wr >= 50 and pf >= 1.3 and dd <= 6.0
    print(f"\n  {'─' * 50}")
    if live_ready:
        print("  🟢 LIVE-READY: All criteria met!")
    else:
        reasons = []
        if wr < 50: reasons.append(f"WR {wr:.1f}% < 50%")
        if pf < 1.3: reasons.append(f"PF {pf:.2f} < 1.3")
        if dd > 6: reasons.append(f"DD {dd:.1f}% > 6%")
        print(f"  🔴 NOT LIVE-READY: {', '.join(reasons)}")

    # ─── Per-Regime Breakdown ───
    if hasattr(result, "per_regime") and result.per_regime:
        print(f"\n  {'─' * 50}")
        print("  📋 Per-Regime Breakdown:")
        for regime, stats in result.per_regime.items():
            trades = stats.get("trades", 0)
            regime_wr = stats.get("win_rate", 0)
            if trades > 0:
                print(f"    {regime:<20} Trades={trades:3d}  WR={regime_wr:5.1f}%")

    print(f"\n{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description="Backtest JTG Zone FVG Strategy")
    parser.add_argument("--symbol", default="XAUUSDc", help="Symbol (default: XAUUSDc)")
    parser.add_argument("--days", type=int, default=60, help="Days of history (default: 60)")
    parser.add_argument("--equity", type=float, default=10000.0, help="Initial equity")
    args = parser.parse_args()

    symbol = args.symbol
    config = SYMBOL_CONFIGS.get(symbol)
    if not config:
        print(f"❌ Unknown symbol: {symbol}")
        sys.exit(1)

    # Connect MT5
    if not connect_mt5():
        sys.exit(1)

    try:
        # Fetch M15 candles
        bars_per_day = 96  # M15 = 96 bars/day
        n_candles = args.days * bars_per_day
        print(f"\n📥 Fetching {n_candles:,} M15 candles for {symbol} ({args.days} days)...")

        candles = fetch_candles(symbol, "M15", n_candles)
        if candles is None or len(candles) < 300:
            print(f"❌ Not enough data ({len(candles) if candles is not None else 0} < 300)")
            sys.exit(1)

        print(f"✅ Got {len(candles):,} candles ({candles['time'].iloc[0]} → {candles['time'].iloc[-1]})")

        # Run backtest
        print(f"\n⚡ Running backtest...")
        start_t = time.monotonic()
        result = run_backtest(candles, symbol, config, args.equity)
        elapsed = time.monotonic() - start_t

        if result is None:
            print("❌ Backtest failed!")
            sys.exit(1)

        print_results(result, symbol, elapsed)

    finally:
        mt5.shutdown()
        print("🔌 MT5 disconnected.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⛔ Cancelled by user.")
    except Exception:
        traceback.print_exc()
