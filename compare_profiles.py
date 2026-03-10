
import asyncio
import sys
import os

# Ensure backend acts as root for app imports
sys.path.append(os.path.join(os.getcwd(), "backend"))

from app.mt5.client import MT5Client
from app.core.config import Settings

async def main():
    settings = Settings()
    mt5 = MT5Client(settings=settings)
    if not mt5.connect():
        print("Failed to connect to MT5")
        return
        
    symbols = ["XAUUSDm", "XAUUSDc", "XAUUSD", "BTCUSDm", "BTCUSDc", "BTCUSD"]
    print(f"{'Symbol':<10} | {'Size':<8} | {'Point':<8} | {'Digits':<6} | {'Spread':<6}")
    print("-" * 50)
    
    for s in symbols:
        p = mt5.get_symbol_info(s)
        if p:
            print(f"{s:<10} | {p.contract_size:<8} | {p.point:<8} | {p.digits:<6} | {p.spread_avg:<6}")
        else:
            print(f"{s:<10} | {'NOT FOUND':<8}")

if __name__ == "__main__":
    asyncio.run(main())
