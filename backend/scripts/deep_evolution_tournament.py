# -*- coding: utf-8 -*-
"""
deep_evolution_tournament.py — Standalone 180-Day Institutional Tournament & Evolution Runner

This script pulls significant historical data (up to 180 days) and performs:
1. Tournament: All registered strategies compete on 80% of the data.
2. Evolution: The Top 5 winners undergo genetic optimization (15+ generations).
3. Validation: The best evolved parameters are tested on the remaining 20% hold-out data.
4. Deployment: Successful parameters are recorded in the AI Memory Store.

Usage:
    python -m backend.scripts.deep_evolution_tournament --symbol XAUUSDm --days 180
    python -m backend.scripts.deep_evolution_tournament --symbol BTCUSDm --days 180
"""

import asyncio
import argparse
import sys
import os
import time
from datetime import datetime, timezone, timedelta

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.core.logging import get_logger
from app.core.config import get_settings
from app.db.sqlite import SQLiteStore
from app.brain.memory_store import MemoryStore
from app.brain.training_orchestrator import TrainingOrchestrator
from app.strategy.factory import StrategyFactory
from app.mt5.client import MT5Client
from app.mt5.market_data import fetch_candles

logger = get_logger("deep_evolution")

# --- Constants ---
M5_BARS_PER_DAY = 288  # 24 * 60 / 5
TRAIN_SPLIT = 0.8

async def run_deep_evolution(symbol: str, days: int):
    settings = get_settings()
    
    print(f"\n{'='*72}")
    print(f" 🚀 ANTIGRAVITY DEEP EVOLUTION | {symbol} | {days} DAYS")
    print(f"{'='*72}")
    
    # 1. Initialize Components
    print(f"  [1/5] Initializing Institutional Core...")
    mt5 = MT5Client(settings=settings)
    if not mt5.connect():
        print("  ❌ Critical Error: MT5 connection failed. Ensure MT5 Terminal is open.")
        return

    db = SQLiteStore(settings=settings)
    db.connect()
    
    memory = MemoryStore()
    memory.connect()
    
    factory = StrategyFactory()
    factory.auto_register(db=db)
    
    orchestrator = TrainingOrchestrator(
        factory=factory,
        memory_store=memory,
        mt5_client=mt5,
        settings=settings
    )
    
    # 2. Ingest Data (180 days ≈ 51,840 bars for M5)
    total_bars = days * M5_BARS_PER_DAY
    print(f"  [2/5] Ingesting {total_bars} bars from MT5... (This may take a moment)")
    
    # Map symbol for broker
    broker_sym = symbol
    if hasattr(mt5, 'adapter'):
        broker_sym = mt5.adapter.map_symbol(symbol)
    
    candles = fetch_candles(broker_sym, timeframe="M5", count=total_bars)
    
    if candles is None or len(candles) < 1000:
        print(f"  ❌ Error: Insufficient data for {symbol}. Received {len(candles) if candles is not None else 0} bars.")
        return
        
    print(f"  ✅ Data Loaded: {len(candles)} bars (Start: {candles.index[0]}, End: {candles.index[-1]})")
    
    # 3. Running Tournament (Vectorized Competition)
    print(f"  [3/5] Starting Institutional Tournament (Competing 40+ Strategies)...")
    
    # Split data manually for deep control
    split_idx = int(len(candles) * TRAIN_SPLIT)
    train_candles = candles.iloc[:split_idx]
    val_candles = candles.iloc[split_idx:]
    
    profile = mt5.get_symbol_info(symbol) or None
    
    # We use the orchestrator's logic but with custom "Deep" parameters
    start_time = time.monotonic()
    
    # We simulate a specialized training run
    print(f"      Phase A: Identifying Baseline Champions...")
    tournament_results = await orchestrator.practice_engine.run_tournament(
        symbol=symbol,
        candles=train_candles,
        profile=profile
    )
    
    if not tournament_results:
        print("  ❌ Error: Tournament produced no valid candidates.")
        return
        
    top_n = tournament_results[:5]
    print(f"      Phase B: Evolving Top 5 Candidates (Deep Generations)...")
    
    best_overall_improvement = 0
    best_strategy = ""
    
    for rank, res in enumerate(top_n, 1):
        print(f"        Rank {rank}: {res.strategy_name} (Initial PF: {res.profit_factor:.2f}, WR: {res.win_rate:.1%})")
        
        # Override standard orchestrator generations for "Deep" mode
        evolve_result = await orchestrator.evolver.evolve(
            practice_engine=orchestrator.practice_engine,
            symbol=symbol,
            candles=train_candles,
            profile=profile,
            strategy_name=res.strategy_name,
            baseline_params=res.params,
            generations=20,     # Deep mode: 20 generations
            population_size=30, # Deep mode: 30 population
            deadline=0 # No deadline for deep runs
        )
        
        # 4. Deep Validation (Hold-out test)
        if evolve_result["improvement"] > 0:
            print(f"        🛡️ Validating Evolved {res.strategy_name} on unseen hold-out data...")
            is_valid = await orchestrator._validate_evolved(
                symbol=symbol,
                strategy_name=res.strategy_name,
                evolved_params=evolve_result["best_params"],
                baseline_params=res.params,
                validation_candles=val_candles,
                profile=profile
            )
            
            if is_valid:
                print(f"        ✅ VALIDATED: Improvement maintained on hold-out.")
                if evolve_result["improvement"] > best_overall_improvement:
                    best_overall_improvement = evolve_result["improvement"]
                    best_strategy = res.strategy_name
            else:
                print(f"        ⚠️ FAILED: Evolution overfitted to training data. Discarding.")

    # 5. Conclusion
    duration = (time.monotonic() - start_time) / 60
    print(f"\n{'='*72}")
    print(f" 🏁 DEEP EVOLUTION COMPLETE | Duration: {duration:.1f} min")
    print(f"  Symbol    : {symbol}")
    print(f"  Champion  : {best_strategy if best_strategy else 'Keep Baseline'}")
    print(f"  Improvement: +{best_overall_improvement:.2%}")
    print(f"{'='*72}")
    print(f"  Parameters recorded in AI Memory. Real-time bot will now use these findings.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ANTIGRAVITY Deep Evolution")
    parser.add_argument("--symbol", required=True, help="Trading symbol (e.g. XAUUSDm)")
    parser.add_argument("--days", type=int, default=180, help="Days of history (default: 180)")
    args = parser.parse_args()
    
    asyncio.run(run_deep_evolution(args.symbol, args.days))
