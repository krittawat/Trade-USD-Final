import asyncio
import os
import sys
import pandas as pd
from datetime import datetime
import MetaTrader5 as mt5

# Add project paths
ROOT = "d:/VibeCode/Trade"
BACKEND = os.path.join(ROOT, "backend")
sys.path.append(ROOT)
sys.path.append(BACKEND)

from app.core.config import Settings
from app.core.logging import get_logger
from app.mt5.client import MT5Client
from app.brain.practice_engine import PracticeEngine
from app.brain.training_orchestrator import TrainingOrchestrator
from app.brain.memory_store import MemoryStore
from app.db.sqlite import SQLiteStore
from app.strategy.factory import StrategyFactory

logger = get_logger("backtest_180d")

MAX_CANDLES = 60000  # 180 days at M5
TARGET_SYMBOLS = ["XAUUSDm", "BTCUSDm", "USOILm", "US30m", "USTECm"]
GO_LIVE_STRATEGIES = ["alpha_v6_smc", "opus_liquidity_hunter", "gold_evolution"]

# Institutional Gates (HARDENED 2026-03-08)
MIN_PF = 1.20     # Profit Factor ≥ 1.20
MAX_DD = 20.0     # Max Drawdown < 20%
MIN_WR = 53.0     # Win Rate > 53%
MIN_TRADES = 50   # Statistical significance

async def run_institutional_backtest():
    """Runs a 180-day backtest for all target symbols."""
    print("\n" + "="*60)
    print("🚀 ANTIGRAVITY INSTITUTIONAL GO-LIVE VERIFICATION (180 DAYS)")
    print("="*60)
    
    settings = Settings()
    
    # 1. Connect
    mt5_client = MT5Client(settings=settings)
    if not mt5_client.connect():
        print("❌ Failed to connect to MT5")
        return

    # Database & Memory
    db_store = SQLiteStore(settings=settings)
    db_store.connect()
    memory = MemoryStore()
    memory.connect()
    
    # Factory & Orchestrator
    factory = StrategyFactory()
    factory.auto_register(db=db_store)
    
    orchestrator = TrainingOrchestrator(
        factory=factory,
        memory_store=memory,
        mt5_client=mt5_client,
        settings=settings
    )
    
    all_results = []
    
    for symbol in TARGET_SYMBOLS:
        try:
            print(f"\nProcessing {symbol} (Target: {MAX_CANDLES} candles)...")
            
            # Fetch Data via Orchestrator (it handles mapping/fetching)
            # Use MT5Client's get_historical_candles if we want more control,
            # but TrainingOrchestrator._fetch_historical_candles is more integrated.
            candles_rates = mt5_client.get_historical_candles(symbol, "M5", MAX_CANDLES)
            if candles_rates is None or len(candles_rates) < 1000:
                print(f"⚠️ Insufficient data for {symbol}")
                continue
            
            # Convert to DataFrame
            df = pd.DataFrame(candles_rates)
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
            df.set_index('time', inplace=True)
            df.sort_index(inplace=True)
            print(f"  - Loaded {len(df)} candles.")
            
            # Get Profile
            profile = orchestrator._get_profile(symbol)
            if not profile:
                print(f"⚠️ Failed to get profile for {symbol}")
                continue
            
            # Run Tournament
            available_strats = [s for s in GO_LIVE_STRATEGIES if s in factory._strategies]
            
            print(f"  - Testing {len(available_strats)} strategies...")
            results = []
            for name in available_strats:
                print(f"    > Running {name}...", end="", flush=True)
                res = await orchestrator.practice_engine.run_practice(
                    symbol=symbol,
                    strategy_name=name,
                    candles=df,
                    profile=profile
                )
                results.append(res)
                print(f" Done. (Trades={res.total_trades}, PF={res.profit_factor:.2f})")
            
            # Analyze & Print
            print(f"\n  Final Results for {symbol}:")
            for res in results:
                wr = (res.win_rate * 100)
                pf = res.profit_factor
                dd = res.max_drawdown
                trades = res.total_trades
                
                # Check Gate
                passed_pf = pf >= MIN_PF
                passed_dd = dd <= MAX_DD
                passed_wr = wr > MIN_WR
                passed_trades = trades >= MIN_TRADES
                passed = passed_pf and passed_dd and passed_wr and passed_trades
                
                pf_icon = "✅" if passed_pf else "❌"
                dd_icon = "✅" if passed_dd else "❌"
                wr_icon = "✅" if passed_wr else "❌"
                status = "✅ PASS" if passed else "❌ FAIL"
                
                print(f"    - [{status}] {res.strategy_name:20}: PF={pf:4.2f}{pf_icon} DD={dd:4.2f}%{dd_icon} WR={wr:4.1f}%{wr_icon} Trades={trades}")
                
                all_results.append({
                    "symbol": symbol,
                    "strategy": res.strategy_name,
                    "pf": pf,
                    "dd": dd,
                    "trades": trades,
                    "passed": passed
                })
                
        except Exception as e:
            print(f"  ❌ Error for {symbol}: {e}")
            # logger.error(f"Error for {symbol}", exc_info=True)

    # Final Summary
    print("\n" + "="*60)
    print("🏁 FINAL GO-LIVE SUMMARY")
    print("="*60)
    pass_count = sum(1 for r in all_results if r['passed'])
    print(f"Total Tests: {len(all_results)}")
    print(f"Passed Gates: {pass_count}")
    
    if pass_count > 0:
        print("\n🏆 SYSTEM READY FOR GO-LIVE ON PASSED SYMBOLS")
    else:
        print("\n⚠️ SYSTEM NEEDS MORE TUNING BEFORE LIVE DEPLOYMENT")
    
    print("="*60 + "\n")
    
    # Cleanup
    mt5_client.disconnect()

if __name__ == "__main__":
    asyncio.run(run_institutional_backtest())
