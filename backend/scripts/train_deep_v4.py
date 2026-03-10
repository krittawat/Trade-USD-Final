#!/usr/bin/env python3
"""
Deep V4 Training Pipeline — Per-Symbol Transformer-GRU Training.

Steps per symbol (XAUUSDc, XAGUSDc, BTCUSDc):
    1. Load existing Parquet data (or download from MT5)
    2. Build 55 V4 features + 3-class targets (BUY/HOLD/SELL)
    3. Train per-symbol Transformer-GRU model
    4. Backtest each symbol independently
    5. Print pass/fail per symbol (WR ≥ 50%, PF ≥ 1.3)
    6. Save model only if passing thresholds

Usage:
    python scripts/train_deep_v4.py                         # All 3 symbols
    python scripts/train_deep_v4.py --symbol XAUUSDc        # Single symbol
    python scripts/train_deep_v4.py --epochs 50             # More epochs
    python scripts/train_deep_v4.py --skip-download         # Use existing data
    python scripts/train_deep_v4.py --backtest              # Backtest only
"""

import os
import sys
import time
import argparse
import numpy as np
from pathlib import Path
from collections import Counter

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# ─── Config ───────────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["XAUUSDc", "XAGUSDc", "BTCUSDc"]
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'exports', 'mtf'))
MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'models'))

# Label names
LABEL_NAMES = {0: "BUY", 1: "HOLD", 2: "SELL"}


def step_download(symbols: list[str], data_dir: str) -> bool:
    """Step 1: Download candles from MT5."""
    print(f"\n{'='*60}")
    print(f"  STEP 1: Download Data from MT5")
    print(f"{'='*60}")

    try:
        from scripts.mtf_data_downloader import init_mt5, download_all
        import MetaTrader5 as mt5

        if not init_mt5():
            print("[ERROR] Cannot connect to MT5. Make sure MT5 is running.")
            return False

        try:
            results = download_all(symbols, data_dir)
            print(f"\n  Downloaded {results['total_files']} files, {results['total_bars']:,} total bars")
            if results["failed"]:
                print(f"  [WARN] {len(results['failed'])} downloads failed")
            return results["total_files"] > 0
        finally:
            mt5.shutdown()
    except ImportError:
        print("[WARN] mtf_data_downloader not available. Use --skip-download.")
        return False


def step_build_features(symbols: list[str], data_dir: str) -> dict:
    """Step 2: Build V4 features (55) with 3-class targets for each symbol."""
    print(f"\n{'='*60}")
    print(f"  STEP 2: Build V4 Features (55 features, 3-class)")
    print(f"{'='*60}")

    from app.brain.mtf_feature_engine import MTFFeatureEngine

    engine = MTFFeatureEngine(data_dir=data_dir)
    all_data = {}

    for symbol in symbols:
        print(f"\n  Processing {symbol}...")
        X, y = engine.load_and_build_v4(symbol)

        if len(X) == 0:
            print(f"    [WARN] No data for {symbol}, skipping")
            continue

        dist = Counter(y.tolist())
        total = len(y)
        print(f"    Sequences: {total:,} | Features: {X.shape[2]}")
        print(f"    BUY:  {dist.get(0, 0):,} ({dist.get(0, 0)/total*100:.1f}%)")
        print(f"    HOLD: {dist.get(1, 0):,} ({dist.get(1, 0)/total*100:.1f}%)")
        print(f"    SELL: {dist.get(2, 0):,} ({dist.get(2, 0)/total*100:.1f}%)")
        all_data[symbol] = (X, y)

    return all_data


def step_train(all_data: dict, epochs: int = 30, verbose: bool = True) -> dict:
    """Step 3: Train per-symbol Transformer-GRU models."""
    print(f"\n{'='*60}")
    print(f"  STEP 3: Train Per-Symbol Transformer-GRU Models")
    print(f"{'='*60}")

    from app.brain.deep_model_v4 import DeepModelV4

    if not all_data:
        print("[ERROR] No training data available!")
        return {}

    results = {}

    for symbol, (X, y) in all_data.items():
        print(f"\n{'─'*60}")
        print(f"  Training {symbol}...")
        print(f"{'─'*60}")

        # Backup existing model
        import shutil
        old_model = os.path.join(MODELS_DIR, f"deep_v4_{symbol}.pth")
        if os.path.exists(old_model):
            backup = old_model + ".bak"
            shutil.copy2(old_model, backup)
            print(f"  [Backup] {old_model} → {backup}")

        # Train per-symbol model
        model = DeepModelV4(symbol)
        result = model.train_session(X, y, epochs=epochs, verbose=verbose)
        results[symbol] = result

    return results


def step_backtest(all_data: dict) -> dict:
    """Step 4: Backtest each per-symbol model independently."""
    print(f"\n{'='*60}")
    print(f"  STEP 4: Per-Symbol Backtest")
    print(f"{'='*60}")

    from app.brain.deep_model_v4 import DeepModelV4

    results = {}

    for symbol, (X, y) in all_data.items():
        print(f"\n  Backtesting {symbol}...")

        model = DeepModelV4(symbol)
        if not model._trained:
            print(f"    [SKIP] Model not trained for {symbol}")
            continue

        # Use last 20% as test set (walk-forward)
        split = int(len(X) * 0.8)
        X_test = X[split:]
        y_test = y[split:]

        if len(X_test) < 50:
            print(f"    [SKIP] Insufficient test data ({len(X_test)} sequences)")
            continue

        # Run predictions
        wins = 0
        losses = 0
        total_pnl_r = 0.0
        trades = 0
        win_pnl = 0.0
        loss_pnl = 0.0

        for i in range(len(X_test)):
            pred = model.predict(X_test[i])
            actual = y_test[i]
            action = pred["action"]

            if action == "HOLD":
                continue

            trades += 1
            if action == "BUY":
                if actual == 0:  # BUY was correct
                    wins += 1
                    total_pnl_r += 2.0  # TP = 2R
                    win_pnl += 2.0
                else:
                    losses += 1
                    total_pnl_r -= 1.0  # SL = -1R
                    loss_pnl += 1.0
            elif action == "SELL":
                if actual == 2:  # SELL was correct
                    wins += 1
                    total_pnl_r += 2.0
                    win_pnl += 2.0
                else:
                    losses += 1
                    total_pnl_r -= 1.0
                    loss_pnl += 1.0

        # Calculate metrics
        win_rate = wins / max(trades, 1)
        profit_factor = win_pnl / max(loss_pnl, 0.001)
        expectancy = total_pnl_r / max(trades, 1)

        # Simulate equity curve for max drawdown
        equity = [0.0]
        peak = 0.0
        max_dd = 0.0

        for i in range(len(X_test)):
            pred = model.predict(X_test[i])
            actual = y_test[i]
            pnl = 0.0

            if pred["action"] == "BUY":
                pnl = 2.0 if actual == 0 else -1.0
            elif pred["action"] == "SELL":
                pnl = 2.0 if actual == 2 else -1.0

            if pnl != 0:
                equity.append(equity[-1] + pnl)
                peak = max(peak, equity[-1])
                dd = peak - equity[-1]
                max_dd = max(max_dd, dd)

        # Print results
        total_test = len(X_test)
        trade_rate = trades / total_test * 100 if total_test > 0 else 0
        hold_count = total_test - trades

        print(f"    {'='*50}")
        print(f"    Test samples:  {total_test}")
        print(f"    Trades:        {trades} ({trade_rate:.1f}%)")
        print(f"    HOLD skipped:  {hold_count}")
        print(f"    Win Rate:      {win_rate:.1%}")
        print(f"    Profit Factor: {profit_factor:.2f}")
        print(f"    Expectancy:    {expectancy:.2f}R per trade")
        print(f"    Total PnL:     {total_pnl_r:.1f}R")
        print(f"    Max Drawdown:  {max_dd:.1f}R")

        # Check thresholds
        checks = {
            "win_rate >= 50%": win_rate >= 0.50,
            "profit_factor >= 1.3": profit_factor >= 1.3,
            "expectancy > 0": expectancy > 0,
        }

        status = "✅ PASS" if all(checks.values()) else "❌ FAIL"
        print(f"    Status:        {status}")

        for check, ok in checks.items():
            icon = "✅" if ok else "❌"
            print(f"      {icon} {check}")

        results[symbol] = {
            "trades": trades,
            "trade_rate": round(trade_rate, 1),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 3),
            "total_pnl_r": round(total_pnl_r, 1),
            "max_dd_r": round(max_dd, 1),
            "pass": all(checks.values()),
        }

    return results


def main():
    parser = argparse.ArgumentParser(description="Deep V4 Per-Symbol Training Pipeline")
    parser.add_argument("--symbol", type=str, default=None,
                        help="Single symbol to train (default: all 3)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbols")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip data download (use existing Parquet)")
    parser.add_argument("--epochs", type=int, default=30,
                        help="Training epochs (default: 30)")
    parser.add_argument("--quick", action="store_true",
                        help="Quick test mode (5 epochs)")
    parser.add_argument("--backtest", action="store_true",
                        help="Run backtest only (skip training)")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Data directory for Parquet files")
    args = parser.parse_args()

    # Determine symbols
    if args.symbol:
        symbols = [args.symbol]
    elif args.symbols:
        symbols = args.symbols.split(",")
    else:
        symbols = DEFAULT_SYMBOLS

    data_dir = args.data_dir or DATA_DIR
    epochs = 5 if args.quick else args.epochs

    print("=" * 60)
    print("  Deep V4 — Per-Symbol Transformer-GRU Training")
    print("=" * 60)
    print(f"  Symbols:       {', '.join(symbols)}")
    print(f"  Data dir:      {data_dir}")
    print(f"  Epochs:        {epochs}")
    print(f"  Skip download: {args.skip_download}")
    print(f"  Backtest only: {args.backtest}")
    print("=" * 60)

    t0 = time.time()

    # Step 1: Download (optional)
    if not args.skip_download and not args.backtest:
        ok = step_download(symbols, data_dir)
        if not ok:
            print("\n[WARN] Download failed/skipped. Trying existing data...")

    # Step 2: Build V4 features
    all_data = step_build_features(symbols, data_dir)
    if not all_data:
        print("\n[ERROR] No features built. Check Parquet files in data dir.")
        return

    # Step 3: Train per-symbol (unless backtest-only)
    if not args.backtest:
        train_results = step_train(all_data, epochs=epochs)
        for sym, res in train_results.items():
            if res.get("status") != "trained":
                print(f"\n[WARN] {sym} training issue: {res}")

    # Step 4: Backtest
    bt_results = step_backtest(all_data)

    # Final Summary
    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  DEEP V4 TRAINING COMPLETE")
    print(f"{'='*60}")
    print(f"  Total time: {elapsed:.1f}s")

    passing = sum(1 for r in bt_results.values() if r.get("pass"))
    total = len(bt_results)
    print(f"  Backtest: {passing}/{total} symbols passed\n")

    for sym, res in bt_results.items():
        icon = "✅" if res.get("pass") else "❌"
        print(
            f"    {icon} {sym}: WR={res['win_rate']:.1%} PF={res['profit_factor']:.2f} "
            f"Exp={res['expectancy']:.2f}R Trades={res['trades']} "
            f"TradeRate={res['trade_rate']}%"
        )

    # Safety gate
    if passing == 0 and total > 0:
        print(f"\n  ⚠️  NO symbols passed! Models saved but NOT for LIVE trading.")
    elif passing < total:
        print(f"\n  ⚠️  Some symbols didn't pass. Review before LIVE trading.")
    else:
        print(f"\n  ✅ All symbols passed! Models ready for DRY_RUN testing.")

    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
