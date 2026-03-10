# -*- coding: utf-8 -*-
"""
USOIL Multi-TF Strategy Tournament - 20,000 Bars Edition.
Includes "No Look-ahead" simulation logic.
"""

import sys
import os
import argparse
import json
import logging
from datetime import datetime
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

# Add project root to sys.path
sys.path.insert(0, "d:/VibeCode/Trade")

from backend.trader.features.volatility import add_volatility_features
from backend.trader.features.structure_usoil import add_structure_features_usoil, detect_displacement_usoil
from backend.trader.features.institutional import add_institutional_features
from backend.trader.features.divergence import detect_rsi_divergence
from backend.trader.features.candle_patterns import detect_candle_patterns
from backend.trader.data.time_utils import time_utils
from backend.trader.regime.classifier import classify_regime
from backend.trader.liquidity.detector import detect_liquidity_events
from backend.trader.storage.sqlite_db import db

# Strategies
from backend.trader.strategy.momentum_scalper_v2 import signal_momentum_scalper_v2
from backend.trader.strategy.usoil_elite import signal_usoil_elite
from backend.trader.strategy.fvg_logic import signal_fvg_logic
from backend.trader.strategy.antigravity_alpha import signal_antigravity_alpha

logging.basicConfig(level=logging.WARNING)

SYMBOL_SPECS = {
    "USOIL": {"point": 0.001, "spread_points": 18, "contract_size": 1000.0, "spread_cap": 50},
}

STRATEGIES = [
    ("MOMENTUM_SCALPER_V2", signal_momentum_scalper_v2),
    ("USOIL_ELITE", signal_usoil_elite),
    ("FVG_LOGIC", signal_fvg_logic),
    ("ANTIGRAVITY_ALPHA", signal_antigravity_alpha),
]

TIMEFRAMES = ["M3", "M5", "M6", "M12", "M15", "M30", "H1", "H2", "H4"]

def load_data(symbol, timeframe, bars):
    from backend.trader.data.mapper import mapper
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M3": mt5.TIMEFRAME_M3, "M4": mt5.TIMEFRAME_M4,
        "M5": mt5.TIMEFRAME_M5, "M6": mt5.TIMEFRAME_M6, "M10": mt5.TIMEFRAME_M10,
        "M12": mt5.TIMEFRAME_M12, "M15": mt5.TIMEFRAME_M15, "M20": mt5.TIMEFRAME_M20,
        "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H2": mt5.TIMEFRAME_H2,
        "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1
    }
    mt5_tf = tf_map[timeframe]
    
    if not mt5.initialize():
        print("MT5 Init Failed")
        return None
        
    broker_sym = mapper.to_broker(symbol)
    rates = mt5.copy_rates_from_pos(broker_sym, mt5_tf, 0, bars)
    if rates is None or len(rates) == 0:
        return None
        
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df

def prepare_features(df):
    df = time_utils.add_session_features(df)
    df = add_volatility_features(df)
    df = add_structure_features_usoil(df)
    df = detect_displacement_usoil(df)
    df = add_institutional_features(df)
    df = detect_rsi_divergence(df)
    df = detect_candle_patterns(df)
    return df

def simulate_trade_no_bias(signal, future_bars, spread_cost):
    """
    Simulation that prevents 'Same-Bar Win' if both TP and SL could have been hit.
    In real trading, we don't know if SL or TP hits first if both are within High/Low.
    Conservative approach: If both hit, assume SL (Capital Preservation).
    """
    side = signal.get("side")
    entry = float(signal.get("entry_price", 0))
    entry_type = signal.get("entry_type", "MARKET")
    sl = float(signal.get("sl", 0))
    tp = float(signal.get("tp1", 0))
    
    filled = False
    effective_entry = 0.0

    for _, bar in future_bars.iterrows():
        if not filled:
            if entry_type == "LIMIT":
                if (side == "BUY" and bar["low"] <= entry) or (side == "SELL" and bar["high"] >= entry):
                    filled = True
                    effective_entry = entry + (spread_cost/2 if side=="BUY" else -spread_cost/2)
                else: continue
            else:
                filled = True
                effective_entry = entry + (spread_cost/2 if side=="BUY" else -spread_cost/2)

        if filled:
            if side == "BUY":
                hit_sl = bar["low"] <= sl
                hit_tp = bar["high"] >= tp
                if hit_sl and hit_tp: # Both hit in same candle
                    return {"result": "SL", "pnl": sl - effective_entry} # Assume loss for safety
                if hit_sl: return {"result": "SL", "pnl": sl - effective_entry}
                if hit_tp: return {"result": "TP", "pnl": tp - effective_entry}
            else: # SELL
                hit_sl = bar["high"] >= sl
                hit_tp = bar["low"] <= tp
                if hit_sl and hit_tp:
                    return {"result": "SL", "pnl": effective_entry - sl}
                if hit_sl: return {"result": "SL", "pnl": effective_entry - sl}
                if hit_tp: return {"result": "TP", "pnl": effective_entry - tp}

    return {"result": "TIMEOUT", "pnl": 0}

def run_backtest(df, symbol, name, signal_fn, specs, timeframe):
    spread_cost = specs["spread_points"] * specs["point"]
    lot = 0.01
    contract = specs["contract_size"]
    trades = []
    
    for i in range(200, len(df) - 60):
        window = df.iloc[i-200:i+1]
        context = {"symbol": symbol, "regime_result": {"regime": "trending"}} # Simplified for speed
        
        sig = signal_fn(window, context)
        if not sig: continue
        
        future = df.iloc[i+1 : i+61]
        outcome = simulate_trade_no_bias(sig, future, spread_cost)
        
        pnl = outcome["pnl"] * lot * contract
        trades.append({"res": outcome["result"], "pnl": pnl})
        
    if not trades: return None
    
    net = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    wr = len(wins)/len(trades) * 100
    return {"name": name, "tf": timeframe, "trades": len(trades), "wr": wr, "net": net}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="USOILm")
    parser.add_argument("--bars", type=int, default=20000)
    args = parser.parse_args()
    
    print(f"\n🚀 USOIL MULTI-TF TOURNAMENT (Bars: {args.bars})")
    specs = SYMBOL_SPECS["USOIL"]
    
    all_results = []
    for tf in TIMEFRAMES:
        print(f"\n--- Timeframe: {tf} ---")
        df = load_data(args.symbol, tf, args.bars)
        if df is None: continue
        df = prepare_features(df)
        
        for name, fn in STRATEGIES:
            res = run_backtest(df, args.symbol, name, fn, specs, tf)
            if res:
                all_results.append(res)
                print(f"  [{name}] Net: ${res['net']:.2f} | WR: {res['wr']:.1f}% | Tr: {res['trades']}")
            else:
                print(f"  [{name}] No trades")

    print("\n" + "="*50)
    print("🏆 CHAMPION SUMMARY (Top 5)")
    all_results.sort(key=lambda x: x["net"], reverse=True)
    for r in all_results[:5]:
        print(f"{r['tf']} | {r['name']:<20} | ${r['net']:>8.2f} | {r['wr']:>5.1f}%")

if __name__ == "__main__":
    main()
