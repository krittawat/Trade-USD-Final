"""
Fast Vectorized Backtester specifically for M5 Rapid Scalper (XAU/XAG 365d)
Skips the slow MT5 line-by-line simulation and calculates theoretical PnL instantly via Pandas.
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
import pandas_ta as ta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.analysis import indicators as ind

TIMEFRAME = mt5.TIMEFRAME_M5

PARAM_GRID = [
    {"label": "RAPID_ULTRA_FAST", "ma": 10, "saf": 0.02, "smax": 0.2, "rsi": 14, "sl_m": 1.2, "tp_m": 1.5, "macd_f": 12, "macd_s": 26, "macd_sig": 9},
    {"label": "RAPID_AGGRESSIVE_TP", "ma": 12, "saf": 0.02, "smax": 0.2, "rsi": 14, "sl_m": 1.5, "tp_m": 3.0, "macd_f": 12, "macd_s": 26, "macd_sig": 9},
    {"label": "RAPID_STANDARD", "ma": 12, "saf": 0.02, "smax": 0.2, "rsi": 14, "sl_m": 1.5, "tp_m": 2.0, "macd_f": 12, "macd_s": 26, "macd_sig": 9},
    {"label": "RAPID_SAFE_WIDE_SL", "ma": 12, "saf": 0.02, "smax": 0.2, "rsi": 14, "sl_m": 2.5, "tp_m": 2.0, "macd_f": 12, "macd_s": 26, "macd_sig": 9},
    {"label": "RAPID_TIGHT_SL_WIDE_TP", "ma": 12, "saf": 0.03, "smax": 0.2, "rsi": 14, "sl_m": 1.0, "tp_m": 3.0, "macd_f": 8, "macd_s": 21, "macd_sig": 5},
    {"label": "RAPID_LONG_RSI", "ma": 12, "saf": 0.02, "smax": 0.2, "rsi": 21, "sl_m": 1.5, "tp_m": 2.0, "macd_f": 12, "macd_s": 26, "macd_sig": 9},
]

def fetch_data(symbol, days):
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, TIMEFRAME, start_date, end_date)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df

def run_vectorized_backtest(df, p):
    df = df.copy()
    
    # 1. Indicators
    df['ma'] = ind.ema(df['close'], length=p['ma'])
    sar = ta.psar(df['high'], df['low'], df['close'], af=p['saf'], af0=p['saf'], max_af=p['smax'])
    if sar is not None and not sar.empty:
        psar_l = [c for c in sar.columns if c.startswith('PSARl_')]
        psar_s = [c for c in sar.columns if c.startswith('PSARs_')]
        if psar_l and psar_s:
            df['psar'] = sar[psar_l[0]].fillna(sar[psar_s[0]])
        elif psar_l:
            df['psar'] = sar[psar_l[0]].fillna(df['close'])
        elif psar_s:
            df['psar'] = sar[psar_s[0]].fillna(df['close'])
        else:
            df['psar'] = df['close'] # fallback
    else:
        df['psar'] = df['close'] # fallback
        
    df['rsi'] = ind.rsi(df['close'], length=p['rsi'])
    
    macd_res = ind.macd(df['close'], fast=p['macd_f'], slow=p['macd_s'], signal=p['macd_sig'])
    macd_line_col = f"MACD_{p['macd_f']}_{p['macd_s']}_{p['macd_sig']}"
    macd_sig_col = f"MACDs_{p['macd_f']}_{p['macd_s']}_{p['macd_sig']}"
    df['macd_line'] = macd_res[macd_line_col]
    df['macd_sig'] = macd_res[macd_sig_col]
    df['atr'] = ind.atr(df['high'], df['low'], df['close'], length=14)
    
    # Shift indicators to simulate trading at the CLOSE of the deciding bar (entering at NEXT open)
    # Actually, we can assume we enter at Current Close for simplicity in pure scalping
    
    df = df.dropna().reset_index(drop=True)
    
    # 2. Signals
    buy_cond = (df['close'] > df['ma']) & (df['psar'] < df['close']) & (df['macd_line'] > df['macd_sig']) & (df['rsi'] <= 85)
    sell_cond = (df['close'] < df['ma']) & (df['psar'] > df['close']) & (df['macd_line'] < df['macd_sig']) & (df['rsi'] >= 15)
    
    df['signal'] = 0
    df.loc[buy_cond, 'signal'] = 1
    df.loc[sell_cond, 'signal'] = -1
    
    # Forward pass to calculate trades
    in_trade = 0
    entry_price = 0.0
    sl = 0.0
    tp = 0.0
    
    wins = 0
    losses = 0
    pnl = 0.0 # Standardize 1 lot = 1 unit for comparison
    
    signals = df['signal'].values
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    atrs = df['atr'].values
    
    for i in range(1, len(df)):
        if in_trade == 1: # Long
            if lows[i] <= sl:
                losses += 1
                pnl -= abs(entry_price - sl)
                in_trade = 0
            elif highs[i] >= tp:
                wins += 1
                pnl += abs(tp - entry_price)
                in_trade = 0
                
        elif in_trade == -1: # Short
            if highs[i] >= sl:
                losses += 1
                pnl -= abs(sl - entry_price)
                in_trade = 0
            elif lows[i] <= tp:
                wins += 1
                pnl += abs(entry_price - tp)
                in_trade = 0
                
        if in_trade == 0 and signals[i-1] != 0:
            in_trade = signals[i-1]
            entry_price = closes[i] # enter at open or close of next bar
            if in_trade == 1:
                sl = entry_price - (atrs[i-1] * p['sl_m'])
                tp = entry_price + (atrs[i-1] * p['tp_m'])
            else:
                sl = entry_price + (atrs[i-1] * p['sl_m'])
                tp = entry_price - (atrs[i-1] * p['tp_m'])
                
    total = wins + losses
    winrate = (wins / total * 100) if total > 0 else 0
    return {
        "label": p["label"],
        "trades": total,
        "winrate": winrate,
        "pnl_points": pnl
    }

def main():
    mt5.initialize()
    
    symbols = ["XAUUSDc", "XAGUSDc"]
    
    for symbol in symbols:
        print(f"\n{'='*50}\nEvaluating FAST SCALPER {symbol} for 365 Days\n{'='*50}")
        df = fetch_data(symbol, 365)
        if df is None:
            print(f"Skipping {symbol} - No Data")
            continue
            
        print(f"Downloaded {len(df)} candles.")
        best_pnl = -99999
        best_res = None
        
        for p in PARAM_GRID:
            res = run_vectorized_backtest(df, p)
            print(f"{res['label']:25s} | Trades: {res['trades']:4d} | WinRate: {res['winrate']:5.1f}% | PnL Points: {res['pnl_points']:8.2f}")
            if res['pnl_points'] > best_pnl:
                best_pnl = res['pnl_points']
                best_res = res
                
        print("-" * 50)
        print(f"BEST SCRIPT FOR {symbol}: {best_res['label']} -> {best_pnl:.2f} Points (WR: {best_res['winrate']:.1f}%)")

if __name__ == "__main__":
    main()
