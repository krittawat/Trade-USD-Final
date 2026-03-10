import asyncio
import logging
import json
from datetime import datetime, timezone
import os
import sys

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.logging import get_logger
from crypto_oracle.ws_client import BinanceWSClient
from crypto_oracle.models import LiquidityIntelligenceEngine, SignalResult

logger = get_logger("backtest_runner", level="DEBUG")

async def run_live_test():
    """
    Connects to the live WebSocket feed for 60 seconds.
    Prints out the Liquidity Heatmap estimates and any signals generated.
    """
    logger.info("Initializing BTC Liquidity Intelligence Engine...")
    engine = LiquidityIntelligenceEngine()
    ws_client = BinanceWSClient("btcusdt")
    ws_client.add_callback(engine.update_from_stream)
    
    # Start WS in background
    ws_task = asyncio.create_task(ws_client.start())
    
    logger.info("Waiting 5 seconds for data to populate...")
    await asyncio.sleep(5)
    
    for i in range(12): # Run for ~60 seconds
        state = engine.state
        logger.info("="*50)
        logger.info(f"Time: {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
        logger.info(f"Mark Price: {state.mark_price:.2f}")
        logger.info(f"Funding Rate: {state.funding_rate:.6%}")
        logger.info(f"CVD: {state.cvd:.2f}")
        
        clusters = engine.estimate_liquidation_clusters()
        shorts = clusters["short_liqs"]
        longs = clusters["long_liqs"]
        
        logger.info("--- TOP 3 SHORT LIQUIDATION CLUSTERS (ABOVE PRICE) ---")
        for idx, c in enumerate(shorts[:3]):
            logger.info(f"  {idx+1}. {c['price']:.2f} (Weight: {c['weight']:.2f})")

        logger.info("--- TOP 3 LONG LIQUIDATION CLUSTERS (BELOW PRICE) ---")
        for idx, c in enumerate(longs[:3]):
            logger.info(f"  {idx+1}. {c['price']:.2f} (Weight: {c['weight']:.2f})")
            
        signal: SignalResult = engine.detect_squeeze()
        print(f"\nAI SIGNAL: {signal.signal} | Confidence: {signal.confidence_score}%")
        print(f"Reason: {signal.reason}")
        if signal.signal != "NEUTRAL":
            print(f"Entry: {signal.entry_price} | Target: {signal.target_liquidity_zone} | SL: {signal.stop_loss}\n")
            
        await asyncio.sleep(5)
        
    logger.info("Test completed. Stopping client...")
    ws_client.stop()
    await ws_task

if __name__ == "__main__":
    try:
        asyncio.run(run_live_test())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
