# -*- coding: utf-8 -*-
"""
Volume Window Optimization Script
Tests different lookback periods for volume calculations to find the most accurate setting.
"""

import sys
import os
import argparse
import json
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Fix path
project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.trader.features.volatility import add_volatility_features
from backend.trader.regime.classifier import classify_regime
from backend.trader.liquidity.detector import detect_liquidity_events
from backend.trader.strategy.selector import select_and_generate_signal
from backend.trader.data.mapper import mapper
from backend.trader.data.fetcher import fetcher

# Mock classes for simulation
class MockExecutor:
    def __init__(self): self.trades = []
    def place_order(self, signal, lot_size=0.01): self.trades.append(signal)

def load_data(symbol, tf_str, bars):
    if not fetcher.connect(): return None
    
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M3": mt5.TIMEFRAME_M3, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4
    }
    tf = tf_map.get(tf_str, mt5.TIMEFRAME_M5)
    broker_sym = mapper.to_broker(symbol)
    df = fetcher.get_rates(symbol, tf, bars)
    fetcher.disconnect()
    return df

def run_backtest_window(df_raw, symbol, tf_str, window_size):
    """Simple backtest simulating the trader loop with a specific volume window."""
    trades = []
    
    # Pre-add standard features (volatility uses the window_size)
    df = add_volatility_features(df_raw.copy(), volume_lookback=window_size)
    from backend.trader.features.structure import add_structure_features, detect_displacement
    from backend.trader.features.institutional import add_institutional_features
    from backend.trader.features.divergence import detect_rsi_divergence
    from backend.trader.features.candle_patterns import detect_candle_patterns
    
    df = add_structure_features(df)
    df = detect_displacement(df)
    df = add_institutional_features(df)
    df = detect_rsi_divergence(df)
    df = detect_candle_patterns(df)
    
    # Simulate step by step (limit window for speed)
    lookback = 100
    start_idx = max(lookback, len(df) - 5000)
    
    for i in range(start_idx, len(df) - 21):
        window = df.iloc[i-lookback:i+1].copy()
        context = {
            "symbol": symbol, 
            "timeframe": tf_str, 
            "regime_result": classify_regime(window, {"trend_threshold": 0.45, "volatility_compression_threshold": 0.5, "volatility_expansion_threshold": 1.5})
        }
        events = detect_liquidity_events(window, {"eqh_eql_threshold_points": 50, "sweep_lookback_bars": 80})
        
        signal = select_and_generate_signal(window, context, events, current_bar=i)
        
        # Diagnostic: If Window 15 and no trades yet, print why
        if not signal and window_size > 10 and i % 100 == 0:
            from backend.trader.strategy.antichop_filter import is_market_choppy
            is_choppy, chop_reason = is_market_choppy(window)
            vol_ratio = window.iloc[-1].get('vol_ratio', 0)
            if is_choppy:
                print(f"DEBUG: Bar {i} Choppy: {chop_reason}")
            elif vol_ratio < 0.8:
                print(f"DEBUG: Bar {i} Low Vol Ratio: {vol_ratio:.2f}")

        if signal:
            # Simple outcome check (next 20 bars)
            future = df.iloc[i+1 : i+21]
            if future.empty: continue
            
            entry = float(signal['entry_price'])
            sl = float(signal['sl'])
            tp = float(signal['tp1'])
            side = signal['side'].upper()
            
            outcome_pnl = 0
            hit = False
            for _, bar in future.iterrows():
                if side == "BUY":
                    if bar['low'] <= sl: 
                        outcome_pnl = sl - entry
                        hit = True; break
                    if bar['high'] >= tp: 
                        outcome_pnl = tp - entry
                        hit = True; break
                else:
                    if bar['high'] >= sl: 
                        outcome_pnl = entry - sl
                        hit = True; break
                    if bar['low'] <= tp: 
                        outcome_pnl = entry - tp
                        hit = True; break
            
            if hit:
                trades.append(outcome_pnl)

    wins = [p for p in trades if p > 0]
    losses = [p for p in trades if p <= 0]
    net_pnl = sum(trades)
    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 0.0
    wr = len(wins) / len(trades) if trades else 0
    
    return {"window": window_size, "trades": len(trades), "net": net_pnl, "wr": wr, "pf": pf}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--tf", default="M5")
    parser.add_argument("--bars", type=int, default=10000)
    args = parser.parse_args()
    
    print(f"--- Optimizing Volume Window for {args.symbol} ({args.tf}) ---")
    df = load_data(args.symbol, args.tf, args.bars)
    if df is None:
        print("Failed to load data")
        return

    windows = [10, 15, 20, 25, 30, 40, 50, 60, 80]
    results = []
    
    for w in windows:
        print(f"Testing Window: {w}...", flush=True)
        res = run_backtest_window(df, args.symbol, args.tf, w)
        results.append(res)
        print(f" Result: Trades={res['trades']} PF={res['pf']:.2f} WR={res['wr']:.1%}")

    results.sort(key=lambda x: x['pf'], reverse=True)
    if results:
        best = results[0]
        print("\n" + "="*50)
        print(f"BEST WINDOW FOR {args.symbol}: {best['window']}")
        print(f"Profit Factor: {best['pf']:.2f}")
        print(f"Win Rate: {best['wr']:.1%}")
        print(f"Trades Sampled: {best['trades']}")
        print("="*50)

if __name__ == "__main__":
    main()
