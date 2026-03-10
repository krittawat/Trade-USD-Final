
import asyncio
import pandas as pd
import sys
import os

# Ensure backend acts as root for app imports
sys.path.append(os.path.join(os.getcwd(), "backend"))

from app.brain.training_orchestrator import TrainingOrchestrator
from app.strategy.factory import StrategyFactory
from app.mt5.client import MT5Client
from app.brain.memory_store import MemoryStore
from app.core.config import Settings

async def main():
    print("Starting XAU Diagnostic...")
    from app.db.sqlite import SQLiteStore
    settings = Settings()
    
    # AI Brain (Memory)
    memory = MemoryStore()
    memory.connect()
    
    # Strategy/Trade DB
    db = SQLiteStore(settings=settings)
    db.connect()
    
    mt5 = MT5Client(settings=settings)
    if not mt5.connect():
        print("Failed to connect to MT5")
        return
        
    factory = StrategyFactory()
    factory.auto_register(db=db)
    
    orchestrator = TrainingOrchestrator(
        factory=factory,
        memory_store=memory,
        mt5_client=mt5,
        settings=settings
    )
    
    symbol = "XAUUSDm"
    MAX_CANDLES = 5000  # Scaled down for stability test
    print(f"Fetching data for {symbol}...")
    candles = orchestrator._fetch_historical_candles(symbol)
    if candles is not None:
        candles = candles.iloc[-5000:]  # Scale down for test
    if candles is None:
        print("Failed to fetch candles.")
        return
    print(f"Loaded {len(candles)} candles.")
    
    profile = orchestrator._get_profile(symbol)
    tick_value = profile.contract_size * profile.point
    print(f"Profile: {profile.symbol}, ContractSize: {profile.contract_size}, Point: {profile.point}, CalcTickValue: {tick_value}")
    
    print("Running Tournament...")
    # Test only the first 5 strategies to save time
    strat_names = list(factory._strategies.keys())[:5]
    print(f"Testing strategies: {strat_names}")
    
    results = await orchestrator.practice_engine.run_tournament(
        symbol=symbol,
        candles=candles,
        profile=profile,
        strategies=strat_names
    )
    
    print(f"Tournament Finished. Results: {len(results)}")
    for r in results:
        print(f"  - {r.strategy_name}: Score={r.score}, Trades={r.total_trades}")

if __name__ == "__main__":
    asyncio.run(main())
