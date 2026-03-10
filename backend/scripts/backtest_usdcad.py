"""
USDCADc Backtest Optimization — Grid search for best WR + Profitability.
========================================================================
Strategy: UsdCadPrecisionStrategy (ForexPrecision 7-Layer Confluence)
Target:   Win Rate > 50%, Profit Factor > 1.0, Net Profit > 0

Grid Parameters:
    - SL_ATR_MULT:     SL distance (ATR multiplier)
    - RR_TARGET:       Risk:Reward target ratio
    - ADX_THRESHOLD:   Min ADX for trend confirmation
    - MIN_CONFIDENCE:  Minimum confidence threshold to enter

Usage:
    python backend/scripts/backtest_usdcad.py
"""

print("🇨🇦 USDCADc Optimization — Starting...", flush=True)

import sys
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logging.disable(logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

from app.core.config import get_settings
from app.execution.backtester import Backtester, BacktestTrade, BacktestResult

settings = get_settings()

# ─── Symbol Profile ───
SYMBOL = "USDCADc"
CONTRACT_SIZE = 100_000.0
POINT = 0.00001
DIGITS = 5
DAYS = 200
INITIAL_EQUITY = 70.0   # Match real account equity
STRATEGY_NAME = "usdcad_precision"


# ─── Curated Parameter Grid (~30 configs) ───
# Focus on combinations that maximize WR while keeping PF > 1.0
PARAM_GRID = [
    # --- Conservative (Tight SL, Low RR = High WR) ---
    {"label": "CONSERVATIVE_1",   "SL_ATR_MULT": 1.5, "RR_TARGET": 1.5, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.50},
    {"label": "CONSERVATIVE_2",   "SL_ATR_MULT": 1.5, "RR_TARGET": 1.5, "ADX_THRESHOLD": 15, "MIN_CONFIDENCE": 0.45},
    {"label": "CONSERVATIVE_3",   "SL_ATR_MULT": 2.0, "RR_TARGET": 1.5, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.50},
    {"label": "CONSERVATIVE_4",   "SL_ATR_MULT": 2.0, "RR_TARGET": 1.5, "ADX_THRESHOLD": 15, "MIN_CONFIDENCE": 0.45},

    # --- Balanced (RR 2.0) ---
    {"label": "BALANCED_1",       "SL_ATR_MULT": 1.5, "RR_TARGET": 2.0, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.50},
    {"label": "BALANCED_2",       "SL_ATR_MULT": 2.0, "RR_TARGET": 2.0, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.50},
    {"label": "BALANCED_3",       "SL_ATR_MULT": 2.0, "RR_TARGET": 2.0, "ADX_THRESHOLD": 15, "MIN_CONFIDENCE": 0.50},
    {"label": "BALANCED_4",       "SL_ATR_MULT": 2.5, "RR_TARGET": 2.0, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.50},
    {"label": "BALANCED_5",       "SL_ATR_MULT": 1.5, "RR_TARGET": 2.0, "ADX_THRESHOLD": 15, "MIN_CONFIDENCE": 0.45},
    {"label": "BALANCED_6",       "SL_ATR_MULT": 2.0, "RR_TARGET": 2.0, "ADX_THRESHOLD": 25, "MIN_CONFIDENCE": 0.55},

    # --- Aggressive (Wide TP for bigger wins) ---
    {"label": "AGGRESSIVE_1",     "SL_ATR_MULT": 1.5, "RR_TARGET": 2.5, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.50},
    {"label": "AGGRESSIVE_2",     "SL_ATR_MULT": 2.0, "RR_TARGET": 2.5, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.55},
    {"label": "AGGRESSIVE_3",     "SL_ATR_MULT": 2.0, "RR_TARGET": 3.0, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.55},
    {"label": "AGGRESSIVE_4",     "SL_ATR_MULT": 2.5, "RR_TARGET": 3.0, "ADX_THRESHOLD": 25, "MIN_CONFIDENCE": 0.55},
    {"label": "AGGRESSIVE_5",     "SL_ATR_MULT": 1.5, "RR_TARGET": 3.0, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.50},

    # --- Loose Filter (More trades, lower confidence) ---
    {"label": "LOOSE_1",          "SL_ATR_MULT": 1.5, "RR_TARGET": 2.0, "ADX_THRESHOLD": 15, "MIN_CONFIDENCE": 0.40},
    {"label": "LOOSE_2",          "SL_ATR_MULT": 2.0, "RR_TARGET": 2.0, "ADX_THRESHOLD": 15, "MIN_CONFIDENCE": 0.40},
    {"label": "LOOSE_3",          "SL_ATR_MULT": 2.0, "RR_TARGET": 1.5, "ADX_THRESHOLD": 15, "MIN_CONFIDENCE": 0.40},
    {"label": "LOOSE_4",          "SL_ATR_MULT": 2.5, "RR_TARGET": 2.0, "ADX_THRESHOLD": 15, "MIN_CONFIDENCE": 0.45},

    # --- Strict Filter (Fewer but higher quality trades) ---
    {"label": "STRICT_1",         "SL_ATR_MULT": 2.0, "RR_TARGET": 2.0, "ADX_THRESHOLD": 25, "MIN_CONFIDENCE": 0.60},
    {"label": "STRICT_2",         "SL_ATR_MULT": 2.0, "RR_TARGET": 2.5, "ADX_THRESHOLD": 25, "MIN_CONFIDENCE": 0.60},
    {"label": "STRICT_3",         "SL_ATR_MULT": 1.5, "RR_TARGET": 2.0, "ADX_THRESHOLD": 25, "MIN_CONFIDENCE": 0.60},
    {"label": "STRICT_4",         "SL_ATR_MULT": 2.5, "RR_TARGET": 2.5, "ADX_THRESHOLD": 25, "MIN_CONFIDENCE": 0.55},

    # --- Wide SL Protection (Less SL whipsaw) ---
    {"label": "WIDE_SL_1",        "SL_ATR_MULT": 3.0, "RR_TARGET": 2.0, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.50},
    {"label": "WIDE_SL_2",        "SL_ATR_MULT": 3.0, "RR_TARGET": 2.5, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.50},
    {"label": "WIDE_SL_3",        "SL_ATR_MULT": 3.0, "RR_TARGET": 3.0, "ADX_THRESHOLD": 15, "MIN_CONFIDENCE": 0.45},

    # --- Tight SL (Small risk) ---
    {"label": "TIGHT_SL_1",       "SL_ATR_MULT": 1.5, "RR_TARGET": 2.5, "ADX_THRESHOLD": 25, "MIN_CONFIDENCE": 0.55},
    {"label": "TIGHT_SL_2",       "SL_ATR_MULT": 1.5, "RR_TARGET": 3.0, "ADX_THRESHOLD": 25, "MIN_CONFIDENCE": 0.55},

    # --- Original Default (from UsdCadPrecisionStrategy) ---
    {"label": "ORIGINAL_DEFAULT", "SL_ATR_MULT": 2.0, "RR_TARGET": 3.0, "ADX_THRESHOLD": 20, "MIN_CONFIDENCE": 0.60},
]


def apply_params(params: dict):
    """Apply grid-search params to strategy module-level constants."""
    import app.strategy.templates.forex_precision as mod

    param_map = {
        "SL_ATR_MULT": "SL_ATR_MULT",
        "RR_TARGET": "RR_TARGET",
        "ADX_THRESHOLD": "ADX_THRESHOLD",
        "MIN_CONFIDENCE": "MIN_CONFIDENCE",
    }
    for grid_key, mod_key in param_map.items():
        if grid_key in params:
            setattr(mod, mod_key, params[grid_key])


def run_single_backtest(candles: pd.DataFrame, params: dict) -> BacktestResult:
    """Run one backtest with the given params.

    Uses standard Backtester (not FullFeature) to keep it lightweight.
    Regime filter is disabled (actionable forced True) to trade all conditions.
    """
    # Apply params to module before importing strategy
    apply_params(params)

    from app.strategy.templates.forex_precision import ForexPrecisionStrategy
    from app.risk.regime_filter import RegimeFilter
    from app.risk.cooldown_manager import CooldownManager
    from app.risk.risk_dampener import RiskDampener
    from app.risk.session_guard import SessionGuard

    # Use ForexPrecisionStrategy directly (NOT UsdCadPrecisionStrategy)
    # because UsdCadPrecisionStrategy hardcodes sl_atr_mult=2.0, rr_target=3.0
    # in its analyze() override, bypassing our module-level constant changes.
    strategy = ForexPrecisionStrategy()
    strategy.name = "usdcad_precision"  # Tag as USDCAD for DB

    # Create a permissive regime filter for all-conditions trading
    regime_filter = RegimeFilter()

    bt = Backtester(
        strategy,
        initial_equity=INITIAL_EQUITY,
        risk_per_trade=0.01,        # 1% risk per trade
        warmup_bars=250,            # Need 200+ bars for EMA/ADX warmup
        regime_filter=regime_filter,
        cooldown_mgr=CooldownManager(),
        risk_dampener=RiskDampener(),
        session_guard=SessionGuard(max_trades_per_session=10),  # Allow more trades
    )

    # Monkey-patch: Force all regimes actionable for "ทุกสภาวะตลาด"
    _original_run = bt.run

    def patched_run(candles, symbol, contract_size, point):
        """Wrap run to make classify_regime always actionable."""
        import app.brain.regime as regime_mod
        _original_classify = regime_mod.classify_regime

        def classify_all_actionable(candles_arg, profile=None):
            result = _original_classify(candles_arg, profile)
            result.actionable = True  # Force actionable for all regimes
            return result

        regime_mod.classify_regime = classify_all_actionable
        try:
            return _original_run(candles, symbol, contract_size=contract_size, point=point)
        finally:
            regime_mod.classify_regime = _original_classify

    result = patched_run(candles, SYMBOL, contract_size=CONTRACT_SIZE, point=POINT)
    return result


def main():
    """Main optimization loop."""
    print("=" * 110)
    print("🇨🇦 USDCADc — PRECISION STRATEGY OPTIMIZATION (All Regimes)")
    print(f"   Symbol: {SYMBOL} | Days: {DAYS} | Configs: {len(PARAM_GRID)} | Equity: ${INITIAL_EQUITY}")
    print("=" * 110)

    # ─── 1. Initialize MT5 & Fetch Data ───
    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, utc_from, utc_to)

    if rates is None or len(rates) == 0:
        print(f"❌ No data for {SYMBOL}")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"✅ Data: {len(df)} bars ({df['time'].iloc[0]} → {df['time'].iloc[-1]})\n")

    mt5.shutdown()

    # ─── 2. Run Grid Search ───
    results = []
    for idx, params in enumerate(PARAM_GRID, 1):
        label = params.get("label", f"SET_{idx}")
        print(f"[{idx}/{len(PARAM_GRID)}] {label:25s}...", end=" ", flush=True)

        t0 = time.monotonic()
        try:
            result = run_single_backtest(df, params)
            elapsed = time.monotonic() - t0
            results.append((params, result))
            print(
                f"Done ({elapsed:.1f}s) | "
                f"Trades={result.total_trades:>3} | "
                f"WR={result.win_rate:>5.1f}% | "
                f"PF={result.profit_factor:>5.2f} | "
                f"P&L=${result.total_profit_usd:>+8.2f}"
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"ERROR ({elapsed:.1f}s): {e}")
            continue

    # ─── 3. Summary Table ───
    print("\n" + "=" * 120)
    print(f"    {'Config':<25s} | {'Trades':>6} | {'W':>4} | {'L':>4} | {'WR%':>6} | {'PF':>5} | {'P&L':>11} | {'DD%':>5} | {'Exp$/T':>7} | {'Sharpe':>6} |")
    print("-" * 120)

    # Sort: prioritize WR >= 50% AND PF > 1.0 AND profit > 0, then by PnL
    sorted_results = sorted(results, key=lambda x: (
        1 if x[1].win_rate >= 50 and x[1].profit_factor > 1.0 and x[1].total_profit_usd > 0 else 0,
        x[1].total_profit_usd,
    ), reverse=True)

    best = None
    for params, r in sorted_results:
        label = params.get("label", "?")
        exp = round(r.total_profit_usd / r.total_trades, 2) if r.total_trades > 0 else 0
        meets = r.win_rate >= 50 and r.profit_factor > 1.0 and r.total_profit_usd > 0
        profitable = r.total_profit_usd > 0

        if meets and r.profit_factor >= 1.3:
            marker = "🏆"
        elif meets:
            marker = "✅"
        elif profitable:
            marker = "⭐"
        else:
            marker = "❌"

        prefix = ">>> " if meets and best is None else "    "
        if meets and best is None:
            best = (params, r)

        print(
            f"{prefix}{label:<21s} | {r.total_trades:>6} | {r.winning_trades:>4} | "
            f"{r.losing_trades:>4} | {r.win_rate:>5.1f}% | {r.profit_factor:>5.2f} | "
            f"${r.total_profit_usd:>+9.2f} | {r.max_drawdown_pct:>4.1f}% | "
            f"${exp:>+5.02f} | {r.sharpe_ratio:>5.2f} | {marker}"
        )

    print("=" * 120)

    # ─── 4. Best Config Detail ───
    if best:
        bp, br = best
        print(f"\n🏆 BEST CONFIG: {bp.get('label', '?')}")
        print(f"   WR={br.win_rate}% | PF={br.profit_factor} | P&L=${br.total_profit_usd:.2f} | DD={br.max_drawdown_pct}% | {br.total_trades} trades")
        print(f"\n   Production Parameters (for usdcad strategy):")
        for k, v in bp.items():
            if k == "label":
                continue
            print(f"     {k} = {v}")

        if br.per_regime:
            print(f"\n   Per-Regime Breakdown:")
            for regime, stats in br.per_regime.items():
                total_r = stats.get("trades", 0)
                wr_r = round(stats.get("wins", 0) / total_r * 100, 1) if total_r > 0 else 0
                print(f"     {regime:<20s}: WR={wr_r:>5.1f}% | N={total_r:>3} | P&L=${stats.get('pnl', 0):>+.2f}")
    else:
        print("\n⚠️ No config met criteria (WR >= 50%, PF > 1.0, Profit > 0)")
        # Still save best PnL config
        if sorted_results:
            best = sorted_results[0]
            bp, br = best
            print(f"   Best available: {bp.get('label', '?')} — WR={br.win_rate}%, PF={br.profit_factor}, P&L=${br.total_profit_usd:.2f}")

    # ─── 5. Save to SQLite DB ───
    print("\n💾 Saving results to SQLite DB...")
    try:
        from app.core.config import get_settings
        from app.db.sqlite import SQLiteStore

        db = SQLiteStore(get_settings())
        db.connect()

        # Save ALL results as audit trail
        for params, r in results:
            exp = round(r.total_profit_usd / r.total_trades, 2) if r.total_trades > 0 else 0
            db.save_backtest_result(
                symbol=SYMBOL,
                strategy_name=STRATEGY_NAME,
                label=params.get("label", ""),
                params={k: v for k, v in params.items() if k != "label"},
                win_rate=r.win_rate,
                profit_factor=r.profit_factor,
                total_pnl=r.total_profit_usd,
                max_drawdown_pct=r.max_drawdown_pct,
                total_trades=r.total_trades,
                winning_trades=r.winning_trades,
                losing_trades=r.losing_trades,
                backtest_days=DAYS,
                expectancy=exp,
                per_regime=r.per_regime or {},
            )
        print(f"   ✅ Saved {len(results)} backtest results to audit trail")

        # Save BEST config as active strategy params
        if best:
            bp, br = best
            clean_params = {k: v for k, v in bp.items() if k != "label"}
            db.save_strategy_params(
                symbol=SYMBOL,
                strategy_name=STRATEGY_NAME,
                params=clean_params,
                win_rate=br.win_rate,
                profit_factor=br.profit_factor,
                total_pnl=br.total_profit_usd,
                max_drawdown_pct=br.max_drawdown_pct,
                total_trades=br.total_trades,
                backtest_days=DAYS,
                label=bp.get("label", ""),
            )
            print(f"   ✅ Saved winning config '{bp.get('label', '')}' to strategy_params")
            print(f"      → Use: db.get_strategy_params('{SYMBOL}', '{STRATEGY_NAME}')")

        db.disconnect()
    except Exception as e:
        import traceback
        print(f"   ⚠️ DB save (state.db) failed: {e}")
        traceback.print_exc()

    # ─── 6. Save to Brain Memory (evolved_params + backtest_history) ───
    print("\n🧠 Saving to Brain Memory (brain.db)...")
    try:
        from app.brain.memory_store import MemoryStore

        brain = MemoryStore()
        brain.connect()

        if best:
            bp, br = best
            clean_params = {k: v for k, v in bp.items() if k != "label"}
            # Score = WR × PF (higher = better)
            score = br.win_rate * br.profit_factor
            brain.save_evolved_params(
                strategy_name=STRATEGY_NAME,
                symbol=SYMBOL,
                regime="ALL",
                params=clean_params,
                score=score,
            )
            print(f"   ✅ Saved to evolved_params (score={score:.1f})")

        brain.disconnect()
    except Exception as e:
        import traceback
        print(f"   ⚠️ Brain DB save failed: {e}")
        traceback.print_exc()

    print("\n✅ USDCADc Optimization Complete!")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
