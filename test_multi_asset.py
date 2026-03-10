
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

async def run_diagnostic(symbol: str, orchestrator: TrainingOrchestrator, factory: StrategyFactory):
    print(f"\n--- Diagnostic for {symbol} ---")
    
    # Fetch 5000 candles for stability check
    candles = orchestrator._fetch_historical_candles(symbol)
    if candles is None or len(candles) == 0:
        print(f"Failed to fetch candles for {symbol}")
        return
        
    candles = candles.iloc[-5000:]
    print(f"Loaded {len(candles)} candles for {symbol}")
    
    # Run tournament for top 3 strategies to keep it fast
    strat_names = list(factory._strategies.keys())[:3]
    print(f"Testing strategies on {symbol}: {strat_names}")
    
    profile = orchestrator._get_profile(symbol)
    
    try:
        results = await orchestrator.practice_engine.run_tournament(
            symbol=symbol,
            candles=candles,
            profile=profile,
            strategies=strat_names
        )
        
        print(f"Tournament results for {symbol}:")
        for res in results:
            print(f"  - {res.strategy_name}: Score={res.score:.4f}, Trades={res.total_trades}, PF={res.profit_factor:.2f}")
    except Exception as e:
        print(f"Error running tournament for {symbol}: {e}")

async def main():
    print("Starting Multi-Asset Diagnostic...")
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
        memory.close()
        db.disconnect()
        return
        
    factory = StrategyFactory()
    factory.auto_register(db=db)
    
    orchestrator = TrainingOrchestrator(
        factory=factory,
        memory_store=memory,
        mt5_client=mt5,
        settings=settings
    )
    
    symbols = ["BTCUSDm", "USOILm", "USTECm", "US30m"]
    
    for symbol in symbols:
        await run_diagnostic(symbol, orchestrator, factory)

    mt5.disconnect()
    db.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
