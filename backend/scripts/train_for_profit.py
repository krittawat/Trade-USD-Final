#!/usr/bin/env python3
"""
Train For Profit — Tournament + Evolution + Routing Update.

เป้าหมาย:
    1. ดึง historical candles จาก MT5 ทุก symbol
    2. Tournament: ทดสอบทุก strategy บนแต่ละ symbol
    3. Filter: เอาเฉพาะ PF >= 1.3 AND WR >= 60%
    4. Evolve: ปรับ params ด้วย Genetic Algorithm สำหรับ top 3
    5. Validate: ทดสอบ evolved params บน hold-out data (20%)
    6. Save: อัปเดต backtest_routing + strategy_params
    7. Report: แสดงผลลัพธ์

Usage:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/train_for_profit.py
    python scripts/train_for_profit.py --symbols XAUUSDc,BTCUSDc
    python scripts/train_for_profit.py --candles 5000 --generations 8
"""

import sys
import os
import asyncio
import argparse
import time

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import MetaTrader5 as mt5

from app.core.logging import get_logger
from app.core.config import get_settings
from app.db.sqlite import SQLiteStore
from app.brain.memory_store import MemoryStore
from app.brain.practice_engine import PracticeEngine
from app.brain.strategy_evolver import StrategyEvolver
from app.brain.regime import classify_regime
from app.strategy.factory import StrategyFactory
from app.domain.models import SymbolProfile, PracticeResult

logger = get_logger("TrainForProfit")

# ═══════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════

MIN_PF = 1.3          # Profit Factor minimum
MIN_WR = 0.60         # Win Rate minimum (60%)
MIN_RR = 1.5          # R:R minimum
MIN_TRADES = 5        # Minimum trades to qualify
MIN_TRADES_EVOLVE = 15  # Min trades for evolution (too few = noisy)
TOP_N_EVOLVE = 3      # Top N strategies to evolve per symbol
TRAIN_SPLIT = 0.80    # 80% train, 20% validate
GENERATIONS = 3       # Genetic algorithm generations (reduced for speed)
POPULATION = 8        # Population size per generation (reduced for speed)
MAX_CANDLES = 3000    # Candles to fetch from MT5 (3000 ≈ 10 days M5, 31 days M15)
EVOLVE_TIMEOUT = 300  # Max seconds per evolution (5 min)

# Timeframe mapping
TIMEFRAME_MAP = {
    "M1": 1,    # mt5.TIMEFRAME_M1
    "M5": 5,    # mt5.TIMEFRAME_M5
    "M15": 15,  # mt5.TIMEFRAME_M15
    "M30": 30,  # mt5.TIMEFRAME_M30
    "H1": 16385,  # mt5.TIMEFRAME_H1
    "H4": 16388,  # mt5.TIMEFRAME_H4
    "D1": 16408,  # mt5.TIMEFRAME_D1
}


# ═══════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(description="Train For Profit — Tournament + Evolve")
    parser.add_argument("--symbols", type=str, default="",
                        help="Comma-separated symbols (default: from settings)")
    parser.add_argument("--strategies", type=str, default="",
                        help="Comma-separated strategies to test (default: all)")
    parser.add_argument("--candles", type=int, default=MAX_CANDLES,
                        help=f"Number of M5 candles to fetch (default: {MAX_CANDLES})")
    parser.add_argument("--generations", type=int, default=GENERATIONS,
                        help=f"GA generations (default: {GENERATIONS})")
    parser.add_argument("--population", type=int, default=POPULATION,
                        help=f"GA population size (default: {POPULATION})")
    parser.add_argument("--min-pf", type=float, default=MIN_PF,
                        help=f"Minimum Profit Factor (default: {MIN_PF})")
    parser.add_argument("--min-wr", type=float, default=MIN_WR,
                        help=f"Minimum Win Rate (default: {MIN_WR})")
    parser.add_argument("--timeframe", type=str, default="M5",
                        choices=list(TIMEFRAME_MAP.keys()),
                        help="Timeframe for candles (default: M5)")
    return parser.parse_args()


def fetch_candles(symbol: str, count: int, timeframe: str = "M5") -> pd.DataFrame:
    """Fetch candles from MT5 with configurable timeframe."""
    tf = TIMEFRAME_MAP.get(timeframe, 5)  # Default M5
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        print(f"  ❌ No candles for {symbol} ({timeframe})")
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"  📊 Fetched {len(df)} {timeframe} candles for {symbol} "
          f"({df['time'].iloc[0].strftime('%Y-%m-%d')} → {df['time'].iloc[-1].strftime('%Y-%m-%d')})")
    return df


def get_symbol_profile(symbol: str) -> SymbolProfile:
    """Create SymbolProfile from MT5 symbol info."""
    info = mt5.symbol_info(symbol)
    if info:
        return SymbolProfile(
            symbol=symbol,
            point=info.point,
            digits=info.digits,
            spread=info.spread,
            contract_size=info.trade_contract_size,
            min_lot=info.volume_min,
            max_lot=info.volume_max,
            lot_step=info.volume_step,
        )
    return SymbolProfile(symbol=symbol)


def print_banner():
    print("\n" + "=" * 70)
    print("  🧬 Antigravity AI — Train For Profit")
    print("  Tournament → Filter → Evolve → Validate → Route")
    print("=" * 70)


def print_results_table(results: list[PracticeResult], title: str = ""):
    """Print tournament results as a formatted table."""
    if title:
        print(f"\n  📊 {title}")
    print(f"  {'Strategy':<25} {'Trades':>6} {'WR%':>7} {'PF':>6} {'AvgRR':>6} {'DD%':>6} {'Score':>7} {'Status'}")
    print(f"  {'-'*80}")

    for r in results:
        status = ""
        if r.total_trades < MIN_TRADES:
            status = "⏭️  Few trades"
        elif r.win_rate >= MIN_WR and r.profit_factor >= MIN_PF:
            status = "✅ PASS"
        elif r.profit_factor < 1.0:
            status = "❌ Losing"
        else:
            status = "⚠️  Below threshold"

        print(f"  {r.strategy_name:<25} {r.total_trades:>6} {r.win_rate*100:>6.1f}% {r.profit_factor:>6.2f} {r.avg_rr:>6.2f} {r.max_drawdown:>5.1f}% {r.score:>7.3f} {status}")


# ═══════════════════════════════════════════════════
# MAIN TRAINING PIPELINE
# ═══════════════════════════════════════════════════

async def train_symbol(
    symbol: str,
    candles: pd.DataFrame,
    profile: SymbolProfile,
    factory: StrategyFactory,
    evolver: StrategyEvolver,
    db: SQLiteStore,
    args,
) -> dict:
    """
    Full training pipeline for one symbol:
        1. Tournament (all strategies)
        2. Filter (PF >= 1.3, WR >= 60%)
        3. Evolve (top 3)
        4. Validate (20% hold-out)
        5. Save routing
    """
    print(f"\n{'─'*70}")
    print(f"  🎯 Training: {symbol} ({len(candles)} candles)")
    print(f"{'─'*70}")

    # Split data: 80% train, 20% validate
    split_idx = int(len(candles) * TRAIN_SPLIT)
    train_candles = candles.iloc[:split_idx].reset_index(drop=True)
    val_candles = candles.iloc[split_idx:].reset_index(drop=True)
    print(f"  📈 Train: {len(train_candles)} bars | Validate: {len(val_candles)} bars")

    # ─── 1. TOURNAMENT ───
    print(f"\n  🏆 Running Tournament ({len(factory._strategies)} strategies)...")
    t0 = time.time()

    engine = PracticeEngine(factory=factory)
    all_results = await engine.run_tournament(
        symbol=symbol,
        candles=train_candles,
        profile=profile,
    )

    elapsed = time.time() - t0
    print(f"  ⏱️  Tournament complete in {elapsed:.1f}s")

    # Print all results
    print_results_table(all_results, f"{symbol} Tournament Results")

    # ─── 2. FILTER ───
    qualified = [
        r for r in all_results
        if r.total_trades >= MIN_TRADES
        and r.win_rate >= args.min_wr
        and r.profit_factor >= args.min_pf
    ]

    print(f"\n  📋 Qualified: {len(qualified)} / {len(all_results)} strategies ")
    print(f"     (PF ≥ {args.min_pf}, WR ≥ {args.min_wr*100:.0f}%, Trades ≥ {MIN_TRADES})")

    if not qualified:
        # Fallback: take top 3 with highest PF (even if below threshold)
        fallback = [r for r in all_results if r.total_trades >= MIN_TRADES and r.profit_factor > 1.0]
        if fallback:
            qualified = fallback[:TOP_N_EVOLVE]
            print(f"  ⚠️  No strategies meet threshold. Using top {len(qualified)} with PF > 1.0 as fallback")
        else:
            print(f"  ❌ No strategies profitable for {symbol}. Skipping evolution.")
            return {
                "symbol": symbol,
                "tournament_count": len(all_results),
                "qualified_count": 0,
                "evolved": [],
                "routed": [],
            }

    # ─── 3. EVOLVE TOP N ───
    top_n = qualified[:TOP_N_EVOLVE]
    evolved_results = []

    for r in top_n:
        # Skip evolution if too few trades (noisy results)
        if r.total_trades < MIN_TRADES_EVOLVE:
            print(f"\n  ⏭️  Skipping evolution for {r.strategy_name} (only {r.total_trades} trades, need {MIN_TRADES_EVOLVE})")
            # Still add to results with original params
            evolved_results.append({
                "strategy_name": r.strategy_name,
                "baseline_result": r,
                "evolution": {
                    "best_params": r.params or {},
                    "best_score": r.score,
                    "base_score": r.score,
                    "improvement": 0.0,
                },
            })
            continue

        print(f"\n  🧬 Evolving: {r.strategy_name} (baseline PF={r.profit_factor:.2f}, WR={r.win_rate*100:.1f}%)")

        try:
            evolution = await asyncio.wait_for(
                evolver.evolve(
                    practice_engine=engine,
                    symbol=symbol,
                    candles=train_candles,
                    profile=profile,
                    strategy_name=r.strategy_name,
                    baseline_params=r.params or {},
                    generations=args.generations,
                    population_size=args.population,
                ),
                timeout=EVOLVE_TIMEOUT,
            )
            print(f"     ✅ Evolved: score {evolution['base_score']:.4f} → {evolution['best_score']:.4f} "
                  f"(+{evolution['improvement']:.4f})")
            evolved_results.append({
                "strategy_name": r.strategy_name,
                "baseline_result": r,
                "evolution": evolution,
            })
        except asyncio.TimeoutError:
            print(f"     ⏱️  Evolution timed out ({EVOLVE_TIMEOUT}s). Using baseline params.")
            evolved_results.append({
                "strategy_name": r.strategy_name,
                "baseline_result": r,
                "evolution": {
                    "best_params": r.params or {},
                    "best_score": r.score,
                    "base_score": r.score,
                    "improvement": 0.0,
                },
            })
        except Exception as e:
            print(f"     ❌ Evolution failed: {e}")

    # ─── 4. VALIDATE ON HOLD-OUT ───
    print(f"\n  🧪 Validating on hold-out data ({len(val_candles)} bars)...")
    validated = []

    for ev in evolved_results:
        strat_name = ev["strategy_name"]
        evolved_params = ev["evolution"]["best_params"]

        try:
            # Test evolved params on validation data
            val_result = await engine.run_practice(
                symbol=symbol,
                strategy_name=strat_name,
                candles=val_candles,
                profile=profile,
                params=evolved_params,
            )

            # Also test baseline on validation data for comparison
            baseline_val = await engine.run_practice(
                symbol=symbol,
                strategy_name=strat_name,
                candles=val_candles,
                profile=profile,
            )

            passed = (
                val_result.total_trades >= 3
                and val_result.profit_factor >= 1.0
                and val_result.score >= baseline_val.score * 0.8  # At least 80% of baseline
            )

            status = "✅" if passed else "⚠️"
            print(f"     {status} {strat_name}: "
                  f"Val PF={val_result.profit_factor:.2f} WR={val_result.win_rate*100:.1f}% "
                  f"Trades={val_result.total_trades} "
                  f"(base: PF={baseline_val.profit_factor:.2f})")

            if passed:
                validated.append({
                    "strategy_name": strat_name,
                    "params": evolved_params,
                    "train_result": ev["baseline_result"],
                    "val_result": val_result,
                    "evolution": ev["evolution"],
                })
        except Exception as e:
            print(f"     ❌ Validation failed for {strat_name}: {e}")

    # ─── 5. SAVE ROUTING ───
    routed = []
    if validated:
        print(f"\n  💾 Saving routing for {len(validated)} strategies...")
        for v in validated:
            try:
                # Detect regime from training data
                regime_window = train_candles.iloc[-200:] if len(train_candles) > 200 else train_candles
                regime = classify_regime(regime_window)
                regime_str = regime.value if hasattr(regime, "value") else str(regime)

                # Save to routing table
                db.save_backtest_routing(
                    symbol=symbol,
                    regime=regime_str,
                    strategy=v["strategy_name"],
                    profit_factor=v["val_result"].profit_factor,
                    win_rate=v["val_result"].win_rate,
                    total_trades=v["val_result"].total_trades,
                    total_pnl=v["val_result"].total_r,
                    score=v["val_result"].score,
                )

                # Also save for "ALL" regime
                db.save_backtest_routing(
                    symbol=symbol,
                    regime="ALL",
                    strategy=v["strategy_name"],
                    profit_factor=v["val_result"].profit_factor,
                    win_rate=v["val_result"].win_rate,
                    total_trades=v["val_result"].total_trades,
                    total_pnl=v["val_result"].total_r,
                    score=v["val_result"].score,
                )

                routed.append(v["strategy_name"])
                print(f"     ✅ Routed: {symbol} → {v['strategy_name']} "
                      f"(PF={v['val_result'].profit_factor:.2f}, WR={v['val_result'].win_rate*100:.1f}%)")

            except Exception as e:
                print(f"     ❌ Save failed: {e}")
    else:
        # Save best available even if not fully validated
        if qualified:
            best = qualified[0]
            regime_window = train_candles.iloc[-200:] if len(train_candles) > 200 else train_candles
            regime = classify_regime(regime_window)
            regime_str = regime.value if hasattr(regime, "value") else str(regime)

            db.save_backtest_routing(
                symbol=symbol,
                regime=regime_str,
                strategy=best.strategy_name,
                profit_factor=best.profit_factor,
                win_rate=best.win_rate,
                total_trades=best.total_trades,
                total_pnl=best.total_r,
                score=best.score,
            )
            db.save_backtest_routing(
                symbol=symbol,
                regime="ALL",
                strategy=best.strategy_name,
                profit_factor=best.profit_factor,
                win_rate=best.win_rate,
                total_trades=best.total_trades,
                total_pnl=best.total_r,
                score=best.score,
            )
            routed.append(best.strategy_name)
            print(f"     ⚠️  Best available: {symbol} → {best.strategy_name} "
                  f"(PF={best.profit_factor:.2f}, WR={best.win_rate*100:.1f}%)")

    return {
        "symbol": symbol,
        "tournament_count": len(all_results),
        "qualified_count": len(qualified),
        "evolved": [e["strategy_name"] for e in evolved_results],
        "validated": [v["strategy_name"] for v in validated],
        "routed": routed,
    }


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

async def main():
    args = parse_args()
    print_banner()

    # ─── Init MT5 ───
    if not mt5.initialize():
        print("❌ MT5 initialization failed:", mt5.last_error())
        sys.exit(1)

    account = mt5.account_info()
    print(f"  MT5: {account.server} | Login: {account.login}")
    print(f"  Balance: {account.balance} {account.currency} | Equity: {account.equity}")

    # ─── Init components ───
    settings = get_settings()

    db = SQLiteStore(settings)
    db.connect()

    memory = MemoryStore(db_path=str(settings.sqlite_db_path).replace("trading.db", "brain.db"))
    memory.connect()

    factory = StrategyFactory()
    factory.auto_register(db=db)
    
    if args.strategies:
        allowed = [s.strip() for s in args.strategies.split(",")]
        factory._strategies = {k: v for k, v in factory._strategies.items() if k in allowed}
        print(f"  Filtered to {len(factory._strategies)} specific strategies: {allowed}")
    else:
        print(f"  Strategies: {len(factory._strategies)} registered")

    evolver = StrategyEvolver(memory_store=memory)

    # ─── Determine symbols ───
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        symbols = [s.strip() for s in settings.trading_symbols.split(",")]

    print(f"  Symbols: {', '.join(symbols)}")
    print(f"  Thresholds: PF ≥ {args.min_pf}, WR ≥ {args.min_wr*100:.0f}%")
    print(f"  Candles: {args.candles} ({args.timeframe}) | GA: {args.generations}gen × {args.population}pop")

    # ─── Train each symbol ───
    all_reports = []
    total_start = time.time()

    for symbol in symbols:
        # Fetch candles
        candles = fetch_candles(symbol, args.candles, args.timeframe)
        if candles.empty:
            print(f"\n  ⏭️  Skipping {symbol}: no data")
            continue

        profile = get_symbol_profile(symbol)

        report = await train_symbol(
            symbol=symbol,
            candles=candles,
            profile=profile,
            factory=factory,
            evolver=evolver,
            db=db,
            args=args,
        )
        all_reports.append(report)

    total_elapsed = time.time() - total_start

    # ─── FINAL REPORT ───
    print(f"\n{'='*70}")
    print(f"  📊 TRAINING COMPLETE — {total_elapsed:.1f}s total")
    print(f"{'='*70}")
    print(f"\n  {'Symbol':<15} {'Tested':>7} {'Passed':>7} {'Evolved':>8} {'Routed'}")
    print(f"  {'-'*60}")

    for r in all_reports:
        routed_str = ", ".join(r["routed"]) if r["routed"] else "❌ none"
        print(f"  {r['symbol']:<15} {r['tournament_count']:>7} {r['qualified_count']:>7} "
              f"{len(r.get('evolved', [])):>8} {routed_str}")

    print(f"\n  💡 Restart the bot to load updated routing!")
    print(f"     หรือเรียก: curl -X POST http://localhost:8000/api/training/trigger")
    print()

    # Cleanup
    mt5.shutdown()
    try:
        db.disconnect()
    except Exception:
        pass
    try:
        memory.close()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
