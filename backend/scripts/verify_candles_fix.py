import asyncio
import os
import sys
from datetime import datetime

# Adjust path to include backend
sys.path.append(os.path.abspath("backend"))

from app.core.config import get_settings
from app.mt5.client import MT5Client
from app.mt5.market_data import fetch_candles
from app.core.mode import TradingMode

async def verify_fetching():
    settings = get_settings()
    client = MT5Client(settings)
    
    print(f"Connecting to MT5 {settings.mt5_server}...")
    if not client.connect():
        print("Failed to connect to MT5")
        return

    # Check symbol mapping
    symbol = "XAGUSD" # Internal
    broker_symbol = client.adapter.map_symbol(symbol)
    print(f"Mapped {symbol} -> {broker_symbol}")
    
    # Try fetch candles using broker symbol (like MasterLoop now does)
    print(f"Fetching candles for {broker_symbol}...")
    candles = fetch_candles(broker_symbol, timeframe="M5", count=10)
    
    if candles is not None and not candles.empty:
        print(f"SUCCESS: Fetched {len(candles)} candles for {broker_symbol}")
        print(candles.tail(1))
    else:
        print(f"FAILURE: No candles for {broker_symbol}")
        
    client.disconnect()

if __name__ == "__main__":
    asyncio.run(verify_fetching())
