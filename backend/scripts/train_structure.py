#!/usr/bin/env python3
"""
Train Structure — GA Optimization for CandlestickStructure Strategy.

เป้าหมาย:
    1. ดึง historical candles จาก MT5
    2. Tournament: run candlestick_structure กับ param variants
    3. GA Evolution: optimize params per symbol
    4. Validate on hold-out split
    5. Save best params to backtest_routing + strategy_params

Usage:
    python scripts/train_structure.py --symbols XAUUSDc
    python scripts/train_structure.py --symbols XAUUSDc,XAGUSDc,BTCUSDc --candles 5000 --generations 5
"""

import sys
import os
import asyncio
import argparse
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np

from app.core.logging import get_logger
from app.db.sqlite import SQLiteStore
from app.brain.practice_engine import PracticeEngine
from app.brain.strategy_evolver import StrategyEvolver
from app.brain.regime import classify_regime
from app.strategy.factory import StrategyFactory
from app.domain.models import SymbolProfile, PracticeResult

logger = get_logger("TrainStructure")

# ═══════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════

DEFAULT_SYMBOLS = ["XAUUSDc", "XAGUSDc", "BTCUSDc"]
DEFAULT_CANDLES = 5000
DEFAULT_GENERATIONS = 5
DEFAULT_POPULATION = 15
TRAIN_SPLIT = 0.8
STRATEGY_NAME = "candlestick_structure"

# MT5 timeframes
TF_MAP = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408,
}

# Param ranges for evolution
STRUCTURE_PARAM_BOUNDS = {
    "sl_atr_mult":      (0.8, 3.0, 0.2),
    "tp_atr_mult":      (1.0, 3.5, 0.2),
    "min_score_trade":  (45, 75, 5),
    "min_score_elite":  (65, 90, 5),
    "adx_min":          (10, 25, 3),
    "rsi_extreme_high": (75, 90, 5),
    "rsi_extreme_low":  (10, 25, 5),
    "swing_lookback":   (3, 10, 1),
    "vol_spike_ratio":  (0.8, 2.0, 0.2),
    "ob_impulse_atr":   (1.0, 2.5, 0.25),
}


# ═══════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(description="Train CandlestickStructure Strategy")
    p.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS),
                   help="Comma-separated symbols")
    p.add_argument("--candles", type=int, default=DEFAULT_CANDLES,
                   help="Number of M5 candles")
    p.add_argument("--generations", type=int, default=DEFAULT_GENERATIONS,
                   help="GA generations")
    p.add_argument("--population", type=int, default=DEFAULT_POPULATION,
                   help="GA population size")
    p.add_argument("--timeframe", type=str, default="M5",
                   help="Timeframe for candles")
    return p.parse_args()


def fetch_candles(symbol: str, count: int, timeframe: str = "M5"):
    """Fetch candles from MT5."""
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            print(f"  ⚠ MT5 init failed")
            return None
        tf = TF_MAP.get(timeframe, 5)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            print(f"  ⚠ No data for {symbol}")
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df
    except Exception as e:
        print(f"  ⚠ Fetch error: {e}")
        return None


def get_symbol_profile(symbol: str):
    """Create SymbolProfile from MT5."""
    try:
        import MetaTrader5 as mt5
        info = mt5.symbol_info(symbol)
        if info is None:
            return SymbolProfile(
                symbol=symbol, point=0.01, digits=2,
                spread_avg=10.0, lot_min=0.01, lot_max=100.0, lot_step=0.01,
            )
        return SymbolProfile(
            symbol=symbol,
            point=info.point,
            digits=info.digits,
            spread_avg=info.spread * info.point,
            lot_min=info.volume_min,
            lot_max=info.volume_max,
            lot_step=info.volume_step,
        )
    except Exception:
        return SymbolProfile(
            symbol=symbol, point=0.01, digits=2,
            spread_avg=10.0, lot_min=0.01, lot_max=100.0, lot_step=0.01,
        )


def print_results_table(results: list, title: str = ""):
    """Print results as formatted table."""
    if title:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")

    print(f"{'Strategy':<25} {'WR%':>6} {'PF':>6} {'Trades':>7} {'NetPnL':>10} {'MaxDD%':>7} {'Score':>7}")
    print("-" * 74)
    for r in results:
        wr = getattr(r, 'win_rate', 0) * 100
        pf = getattr(r, 'profit_factor', 0)
        trades = getattr(r, 'total_trades', 0)
        pnl = getattr(r, 'net_profit', 0)
        dd = getattr(r, 'max_drawdown', 0) * 100
        sc = getattr(r, 'score', 0)
        name = getattr(r, 'strategy_name', '?')
        print(f"{name:<25} {wr:>5.1f}% {pf:>6.2f} {trades:>7} {pnl:>10.2f} {dd:>6.1f}% {sc:>7.2f}")


# ═══════════════════════════════════════════════════
# MAIN TRAINING PIPELINE
# ═══════════════════════════════════════════════════

async def train_symbol(
    symbol: str,
    candles: pd.DataFrame,
    profile: SymbolProfile,
    evolver: StrategyEvolver,
    db: SQLiteStore,
    args,
):
    """Full training pipeline for one symbol."""
    print(f"\n{'='*60}")
    print(f"  🎯 Training {STRATEGY_NAME} on {symbol}")
    print(f"  📊 Candles: {len(candles)} | Split: {TRAIN_SPLIT:.0%} train / {1-TRAIN_SPLIT:.0%} validate")
    print(f"{'='*60}")

    # Split
    split_idx = int(len(candles) * TRAIN_SPLIT)
    train_candles = candles.iloc[:split_idx].copy()
    val_candles = candles.iloc[split_idx:].copy()

    print(f"  Train: {len(train_candles)} bars | Validate: {len(val_candles)} bars")

    # Detect regime
    try:
        regime = classify_regime(candles)
        print(f"  Regime: {regime}")
    except Exception:
        regime = "UNKNOWN"
        print(f"  Regime: UNKNOWN (fallback)")

    # Strategy instance + factory
    from app.strategy.templates.candlestick_structure import CandlestickStructureStrategy
    strategy = CandlestickStructureStrategy()
    
    # CRITICAL: Disable AI boost during training to prevent OOM (8GB RAM)
    # AI model loads ~500K params per init, GA creates many practice runs
    strategy.p["ai_boost_enabled"] = False
    strategy._ai_init_attempted = True  # prevent lazy-load
    
    # Register in factory so PracticeEngine can find it
    factory = StrategyFactory()
    factory.register(strategy)

    # Practice engine with factory
    practice = PracticeEngine(factory=factory)

    # ─── Baseline test ───
    print(f"\n  ▶ Running baseline test...")
    t0 = time.time()
    try:
        baseline = await practice.run_practice(
            symbol=symbol,
            strategy_name=STRATEGY_NAME,
            candles=train_candles,
            profile=profile,
        )
        elapsed = time.time() - t0
        print(f"  ✅ Baseline: WR={baseline.win_rate*100:.1f}% PF={baseline.profit_factor:.2f} "
              f"Trades={baseline.total_trades} Score={baseline.score:.2f} ({elapsed:.1f}s)")
    except Exception as e:
        print(f"  ⚠ Baseline error: {e}")
        baseline = None

    # ─── GA Evolution ───
    print(f"\n  ▶ Running GA Evolution ({args.generations} gen × {args.population} pop)...")

    # Inject custom param bounds
    from app.brain import strategy_evolver as se_mod
    original_bounds = se_mod.PARAM_BOUNDS.copy()
    se_mod.PARAM_BOUNDS.update(STRUCTURE_PARAM_BOUNDS)

    try:
        evo_result = await evolver.evolve(
            practice_engine=practice,
            symbol=symbol,
            candles=train_candles,
            profile=profile,
            strategy_name=STRATEGY_NAME,
            baseline_params=None,
            generations=args.generations,
            population_size=args.population,
        )
    finally:
        # Restore original bounds
        se_mod.PARAM_BOUNDS = original_bounds

    print(f"  ✅ Evolution: best_score={evo_result['best_score']:.4f} "
          f"improvement={evo_result['improvement']:+.4f}")

    # ─── Validation ───
    print(f"\n  ▶ Validating on hold-out ({len(val_candles)} bars)...")
    try:
        val_result = await practice.run_practice(
            symbol=symbol,
            strategy_name=STRATEGY_NAME,
            candles=val_candles,
            profile=profile,
            params=evo_result.get("best_params"),
        )
        print(f"  ✅ Validation: WR={val_result.win_rate*100:.1f}% PF={val_result.profit_factor:.2f} "
              f"Trades={val_result.total_trades} DD={val_result.max_drawdown*100:.1f}%")

        # Check acceptance criteria
        passed = True
        if val_result.win_rate < 0.50:
            print(f"  ⚠ WR {val_result.win_rate*100:.1f}% < 50% — FAIL")
            passed = False
        if val_result.profit_factor < 1.3:
            print(f"  ⚠ PF {val_result.profit_factor:.2f} < 1.30 — FAIL")
            passed = False
        if val_result.max_drawdown > 0.06:
            print(f"  ⚠ DD {val_result.max_drawdown*100:.1f}% > 6% — FAIL")
            passed = False

        if passed:
            print(f"  ✅ PASSED all criteria! Saving routing...")
        else:
            print(f"  ❌ FAILED criteria — params NOT saved to routing")
            print(f"     (Still saving params for reference)")
        
        # Always save params for reference
        _save_params(db, symbol, evo_result.get("best_params", {}))

    except Exception as e:
        print(f"  ⚠ Validation error: {e}")
        import traceback as tb
        tb.print_exc()

    return evo_result


def _save_routing(db, symbol, regime, result):
    """Save best strategy to backtest_routing."""
    try:
        if db and hasattr(db, 'execute'):
            db.execute("""
                INSERT OR REPLACE INTO backtest_routing
                (symbol, regime, strategy_name, score, win_rate, profit_factor, total_trades, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (symbol, str(regime), STRATEGY_NAME,
                  result.score, result.win_rate, result.profit_factor, result.total_trades))
            print(f"  💾 Saved routing: {symbol}/{regime} → {STRATEGY_NAME}")
    except Exception as e:
        print(f"  ⚠ Save routing error: {e}")


def _save_params(db, symbol, params):
    """Save evolved params to strategy_params."""
    try:
        import json
        if db and hasattr(db, 'execute'):
            db.execute("""
                INSERT OR REPLACE INTO strategy_params
                (strategy_name, symbol, params_json, updated_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (STRATEGY_NAME, symbol, json.dumps(params)))
            print(f"  💾 Saved params for {symbol}")
    except Exception as e:
        print(f"  ⚠ Save params error: {e}")


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

async def main():
    args = parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    print("=" * 60)
    print("  🧠 CandlestickStructure Trainer")
    print(f"  Symbols: {symbols}")
    print(f"  Candles: {args.candles} | TF: {args.timeframe}")
    print(f"  GA: {args.generations} gen × {args.population} pop")
    print("=" * 60)

    # Init DB
    from app.core.config import get_settings
    settings = get_settings()
    db = SQLiteStore(settings)
    db.connect()
    evolver = StrategyEvolver(memory_store=None)

    results = {}

    for symbol in symbols:
        print(f"\n⏳ Fetching {args.candles} candles for {symbol}...")
        candles = fetch_candles(symbol, args.candles, args.timeframe)
        if candles is None or len(candles) < 200:
            print(f"  ⚠ Skipping {symbol} — insufficient data")
            continue

        profile = get_symbol_profile(symbol)
        print(f"  ✅ Got {len(candles)} candles, profile: digits={profile.digits}")

        try:
            evo_result = await train_symbol(
                symbol=symbol,
                candles=candles,
                profile=profile,
                evolver=evolver,
                db=db,
                args=args,
            )
            results[symbol] = evo_result
        except Exception as e:
            print(f"  ❌ Training failed for {symbol}: {e}")
            import traceback
            traceback.print_exc()
        
        # Free memory between symbols (8GB RAM mode)
        import gc
        gc.collect()

    # Summary
    print(f"\n{'='*60}")
    print(f"  📊 TRAINING SUMMARY")
    print(f"{'='*60}")
    for sym, res in results.items():
        print(f"  {sym}: best_score={res['best_score']:.4f} improvement={res['improvement']:+.4f}")
    if not results:
        print("  No successful training runs.")

    print(f"\n✅ Training complete!")


if __name__ == "__main__":
    asyncio.run(main())
