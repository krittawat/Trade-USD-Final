"""
OPUS Ghost Protocol — Backtest Orchestrator.

รัน backtest สำหรับ OPUS strategies ทั้ง 2 models:
    - Model A: Liquidity Hunter
    - Model B: Trend Killer

รองรับ:
    - Multi-symbol (XAUUSDc, XAGUSDc, BTCUSDc)
    - Per-session breakdown
    - Per-regime breakdown
    - Rejection criteria validation

Usage:
    python scripts/backtest_opus.py --symbols XAUUSDc,XAGUSDc,BTCUSDc --days 90
"""

import sys
import os
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

from app.core.logging import get_logger
from app.execution.backtester import Backtester, BacktestResult
from app.strategy.templates.opus_liquidity_hunter import OpusLiquidityHunterStrategy
from app.strategy.templates.opus_trend_killer import OpusTrendKillerStrategy
from app.domain.models import SymbolProfile

logger = get_logger("OPUS_Backtest")

# ═══════════════════════════════════════════════════════════════════
# Contract specifications
# ═══════════════════════════════════════════════════════════════════
SYMBOL_SPECS = {
    "XAUUSDc": {"contract_size": 100.0, "point": 0.01, "digits": 2, "spread_avg": 20},
    "XAGUSDc": {"contract_size": 5000.0, "point": 0.001, "digits": 3, "spread_avg": 15},
    "BTCUSDc": {"contract_size": 1.0, "point": 0.01, "digits": 2, "spread_avg": 50},
}

# Rejection thresholds
MIN_PF = 1.25           # Minimum Profit Factor
MAX_DD_PCT = 25.0       # Maximum Drawdown %
MIN_TRADES = 5          # Minimum trades to evaluate


def fetch_candles_mt5(symbol: str, timeframe_str: str, days: int) -> pd.DataFrame:
    """Fetch candles from MT5."""
    try:
        import MetaTrader5 as mt5

        if not mt5.initialize():
            logger.error(f"MT5 init failed: {mt5.last_error()}")
            return pd.DataFrame()

        tf_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        tf = tf_map.get(timeframe_str, mt5.TIMEFRAME_M5)
        utc_to = datetime.now(timezone.utc)
        utc_from = utc_to - timedelta(days=days)
        rates = mt5.copy_rates_range(symbol, tf, utc_from, utc_to)

        if rates is None or len(rates) == 0:
            logger.error(f"No data for {symbol}")
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df

    except Exception as e:
        logger.error(f"MT5 fetch error: {e}")
        return pd.DataFrame()


def run_backtest_for_strategy(
    strategy,
    candles: pd.DataFrame,
    symbol: str,
    initial_equity: float = 1000.0,
    risk_per_trade: float = 0.015,
) -> BacktestResult | None:
    """Run backtest for a single strategy."""
    specs = SYMBOL_SPECS.get(symbol, {})
    contract_size = specs.get("contract_size", 100.0)
    point = specs.get("point", 0.01)

    try:
        bt = Backtester(
            strategy=strategy,
            initial_equity=initial_equity,
            risk_per_trade=risk_per_trade,
            spread_points=specs.get("spread_avg", 20),
            commission_per_lot=7.0,
        )
        result = bt.run(
            candles=candles,
            symbol=symbol,
            contract_size=contract_size,
            point=point,
        )
        return result
    except Exception as e:
        logger.error(f"Backtest error for {strategy.name} on {symbol}: {e}")
        return None


def print_results_table(results: list[dict]):
    """Print formatted results table."""
    if not results:
        print("\n❌ No results to display")
        return

    print("\n" + "=" * 100)
    print("  OPUS GHOST PROTOCOL — BACKTEST RESULTS")
    print("=" * 100)
    
    header = f"{'Symbol':<12} {'Strategy':<25} {'Trades':>7} {'WR%':>7} {'PF':>7} " \
             f"{'MaxDD%':>8} {'Expect':>8} {'AvgRR':>7} {'P&L$':>10} {'Status':<10}"
    print(header)
    print("-" * 100)

    for r in results:
        status = "✅ PASS" if r["passed"] else "❌ FAIL"
        print(f"{r['symbol']:<12} {r['strategy']:<25} {r['trades']:>7} "
              f"{r['win_rate']:>6.1f}% {r['pf']:>7.2f} {r['max_dd']:>7.1f}% "
              f"{r['expectancy']:>8.2f} {r['avg_rr']:>7.2f} {r['pnl']:>10.2f} {status:<10}")

    print("=" * 100)

    # Per-regime breakdown for passed strategies
    for r in results:
        if r.get("per_regime") and r["passed"]:
            print(f"\n📊 {r['symbol']} / {r['strategy']} — Per-Regime:")
            for regime, stats in r["per_regime"].items():
                trades = stats.get("trades", 0)
                wr = stats.get("win_rate", 0)
                pf = stats.get("pf", 0)
                if trades > 0:
                    print(f"   {regime:<20} trades={trades:>4} WR={wr:>5.1f}% PF={pf:>5.2f}")


def validate_result(result: BacktestResult) -> tuple[bool, list[str]]:
    """Validate backtest result against OPUS rejection criteria."""
    reasons = []
    passed = True

    if result.total_trades < MIN_TRADES:
        reasons.append(f"Too few trades: {result.total_trades} < {MIN_TRADES}")
        passed = False

    if result.profit_factor < MIN_PF:
        reasons.append(f"PF {result.profit_factor:.2f} < {MIN_PF}")
        passed = False

    if result.max_dd_pct > MAX_DD_PCT:
        reasons.append(f"Max DD {result.max_dd_pct:.1f}% > {MAX_DD_PCT}%")
        passed = False

    return passed, reasons


def main():
    parser = argparse.ArgumentParser(description="OPUS Ghost Protocol Backtest")
    parser.add_argument("--symbols", default="XAUUSDc,XAGUSDc,BTCUSDc",
                        help="Comma-separated symbols")
    parser.add_argument("--days", type=int, default=90,
                        help="Number of days to backtest")
    parser.add_argument("--equity", type=float, default=1000.0,
                        help="Initial equity ($)")
    parser.add_argument("--risk", type=float, default=0.015,
                        help="Risk per trade (0.015 = 1.5%%)")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]

    strategies = [
        OpusLiquidityHunterStrategy(),
        OpusTrendKillerStrategy(),
    ]

    print(f"\n🔫 OPUS Ghost Protocol Backtest")
    print(f"   Symbols: {symbols}")
    print(f"   Days: {args.days}")
    print(f"   Equity: ${args.equity}")
    print(f"   Risk: {args.risk:.1%}")
    print(f"   Strategies: {[s.name for s in strategies]}")

    all_results = []
    total_start = time.time()

    for symbol in symbols:
        print(f"\n📥 Fetching {args.days}d M5 candles for {symbol}...")
        candles = fetch_candles_mt5(symbol, "M5", args.days)

        if candles.empty or len(candles) < 100:
            print(f"   ⚠️ Insufficient data for {symbol}: {len(candles)} bars")
            continue

        print(f"   ✅ {len(candles)} candles ({candles['time'].iloc[0]} → {candles['time'].iloc[-1]})")

        for strat in strategies:
            print(f"\n   🧪 Running {strat.name} on {symbol}...")
            start = time.time()

            result = run_backtest_for_strategy(
                strategy=strat,
                candles=candles,
                symbol=symbol,
                initial_equity=args.equity,
                risk_per_trade=args.risk,
            )

            elapsed = time.time() - start
            print(f"      ⏱️ {elapsed:.1f}s")

            if result is None:
                print(f"      ❌ Backtest failed")
                continue

            passed, rejection_reasons = validate_result(result)

            if not passed:
                print(f"      ❌ REJECTED: {'; '.join(rejection_reasons)}")
            else:
                print(f"      ✅ PASSED — WR={result.win_rate:.1f}% PF={result.profit_factor:.2f} "
                      f"DD={result.max_dd_pct:.1f}%")

            pnl = result.final_equity - result.initial_equity

            # Compute avg RR and expectancy
            avg_rr = 0.0
            expectancy = 0.0
            if result.trades:
                rrs = [t.get("risk_reward", 0) for t in result.trades if "risk_reward" in t]
                avg_rr = float(np.mean(rrs)) if rrs else 0.0

                wins = [t["profit_usd"] for t in result.trades if t.get("profit_usd", 0) > 0]
                losses = [abs(t["profit_usd"]) for t in result.trades if t.get("profit_usd", 0) < 0]
                if wins and losses:
                    wr = len(wins) / (len(wins) + len(losses))
                    avg_win = np.mean(wins)
                    avg_loss = np.mean(losses)
                    expectancy = (wr * avg_win) - ((1 - wr) * avg_loss)

            all_results.append({
                "symbol": symbol,
                "strategy": strat.name,
                "trades": result.total_trades,
                "win_rate": result.win_rate,
                "pf": result.profit_factor,
                "max_dd": result.max_dd_pct,
                "expectancy": expectancy,
                "avg_rr": avg_rr,
                "pnl": pnl,
                "passed": passed,
                "per_regime": result.per_regime,
                "rejection_reasons": rejection_reasons if not passed else [],
            })

    total_time = time.time() - total_start
    print_results_table(all_results)
    print(f"\n⏱️ Total time: {total_time:.1f}s")

    # Summary
    passed_count = sum(1 for r in all_results if r["passed"])
    total_count = len(all_results)
    print(f"\n{'=' * 60}")
    print(f"  OPUS QC: {passed_count}/{total_count} strategy-symbol pairs PASSED")
    print(f"{'=' * 60}")

    if passed_count == 0:
        print("\n⚠️ WARNING: No strategies passed rejection criteria.")
        print("   Consider adjusting parameters or extending backtest period.")

    return 0 if passed_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
