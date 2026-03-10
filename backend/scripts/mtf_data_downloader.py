"""
MTF Data Downloader — Download candles for ALL timeframes from MT5.

Downloads OHLCV data for all configured symbols across 6 timeframes:
    M5, M15, M30, H1, H4, D1

Data is saved as Parquet files for efficient re-use in AI training.

Usage:
    python scripts/mtf_data_downloader.py
    python scripts/mtf_data_downloader.py --symbols XAUUSDc,BTCUSDc
    python scripts/mtf_data_downloader.py --output-dir data/exports/mtf

RAM Safety:
    - Downloads one TF at a time (sequential, not parallel)
    - Max 10,000 bars per request
    - Frees DataFrame after saving
"""

import os
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import MetaTrader5 as mt5

# ─── Config ───────────────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["XAUUSDc", "XAGUSDc", "EURUSDc", "USDJPYc", "BTCUSDc"]

TIMEFRAME_CONFIG = {
    # tf_name: (mt5_enum, bar_count, approx_days_description)
    "M2":  (mt5.TIMEFRAME_M2,  300000, "~416 days"),
    "M3":  (mt5.TIMEFRAME_M3,  200000, "~416 days"),
    "M5":  (mt5.TIMEFRAME_M5,  120000, "~416 days"),
    "M12": (mt5.TIMEFRAME_M12,  50000, "~416 days"),
    "M15": (mt5.TIMEFRAME_M15, 40000, "~416 days"),
    "M30": (mt5.TIMEFRAME_M30, 20000, "~416 days"),
    "H1":  (mt5.TIMEFRAME_H1,  15000, "~625 days"),
    "H4":  (mt5.TIMEFRAME_H4,   5000, "~833 days"),
    "D1":  (mt5.TIMEFRAME_D1,   2000, "~2000 days"),
}


def init_mt5() -> bool:
    """Initialize MT5 connection."""
    if not mt5.initialize():
        print(f"[ERROR] MT5 initialization failed: {mt5.last_error()}")
        return False
    
    info = mt5.terminal_info()
    if info is None:
        print("[ERROR] Cannot get terminal info")
        return False
    
    account = mt5.account_info()
    if account:
        print(f"[OK] MT5 connected: Account #{account.login} | Balance: {account.balance}")
    else:
        print("[OK] MT5 connected (no account info)")
    
    return True


def download_candles(symbol: str, tf_name: str, tf_enum: int, count: int) -> pd.DataFrame | None:
    """Download OHLCV candles from MT5."""
    rates = mt5.copy_rates_from_pos(symbol, tf_enum, 0, count)
    
    if rates is None or len(rates) == 0:
        print(f"  [WARN] No data for {symbol} {tf_name}: {mt5.last_error()}")
        return None
    
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    
    # Standardize columns
    if "tick_volume" in df.columns:
        df["volume"] = df["tick_volume"]
    
    # Keep only essential OHLCV columns
    cols_to_keep = ["time", "open", "high", "low", "close", "volume"]
    cols_available = [c for c in cols_to_keep if c in df.columns]
    df = df[cols_available]
    
    df.sort_values("time", inplace=True)
    df.reset_index(drop=True, inplace=True)
    
    return df


def save_parquet(df: pd.DataFrame, output_dir: str, symbol: str, tf_name: str) -> str:
    """Save DataFrame to Parquet file."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{symbol}_{tf_name}.parquet"
    filepath = os.path.join(output_dir, filename)
    df.to_parquet(filepath, index=False, engine="pyarrow")
    return filepath


def download_all(symbols: list[str], output_dir: str) -> dict:
    """Download all TFs for all symbols."""
    results = {
        "success": [],
        "failed": [],
        "total_bars": 0,
        "total_files": 0,
    }
    
    total_tasks = len(symbols) * len(TIMEFRAME_CONFIG)
    current = 0
    
    for symbol in symbols:
        print(f"\n{'='*60}")
        print(f"  Symbol: {symbol}")
        print(f"{'='*60}")
        
        # Check if symbol is available
        sym_info = mt5.symbol_info(symbol)
        if sym_info is None:
            print(f"  [ERROR] Symbol {symbol} not found in MT5")
            for tf_name in TIMEFRAME_CONFIG:
                results["failed"].append(f"{symbol}_{tf_name}")
                current += 1
            continue
        
        if not sym_info.visible:
            mt5.symbol_select(symbol, True)
            time.sleep(0.5)
        
        for tf_name, (tf_enum, count, desc) in TIMEFRAME_CONFIG.items():
            current += 1
            progress = f"[{current}/{total_tasks}]"
            
            print(f"  {progress} Downloading {tf_name} ({count} bars, {desc})...", end=" ")
            
            df = download_candles(symbol, tf_name, tf_enum, count)
            
            if df is None or df.empty:
                results["failed"].append(f"{symbol}_{tf_name}")
                print("FAILED")
                continue
            
            filepath = save_parquet(df, output_dir, symbol, tf_name)
            bars = len(df)
            date_range = f"{df['time'].iloc[0].strftime('%Y-%m-%d')} → {df['time'].iloc[-1].strftime('%Y-%m-%d')}"
            
            results["success"].append(f"{symbol}_{tf_name}")
            results["total_bars"] += bars
            results["total_files"] += 1
            
            size_kb = os.path.getsize(filepath) / 1024
            print(f"OK | {bars:,} bars | {date_range} | {size_kb:.0f} KB")
            
            # Free memory
            del df
    
    return results


def main():
    parser = argparse.ArgumentParser(description="MTF Data Downloader — Download all TF candles from MT5")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbols (default: all from .env)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory for Parquet files")
    args = parser.parse_args()
    
    # Determine symbols
    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS
    
    # Determine output dir
    output_dir = args.output_dir or os.path.join(
        os.path.dirname(__file__), "..", "data", "exports", "mtf"
    )
    output_dir = os.path.abspath(output_dir)
    
    print("=" * 60)
    print("  MTF Data Downloader")
    print("=" * 60)
    print(f"  Symbols:    {', '.join(symbols)}")
    print(f"  Timeframes: {', '.join(TIMEFRAME_CONFIG.keys())}")
    print(f"  Output:     {output_dir}")
    print(f"  Time:       {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)
    
    # Initialize MT5
    if not init_mt5():
        sys.exit(1)
    
    try:
        t0 = time.time()
        results = download_all(symbols, output_dir)
        elapsed = time.time() - t0
        
        # Summary
        print(f"\n{'='*60}")
        print(f"  DOWNLOAD COMPLETE")
        print(f"{'='*60}")
        print(f"  Files saved:  {results['total_files']}")
        print(f"  Total bars:   {results['total_bars']:,}")
        print(f"  Failed:       {len(results['failed'])}")
        print(f"  Time:         {elapsed:.1f}s")
        print(f"  Output dir:   {output_dir}")
        
        if results["failed"]:
            print(f"\n  Failed downloads:")
            for f in results["failed"]:
                print(f"    - {f}")
        
        print(f"{'='*60}")
        
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
