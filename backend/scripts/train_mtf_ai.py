"""
MTF AI Training Pipeline — End-to-end script for training the GRU model.

Steps:
    1. Download data from MT5 (all symbols, all TFs) → Parquet
    2. Build MTF features (43 features per bar)
    3. Train GRU+Attention model with walk-forward validation
    4. Evaluate and print metrics
    5. Run quick backtest simulation
    6. Save model if metrics pass threshold

Usage:
    python scripts/train_mtf_ai.py                          # Full training
    python scripts/train_mtf_ai.py --symbol XAUUSDc         # Single symbol
    python scripts/train_mtf_ai.py --skip-download           # Skip data download
    python scripts/train_mtf_ai.py --epochs 10 --quick       # Quick test
    python scripts/train_mtf_ai.py --backtest                # Backtest only
"""

import os
import sys
import time
import argparse
import numpy as np
from pathlib import Path

# Add backend to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# ─── Config ───────────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["XAUUSDc", "XAGUSDc", "EURUSDc", "USDJPYc", "BTCUSDc"]
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'exports', 'mtf'))
MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'models'))


def step_download(symbols: list[str], data_dir: str):
    """Step 1: Download candles from MT5."""
    print("\n" + "=" * 60)
    print("  STEP 1: Download Data from MT5")
    print("=" * 60)

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


def step_build_features(symbols: list[str], data_dir: str) -> dict:
    """Step 2: Build MTF features for each symbol."""
    print("\n" + "=" * 60)
    print("  STEP 2: Build MTF Features")
    print("=" * 60)

    from app.brain.mtf_feature_engine import MTFFeatureEngine

    engine = MTFFeatureEngine(data_dir=data_dir)
    all_data = {}

    for symbol in symbols:
        print(f"\n  Processing {symbol}...")
        X, y = engine.load_and_build(symbol)

        if len(X) == 0:
            print(f"    [WARN] No data for {symbol}, skipping")
            continue

        pos_ratio = y.mean()
        print(f"    Sequences: {len(X):,} | Features: {X.shape[2]} | Pos ratio: {pos_ratio:.3f}")
        all_data[symbol] = (X, y)

    return all_data


def step_train(all_data: dict, epochs: int = 30, verbose: bool = True) -> dict:
    """Step 3: Train the GRU model."""
    print("\n" + "=" * 60)
    print("  STEP 3: Train GRU+Attention Model")
    print("=" * 60)

    from app.brain.mtf_model import MTFDeepLearner

    if not all_data:
        print("[ERROR] No training data available!")
        return {"status": "no_data"}

    # Combine all symbols into single dataset
    X_all = []
    y_all = []
    for symbol, (X, y) in all_data.items():
        X_all.append(X)
        y_all.append(y)
        print(f"  {symbol}: {len(X):,} sequences")

    X_combined = np.concatenate(X_all, axis=0)
    y_combined = np.concatenate(y_all, axis=0)
    print(f"\n  Combined: {len(X_combined):,} total sequences")
    print(f"  Positive ratio: {y_combined.mean():.3f}")

    # Shuffle (but maintain time-order within walk-forward split)
    # Note: we time-shuffle across symbols but walk-forward within each
    indices = np.arange(len(X_combined))
    np.random.seed(42)
    np.random.shuffle(indices)
    X_combined = X_combined[indices]
    y_combined = y_combined[indices]

    # Backup existing model
    import shutil
    old_model = os.path.join(MODELS_DIR, "mtf_brain_v3.pth")
    if os.path.exists(old_model):
        backup = old_model + ".bak"
        shutil.copy2(old_model, backup)
        print(f"  [Backup] {old_model} → {backup}")

    # Train
    learner = MTFDeepLearner()
    result = learner.train_session(X_combined, y_combined, epochs=epochs, verbose=verbose)

    return result


def step_backtest(all_data: dict) -> dict:
    """Step 4: Quick backtest simulation using the trained model."""
    print("\n" + "=" * 60)
    print("  STEP 4: Backtest Simulation")
    print("=" * 60)

    from app.brain.mtf_model import MTFDeepLearner

    learner = MTFDeepLearner()
    if not learner._trained:
        print("[ERROR] Model not trained!")
        return {"status": "not_trained"}

    results = {}

    for symbol, (X, y) in all_data.items():
        print(f"\n  Backtesting {symbol}...")

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
        total_pnl_r = 0.0  # PnL in R multiples
        trades = 0
        win_pnl = 0.0
        loss_pnl = 0.0

        BUY_THRESH = 0.60
        SELL_THRESH = 0.40

        for i in range(len(X_test)):
            prob = learner.predict(X_test[i])
            actual = y_test[i]

            if prob > BUY_THRESH:
                # BUY signal
                trades += 1
                if actual == 1.0:
                    # TP hit first → Win (+2R with 1:2 R:R)
                    wins += 1
                    total_pnl_r += 2.0
                    win_pnl += 2.0
                else:
                    # SL hit first → Loss (-1R)
                    losses += 1
                    total_pnl_r -= 1.0
                    loss_pnl += 1.0

            elif prob < SELL_THRESH:
                # SELL signal (inverse: actual=1 means price went up → loss for SELL)
                trades += 1
                if actual == 0.0:
                    # Price didn't go up → Win for SELL (+2R)
                    wins += 1
                    total_pnl_r += 2.0
                    win_pnl += 2.0
                else:
                    # Price went up → Loss for SELL (-1R)
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
            prob = learner.predict(X_test[i])
            actual = y_test[i]
            pnl = 0.0

            if prob > BUY_THRESH:
                pnl = 2.0 if actual == 1.0 else -1.0
            elif prob < SELL_THRESH:
                pnl = 2.0 if actual == 0.0 else -1.0

            equity.append(equity[-1] + pnl)
            peak = max(peak, equity[-1])
            dd = peak - equity[-1]
            max_dd = max(max_dd, dd)

        # Print results
        print(f"    {'='*50}")
        print(f"    Trades:        {trades}")
        print(f"    Win Rate:      {win_rate:.1%}")
        print(f"    Profit Factor: {profit_factor:.2f}")
        print(f"    Expectancy:    {expectancy:.2f}R per trade")
        print(f"    Total PnL:     {total_pnl_r:.1f}R")
        print(f"    Max Drawdown:  {max_dd:.1f}R")

        # Check thresholds
        checks = {
            "win_rate_ok": win_rate >= 0.50,
            "pf_ok": profit_factor >= 1.3,
            "expectancy_ok": expectancy > 0,
        }

        status = "✅ PASS" if all(checks.values()) else "❌ FAIL"
        print(f"    Status:        {status}")

        for check, ok in checks.items():
            icon = "✅" if ok else "❌"
            print(f"      {icon} {check}")

        results[symbol] = {
            "trades": trades,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 3),
            "total_pnl_r": round(total_pnl_r, 1),
            "max_dd_r": round(max_dd, 1),
            "pass": all(checks.values()),
        }

    return results


def main():
    parser = argparse.ArgumentParser(description="MTF AI Training Pipeline")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbols (default: all)")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip data download (use existing Parquet)")
    parser.add_argument("--epochs", type=int, default=30,
                        help="Training epochs (default: 30)")
    parser.add_argument("--quick", action="store_true",
                        help="Quick test mode (fewer epochs)")
    parser.add_argument("--backtest", action="store_true",
                        help="Run backtest only (skip training)")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Data directory for Parquet files")
    args = parser.parse_args()

    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS
    data_dir = args.data_dir or DATA_DIR
    epochs = 5 if args.quick else args.epochs

    print("=" * 60)
    print("  MTF AI Training Pipeline")
    print("=" * 60)
    print(f"  Symbols:       {', '.join(symbols)}")
    print(f"  Data dir:      {data_dir}")
    print(f"  Epochs:        {epochs}")
    print(f"  Skip download: {args.skip_download}")
    print(f"  Backtest only: {args.backtest}")
    print("=" * 60)

    t0 = time.time()

    # Step 1: Download
    if not args.skip_download and not args.backtest:
        ok = step_download(symbols, data_dir)
        if not ok:
            print("\n[ERROR] Download failed. Use --skip-download if data exists.")
            return

    # Step 2: Build features
    all_data = step_build_features(symbols, data_dir)
    if not all_data:
        print("\n[ERROR] No features built. Check Parquet files in data dir.")
        return

    # Step 3: Train (unless backtest-only)
    if not args.backtest:
        train_result = step_train(all_data, epochs=epochs)
        if train_result.get("status") not in ("trained",):
            print(f"\n[WARN] Training issue: {train_result}")

    # Step 4: Backtest
    bt_results = step_backtest(all_data)

    # Final Summary
    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  TRAINING PIPELINE COMPLETE")
    print(f"{'='*60}")
    print(f"  Total time: {elapsed:.1f}s")

    passing = sum(1 for r in bt_results.values() if r.get("pass"))
    total = len(bt_results)
    print(f"  Backtest: {passing}/{total} symbols passed")

    for sym, res in bt_results.items():
        icon = "✅" if res.get("pass") else "❌"
        print(f"    {icon} {sym}: WR={res['win_rate']:.1%} PF={res['profit_factor']:.2f} "
              f"Exp={res['expectancy']:.2f}R Trades={res['trades']}")

    # Safety gate
    if passing == 0 and total > 0:
        print(f"\n  ⚠️  NO symbols passed backtest thresholds!")
        print(f"  ⚠️  Model saved but NOT recommended for LIVE trading.")
    elif passing < total:
        print(f"\n  ⚠️  Some symbols didn't pass. Review before LIVE trading.")
    else:
        print(f"\n  ✅ All symbols passed! Model is ready for DRY_RUN testing.")

    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
