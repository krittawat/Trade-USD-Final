import os
import sys
import time
import asyncio
from datetime import datetime
import MetaTrader5 as mt5

# Add project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.core.config import get_settings
from app.db.sqlite import SQLiteStore
from app.core.logging import get_logger
from app.mt5.market_data import fetch_ticks_since

logger = get_logger("tick_recorder")

def print_banner():
    print("=" * 60)
    print("🚀 ANTIGRAVITY TICK RECORDER (SQLITE NATIVE)")
    print("   Capturing precise Bid/Ask + Real Tick Volume")
    print("=" * 60)

async def main():
    settings = get_settings()
    
    # 1. Start MT5
    if not mt5.initialize(
        path=settings.mt5_path,
        login=settings.mt5_login,
        password=settings.mt5_password,
        server=settings.mt5_server,
        timeout=settings.mt5_timeout
    ):
        print(f"❌ Failed to connect to MT5: {mt5.last_error()}")
        return

    print("✅ MT5 Connected Successfully")

    # 2. Start SQLite 
    db = SQLiteStore(settings)
    db.connect()
    print(f"✅ SQLite Connected: {settings.sqlite_db_path}")

    # Load symbols
    symbols_str = os.getenv("TRADING_SYMBOLS", "XAUUSDc,BTCUSDc")
    symbols = [s.strip() for s in symbols_str.split(",") if s.strip()]
    
    print(f"📡 Tracking Symbols: {', '.join(symbols)}")
    print_banner()

    last_times = {sym: int(time.time() * 1000) - 5000 for sym in symbols}
    
    try:
        while True:
            total_ticks_cycle = 0
            for sym in symbols:
                try:
                    # MT5 sometimes requires mapped symbols depending on account suffix
                    # Assume proper names are passed in .env
                    ticks = fetch_ticks_since(sym, last_times[sym])
                    
                    if ticks:
                        # 3. Store to SQLite!
                        db.ingest_ticks(sym, ticks)
                        
                        max_ts = max(t['time'] for t in ticks)
                        last_times[sym] = int(max_ts * 1000)
                        
                        # Calculate total volume for console output
                        total_vol = sum(t['volume'] for t in ticks)
                        
                        # Grab the latest tick for display
                        latest = ticks[-1]
                        dt_str = datetime.fromtimestamp(latest['time']).strftime('%H:%M:%S.%f')[:-3]
                        
                        print(f"[{dt_str}] {sym:<10} | Bid: {latest['bid']:.3f} | Ask: {latest['ask']:.3f} | Ticks: {len(ticks)} | Vol: {total_vol}")
                        
                        total_ticks_cycle += len(ticks)
                        
                except Exception as e:
                    print(f"⚠️ Error tracking {sym}: {e}")

            if total_ticks_cycle == 0:
                # Sleep a bit longer if no ticks
                await asyncio.sleep(0.5)
            else:
                # Yield to event loop, minimal delay
                await asyncio.sleep(0.05)
                
    except KeyboardInterrupt:
        print("\n🛑 Tick Recorder Stopped.")
    finally:
        mt5.shutdown()
        db.disconnect()
        print("✅ Gracefully shut down DB & MT5.")

if __name__ == "__main__":
    asyncio.run(main())
