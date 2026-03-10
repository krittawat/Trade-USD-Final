import asyncio
import sys
sys.path.append('backend')
from app.core.config import get_settings
from app.mt5.client import MT5Client
from app.mt5.market_data import fetch_candles

async def main():
    settings = get_settings()
    client = MT5Client(settings)
    client.connect()
    print("MT5 Connected:", client.is_connected())
    
    # Test fetch_candles in a separate thread
    df = await asyncio.to_thread(fetch_candles, "XAUUSDc", "M5", 10)
    if df is not None:
        print("Success:", len(df), "candles fetched.")
    else:
        print("Failed: No candles returned or MT5 fetch failed.")
        
if __name__ == '__main__':
    asyncio.run(main())
