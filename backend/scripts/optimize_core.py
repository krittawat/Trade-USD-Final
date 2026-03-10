"""
Core Strategies Optimizer (XAU, XAG, BTC)
=========================================
Systematic grid search for `gold_smart_money`, `silver_evolution`, `btc_elite`.
Auto-generates parameters, runs backtests, and saves directly to strategy_params in DB.

Usage:
    python backend/scripts/optimize_core.py --strategy gold_smart_money --days 30
"""

import sys
import os
import time
import json
import logging
import argparse
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from itertools import product
from typing import List, Dict, Any
import pandas as pd
import numpy as np

# Adjust path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
from app.core.logging import get_logger
from app.execution.backtester import Backtester, BacktestResult
from app.brain.memory_store import MemoryStore
from scripts.backtest_full import FullFeatureBacktester
from app.strategy.factory import StrategyFactory
from app.db.sqlite import SQLiteStore
from app.core.config import get_settings

# Setup StrategyParamLoader explicitly for optimization scripts
from app.strategy.param_loader import StrategyParamLoader, set_param_loader

# Suppress noisy logs
for log_name in ["app.core.logging", "app.risk", "app.risk.sizing", "app.risk.trailing", "app.brain.regime", "FullBacktest"]:
    logging.getLogger(log_name).setLevel(logging.ERROR)

def get_grid(strategy_name: str, quick: bool = False) -> List[Dict[str, Any]]:
    combos = []
    
    if strategy_name == "gold_smart_money":
        # Target: Win Rate > 50%, PF > 1.3
        sl_buffers = [0.2, 0.3, 0.5]
        rr_targets = [1.5, 2.0, 2.5]
        confluences = [2, 3, 4]
        sweep_wicks = [0.2, 0.4]
        swing_lookbacks = [15, 20]
        
        if quick:
            sl_buffers, rr_targets = [0.3, 0.5], [2.0, 2.5]
            
        for sl, rr, conf, sw, slk in product(sl_buffers, rr_targets, confluences, sweep_wicks, swing_lookbacks):
            label = f"XAU_SL{sl}_RR{rr}_Conf{conf}_Sw{sw}_Slk{slk}"
            combos.append({
                "label": label,
                "params": {
                    "sl_buffer_atr": sl,
                    "rr_target": rr,
                    "min_confluence": conf,
                    "sweep_wick_min": sw,
                    "swing_lookback": slk
                }
            })
            
    elif strategy_name == "silver_evolution":
        sl_mults = [1.5, 2.0, 2.5]
        tp_mults = [2.0, 3.0, 4.0]
        adx_mins = [20, 25]
        bb_stds = [2.0, 2.5]
        
        if quick:
            sl_mults, tp_mults = [2.0], [2.0, 3.0]
            
        for sl, tp, adx, bb in product(sl_mults, tp_mults, adx_mins, bb_stds):
            label = f"XAG_SL{sl}_TP{tp}_ADX{adx}_BB{bb}"
            combos.append({
                "label": label,
                "params": {
                    "sl_atr_mult": sl,
                    "tp_atr_mult": tp,
                    "adx_min": adx,
                    "bb_std": bb
                }
            })
            
    elif strategy_name == "btc_elite":
        sl_mults = [1.5, 2.0, 3.0]
        rr_standards = [1.2, 1.5, 2.0]
        adx_mins = [15, 18, 20]
        min_scores = [50, 60, 70]
        
        if quick:
            sl_mults, rr_standards = [2.0], [1.5, 2.0]
            
        for sl, rr, adx, ms in product(sl_mults, rr_standards, adx_mins, min_scores):
            label = f"BTC_SL{sl}_RR{rr}_ADX{adx}_Score{ms}"
            combos.append({
                "label": label,
                "params": {
                    "sl_atr_mult": sl,
                    "rr_standard": rr,
                    "adx_min": adx,
                    "min_score_trade": ms
                }
            })
            
    return combos

def fetch_data(symbol: str, tf, days: int):
    if not mt5.initialize():
        print("MT5 Init failed")
        return None, 0.0

    acc = mt5.account_info()
    balance = acc.balance if acc else 10000.0

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)

    rates = mt5.copy_rates_range(symbol, tf, start_date, end_date)
    mt5.shutdown()
    
    if rates is None or len(rates) == 0:
        return None, 0.0
        
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df, balance

def run_optimization(strategy_name: str, days: int, quick: bool = False):
    symbol_map = {
        "gold_smart_money": ("XAUUSDc", mt5.TIMEFRAME_M15),
        "silver_evolution": ("XAGUSDc", mt5.TIMEFRAME_M15),
        "btc_elite": ("BTCUSDc", mt5.TIMEFRAME_M5)
    }
    
    if strategy_name not in symbol_map:
        print(f"Unknown strategy: {strategy_name}")
        return
        
    symbol, timeframe = symbol_map[strategy_name]
    
    # Init DB and param loader BEFORE instantiating strategies
    settings = get_settings()
    db = SQLiteStore(settings)
    db.connect()
    loader = StrategyParamLoader(db)
    set_param_loader(loader)
    
    df, balance = fetch_data(symbol, timeframe, days)
    if df is None:
        print(f"No data for {symbol}")
        return
        
    combos = get_grid(strategy_name, quick)
    print(f"\n🚀 Tunning {strategy_name} on {symbol} | Combinations: {len(combos)}")
    
    factory = StrategyFactory()
    factory.auto_register(db)
    
    results = []
    
    best_score = -9999
    best_params = None
    best_result = None
    
    for i, c in enumerate(combos):
        # Apply params logic
        strategy = factory._strategies.get(strategy_name)
        if not strategy:
            print(f"Strategy {strategy_name} not found in factory")
            return
            
        # Update strategy parameters dynamically
        for key, val in c["params"].items():
            if hasattr(strategy, "p"):
                strategy.p[key] = val
            elif hasattr(strategy, key):
                setattr(strategy, key, val)
                
        bt = FullFeatureBacktester(strategy, initial_equity=balance)
        res = bt.run(df, symbol)
        
        # Scoring Criteria target: WR > 50%, PF > 1.3
        score = 0
        if res.win_rate >= 50:
            score += res.win_rate
        else:
            score -= (50 - res.win_rate) * 2
            
        if res.profit_factor >= 1.3:
            score += res.profit_factor * 20
        else:
            score -= (1.3 - res.profit_factor) * 50
            
        if res.total_trades < 10:
            score -= 100
        
        results.append({
            "label": c["label"],
            "params": c["params"],
            "wr": res.win_rate,
            "pf": res.profit_factor,
            "trades": res.total_trades,
            "pnl": res.total_profit_usd,
            "score": score
        })
        
        print(f"[{i+1}/{len(combos)}] {c['label']:<35} | WR: {res.win_rate:>5.1f}% | PF: {res.profit_factor:>4.2f} | Trds: {res.total_trades:>3} | PnL: ${res.total_profit_usd:>6.2f} | Score: {score:>6.1f}")
        
        if score > best_score and res.win_rate >= 50 and res.profit_factor >= 1.3:
            best_score = score
            best_params = c["params"]
            best_result = res
            
    if best_params:
        print(f"\n🏆 Best Config for {strategy_name}:")
        print(f"WR: {best_result.win_rate:.1f}% | PF: {best_result.profit_factor:.2f} | Score: {best_score:.1f}")
        for k, v in best_params.items():
            print(f"  {k}: {v}")
            
        # Save to DB
        loader.save_params(strategy_name, symbol, best_params)
        print("✅ Saved to database!")
    else:
        print("\n❌ No configuration met the minimum standards (WR >= 50%, PF >= 1.3).")
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", type=str, required=True, choices=["gold_smart_money", "silver_evolution", "btc_elite"])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    
    run_optimization(args.strategy, args.days, args.quick)
