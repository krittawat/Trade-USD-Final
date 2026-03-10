"""
Download 2 years of data for specified symbols and timeframes.
"""
import os
import sys
import time
from datetime import datetime, timezone
import pandas as pd
import MetaTrader5 as mt5

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

SYMBOLS = ["XAUUSDc", "XAGUSDc", "XAUUSD", "XAGUSD"]

TIMEFRAME_CONFIG = {
    "M1":  (mt5.TIMEFRAME_M1,  750000),
    "M5":  (mt5.TIMEFRAME_M5,  150000),
    "M15": (mt5.TIMEFRAME_M15, 50000),
    "H1":  (mt5.TIMEFRAME_H1,  15000),
    "H4":  (mt5.TIMEFRAME_H4,  4000),
}


def init_mt5() -> bool:
    if not mt5.initialize():
        print(f"[ERROR] MT5 initialization failed: {mt5.last_error()}")
        return False
    return True

def download_candles(symbol: str, tf_name: str, tf_enum: int, count: int) -> pd.DataFrame | None:
    rates = mt5.copy_rates_from_pos(symbol, tf_enum, 0, count)
    if rates is None or len(rates) == 0:
        # Retry with just the base symbol if it doesn't have suffix
        return None
    
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    if "tick_volume" in df.columns:
        df["volume"] = df["tick_volume"]
    
    cols_to_keep = ["time", "open", "high", "low", "close", "volume", "spread"]
    cols_available = [c for c in cols_to_keep if c in df.columns]
    df = df[cols_available]
    
    df.sort_values("time", inplace=True)
    df.reset_index(drop=True, inplace=True)
    
    return df

def main():
    if not init_mt5():
        sys.exit(1)
        
    output_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "exports", "mtf"))
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        found_symbols = []
        # First resolve available symbols from Exness since they might have 'c', 'm', or no suffix
        for sym in SYMBOLS:
            info = mt5.symbol_info(sym)
            if info is not None:
                found_symbols.append(sym)
                
        # Deduplicate to process only valid ones
        if "XAUUSDc" in found_symbols and "XAUUSD" in found_symbols:
            found_symbols.remove("XAUUSD")
        if "XAGUSDc" in found_symbols and "XAGUSD" in found_symbols:
            found_symbols.remove("XAGUSD")

        for symbol in found_symbols:
            print(f"\nProcessing {symbol}...")
            
            sym_info = mt5.symbol_info(symbol)
            if sym_info is None:
                print(f"  [ERROR] Symbol {symbol} not found in MT5")
                continue
                
            if not sym_info.visible:
                mt5.symbol_select(symbol, True)
                time.sleep(0.5)
                
            for tf_name, (tf_enum, count) in TIMEFRAME_CONFIG.items():
                print(f"  Downloading {tf_name} ({count} bars)...", end=" ", flush=True)
                df = download_candles(symbol, tf_name, tf_enum, count)
                if df is None or df.empty:
                    print("FAILED")
                    continue
                    
                filename = f"{symbol}_{tf_name}.parquet"
                filepath = os.path.join(output_dir, filename)
                df.to_parquet(filepath, index=False, engine="pyarrow")
                
                bars = len(df)
                start_date = df['time'].iloc[0].strftime('%Y-%m-%d')
                end_date = df['time'].iloc[-1].strftime('%Y-%m-%d')
                size_kb = os.path.getsize(filepath) / 1024
                
                print(f"OK | {bars:,} bars | {start_date} -> {end_date} | {size_kb:.0f} KB")
                del df
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()
