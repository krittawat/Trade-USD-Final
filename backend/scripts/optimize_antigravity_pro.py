# -*- coding: utf-8 -*-
"""
Optimize Antigravity Pro — Institutional-Grade Optimization Suite
================================================================
Target: BTCUSD, USOILm, XAUUSD, XAGUSD
Logic: Alpha V6 + FullFeatureBacktester (Sizing + Ratchet)
Goal: Win Rate > 55%, PF > 1.3, Daily Profit 600-2,000 THB
"""

import sys
import os
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

# Add workspace root and trader to sys.path
BASE_PATH = Path(__file__).resolve().parent.parent
if str(BASE_PATH) not in sys.path:
    sys.path.insert(0, str(BASE_PATH))
# No need to add trader separately if we use absolute backend.trader imports, 
# but let's keep it for compatibility with some scripts.
sys.path.insert(0, str(BASE_PATH / "trader"))

from app.strategy.templates.alpha_v6_wrapper import AlphaV6Strategy
from backtest_full import FullFeatureBacktester
from app.core.logging import get_logger

logger = get_logger("OptimizePro")

# ═══════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════
SYMBOLS = ["BTCUSDm", "USOILm", "XAUUSDm", "XAGUSDm"]
TIMEFRAME = mt5.TIMEFRAME_M5
DAYS = 365
INITIAL_EQUITY = 200.0  # User Requirement: $200 Portfolio
MIN_PF_THRESHOLD = 1.3
MIN_WR_THRESHOLD = 55.0

# ═══════════════════════════════════════════════════════════
# OPTIMIZATION
# ═══════════════════════════════════════════════════════════

def fetch_data(symbol, days):
    """Fetch M5 data from MT5."""
    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return None

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    
    # Map symbol for Exness Standard if needed (though user provided clean names)
    # If the user symbols don't exist, we try variations
    candidates = [symbol]
    if "XAU" in symbol: candidates.append("XAUUSDc")
    if "XAG" in symbol: candidates.append("XAGUSDc")
    if "BTC" in symbol: candidates.append("BTCUSDm")
    
    df = None
    for cand in candidates:
        rates = mt5.copy_rates_range(cand, TIMEFRAME, utc_from, utc_to)
        if rates is not None and len(rates) > 1000:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            print(f"✅ Loaded {len(df)} bars for {cand}")
            return df, cand
            
    print(f"❌ No data for {symbol} after checking {candidates}")
    return None, symbol

def run_backtest(symbol, df, overrides=None):
    """Run backtest with Alpha V6."""
    strategy = AlphaV6Strategy(overrides)
    bt = FullFeatureBacktester(strategy, initial_equity=INITIAL_EQUITY)
    
    # Contract size & point mapping
    specs = {
        "XAU": {"contract": 100.0, "point": 0.01},
        "XAG": {"contract": 5000.0, "point": 0.001},
        "BTC": {"contract": 1.0, "point": 0.01},
        "OIL": {"contract": 1000.0, "point": 0.01}
    }
    
    found_spec = {"contract": 100.0, "point": 0.01} # default
    for key, spec in specs.items():
        if key in symbol.upper():
            found_spec = spec
            break
            
    try:
        result = bt.run(df, symbol, contract_size=found_spec["contract"], point=found_spec["point"])
        return result
    except Exception as e:
        print(f"🔥 Backtest CRASH: {e}")
        return None

PARAM_GRID = [
    {"sweep_lookback": 20, "min_fvg_atr": 0.5, "tp1_rr": 1.5},
    {"sweep_lookback": 30, "min_fvg_atr": 0.7, "tp1_rr": 1.5},
    {"sweep_lookback": 40, "min_fvg_atr": 0.7, "tp1_rr": 2.0},
    {"sweep_lookback": 40, "min_fvg_atr": 1.0, "tp1_rr": 1.5},
]

def score_res(res):
    """Score a backtest result: (WR * 10) + (PF * 50) - (DD * 5)"""
    if res.total_trades < 5: return -100 # Not enough statistically
    return (res.win_rate * 10) + (res.profit_factor * 50) - (res.max_drawdown_pct * 5)

def main():
    print("🚀 Starting ANTIGRAVITY PRO Optimization Grid Search...")
    
    best_overall = []
    
    for symbol in SYMBOLS:
        print(f"\n--- GRID SEARCH: {symbol} ---")
        df, actual_symbol = fetch_data(symbol, DAYS)
        if df is None: continue
        
        runs = []
        for params in PARAM_GRID:
            print(f"  Testing Params: {params}")
            result = run_backtest(actual_symbol, df, overrides=params)
            if result:
                score = score_res(result)
                print(f"    -> [DONE] WR={result.win_rate}% | PF={result.profit_factor} | Score={score:.1f}")
                runs.append({"params": params, "result": result, "score": score})
        
        if runs:
            # Pick best for this symbol
            runs.sort(key=lambda x: x["score"], reverse=True)
            winner = runs[0]
            print(f"🏆 Best for {symbol}: WR={winner['result'].win_rate}% PF={winner['result'].profit_factor} Params={winner['params']}")
            
            best_overall.append({
                "symbol": symbol,
                "best_params": winner["params"],
                "win_rate": winner["result"].win_rate,
                "pf": winner["result"].profit_factor,
                "pnl": winner["result"].total_profit_usd,
                "max_dd": winner["result"].max_drawdown_pct,
                "trades": winner["result"].total_trades
            })
    
    mt5.shutdown()
    
    # Save optimized results
    output_file = BASE_PATH / "data" / "optimization_pro_best.json"
    with open(output_file, "w") as f:
        json.dump(best_overall, f, indent=4)
    print(f"\n✅ Optimization complete! Best config saved to {output_file}")

if __name__ == "__main__":
    main()
