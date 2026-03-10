"""
Randomized Grid Search explicitly targeting Daily Profitability.
Optimizing M5 Rapid Scalper parameters to maximize WinRate, PnL Points, and % Profitable Days.
"""

import sys
import time
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5
import numpy as np
import pandas as pd
import pandas_ta as ta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.analysis import indicators as ind

def fetch_data(symbol, days, timeframe):
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, timeframe, start_date, end_date)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df['date'] = df['time'].dt.date
    return df

def generate_random_params(n_iter):
    params = []
    
    ma_choices = [8, 10, 12, 14, 21, 50]
    saf_choices = [0.015, 0.02, 0.025, 0.03, 0.04]
    smax_choices = [0.15, 0.20, 0.25, 0.30]
    rsi_choices = [10, 14, 21]
    sl_m_choices = [0.8, 1.0, 1.2, 1.5, 2.0, 2.5]
    tp_m_choices = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
    macd_choices = [(12,26,9), (8,21,5), (5,13,5)]
    
    for i in range(n_iter):
        macd = random.choice(macd_choices)
        p = {
            "label": f"RANDOM_{i}",
            "ma": random.choice(ma_choices),
            "saf": random.choice(saf_choices),
            "smax": random.choice(smax_choices),
            "rsi": random.choice(rsi_choices),
            "sl_m": random.choice(sl_m_choices),
            "tp_m": random.choice(tp_m_choices),
            "macd_f": macd[0],
            "macd_s": macd[1],
            "macd_sig": macd[2],
            "rsi_upper": random.choice([75, 80, 85, 90]),
            "rsi_lower": random.choice([10, 15, 20, 25])
        }
        params.append(p)
    return params

def run_vectorized_backtest(df, p):
    df = df.copy()
    
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
            df['psar'] = df['close'] 
    else:
        df['psar'] = df['close'] 
        
    df['rsi'] = ind.rsi(df['close'], length=p['rsi'])
    
    macd_res = ind.macd(df['close'], fast=p['macd_f'], slow=p['macd_s'], signal=p['macd_sig'])
    df['macd_line'] = macd_res[f"MACD_{p['macd_f']}_{p['macd_s']}_{p['macd_sig']}"]
    df['macd_sig'] = macd_res[f"MACDs_{p['macd_f']}_{p['macd_s']}_{p['macd_sig']}"]
    df['atr'] = ind.atr(df['high'], df['low'], df['close'], length=14)
    
    df = df.dropna().reset_index(drop=True)
    
    buy_cond = (df['close'] > df['ma']) & (df['psar'] < df['close']) & (df['macd_line'] > df['macd_sig']) & (df['rsi'] <= p['rsi_upper'])
    sell_cond = (df['close'] < df['ma']) & (df['psar'] > df['close']) & (df['macd_line'] < df['macd_sig']) & (df['rsi'] >= p['rsi_lower'])
    
    df['signal'] = 0
    df.loc[buy_cond, 'signal'] = 1
    df.loc[sell_cond, 'signal'] = -1
    
    in_trade = 0
    entry_price = 0.0
    sl = 0.0
    tp = 0.0
    
    wins = 0
    losses = 0
    total_pnl = 0.0
    
    # Create daily tracker
    dates = df['date'].values
    pnl_by_date = {}
    
    signals = df['signal'].values
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    atrs = df['atr'].values
    
    for i in range(1, len(df)):
        current_date = dates[i]
        if current_date not in pnl_by_date:
            pnl_by_date[current_date] = 0.0
            
        if in_trade == 1: 
            if lows[i] <= sl:
                losses += 1
                curr_loss = -abs(entry_price - sl)
                total_pnl += curr_loss
                pnl_by_date[current_date] += curr_loss
                in_trade = 0
            elif highs[i] >= tp:
                wins += 1
                curr_win = abs(tp - entry_price)
                total_pnl += curr_win
                pnl_by_date[current_date] += curr_win
                in_trade = 0
                
        elif in_trade == -1: 
            if highs[i] >= sl:
                losses += 1
                curr_loss = -abs(sl - entry_price)
                total_pnl += curr_loss
                pnl_by_date[current_date] += curr_loss
                in_trade = 0
            elif lows[i] <= tp:
                wins += 1
                curr_win = abs(entry_price - tp)
                total_pnl += curr_win
                pnl_by_date[current_date] += curr_win
                in_trade = 0
                
        if in_trade == 0 and signals[i-1] != 0:
            in_trade = signals[i-1]
            entry_price = closes[i] 
            if in_trade == 1:
                sl = entry_price - (atrs[i-1] * p['sl_m'])
                tp = entry_price + (atrs[i-1] * p['tp_m'])
            else:
                sl = entry_price + (atrs[i-1] * p['sl_m'])
                tp = entry_price - (atrs[i-1] * p['tp_m'])
                
    total = wins + losses
    winrate = (wins / total * 100) if total > 0 else 0
    
    profit_days = sum(1 for v in pnl_by_date.values() if v > 0)
    total_days_traded = sum(1 for v in pnl_by_date.values() if v != 0)
    win_days_pct = (profit_days / total_days_traded * 100) if total_days_traded > 0 else 0
    
    return {
        "params": p,
        "trades": total,
        "winrate": winrate,
        "pnl_points": total_pnl,
        "win_days_pct": win_days_pct,
        "total_days_traded": total_days_traded
    }

def main():
    mt5.initialize()
    
    symbols = ["XAUUSDc", "XAGUSDc"]
    N_ITERATIONS = 300
    
    timeframes = [
        ("M2", mt5.TIMEFRAME_M2),
        ("M3", mt5.TIMEFRAME_M3),
        ("M5", mt5.TIMEFRAME_M5),
        ("M12", mt5.TIMEFRAME_M12),
        ("M30", mt5.TIMEFRAME_M30),
        ("H1", mt5.TIMEFRAME_H1)
    ]
    
    random.seed(42)
    param_grid = generate_random_params(N_ITERATIONS)
    
    best_overall = {}

    for tf_name, tf_enum in timeframes:
        for symbol in symbols:
            print(f"\\n{'='*60}\\nRunning Random Search {N_ITERATIONS} Configs for {symbol} on {tf_name} (365d)\\n{'='*60}")
            df = fetch_data(symbol, 365, tf_enum)
            if df is None:
                print(f"Skipping {symbol} - No Data")
                continue
                
            print(f"Downloaded {len(df)} candles.")
            best_daily_winrate = -1
            best_pnl_combo = None
            
            all_results = []
            
            # We will process param configs
            for i, p in enumerate(param_grid):
                # Print progress every 10%
                if (i+1) % (N_ITERATIONS//10) == 0:
                    print(f"Progress: {i+1}/{N_ITERATIONS} ...")
                    
                try:
                    res = run_vectorized_backtest(df, p)
                    if res['trades'] > 50: # Minimum significance
                        all_results.append(res)
                except Exception as e:
                    pass
                    
            if len(all_results) == 0:
                print("No valid results found with > 50 trades.")
                continue
                
            # Sort by PnL
            sorted_by_pnl = sorted(all_results, key=lambda x: x['pnl_points'], reverse=True)
            best_pnl = sorted_by_pnl[0]
            
            # Sort by Win Days Pct (if PnL > 0)
            profitable_res = [r for r in all_results if r['pnl_points'] > 0]
            if profitable_res:
                sorted_by_win_days = sorted(profitable_res, key=lambda x: (x['win_days_pct'], x['pnl_points']), reverse=True)
                best_win_days = sorted_by_win_days[0]
            else:
                best_win_days = best_pnl
            
            print(f"\\n--- TOP BY TOTAL PnL POINTS ({symbol} - {tf_name}) ---")
            p = best_pnl['params']
            print(f"PnL: {best_pnl['pnl_points']:.2f} | WinRate: {best_pnl['winrate']:.1f}% | WinDays: {best_pnl['win_days_pct']:.1f}% ({best_pnl['total_days_traded']} days)")
            print(f"Params: MA={p['ma']}, SAR({p['saf']},{p['smax']}), RSI={p['rsi']} [{p['rsi_lower']},{p['rsi_upper']}], SL={p['sl_m']}x, TP={p['tp_m']}x, MACD=({p['macd_f']},{p['macd_s']},{p['macd_sig']})")
            
            print(f"\\n--- TOP BY DAILY PROFITABILITY (%) ({symbol} - {tf_name}) ---")
            p = best_win_days['params']
            print(f"WinDays: {best_win_days['win_days_pct']:.1f}% ({best_win_days['total_days_traded']} days) | PnL: {best_win_days['pnl_points']:.2f} | WinRate: {best_win_days['winrate']:.1f}%")
            print(f"Params: MA={p['ma']}, SAR({p['saf']},{p['smax']}), RSI={p['rsi']} [{p['rsi_lower']},{p['rsi_upper']}], SL={p['sl_m']}x, TP={p['tp_m']}x, MACD=({p['macd_f']},{p['macd_s']},{p['macd_sig']})")
            print("="*60)
            
            # Track overall best for summary
            if symbol not in best_overall:
                best_overall[symbol] = []
            best_overall[symbol].append({
                "tf": tf_name,
                "win_days": best_win_days['win_days_pct'],
                "pnl": best_win_days['pnl_points'],
                "params": best_win_days['params']
            })

    print(f"\\n\\n{'='*80}\\nULTIMATE OPTIMIZATION SUMMARY ACROSS ALL TIMEFRAMES\\n{'='*80}")
    for symbol, results in best_overall.items():
        print(f"\\n--- BEST TIMEFRAMES FOR {symbol} ---")
        sorted_res = sorted(results, key=lambda x: x['win_days'], reverse=True)
        for i, res in enumerate(sorted_res):
            p = res['params']
            print(f"Rank {i+1}: {res['tf']} => WinDays: {res['win_days']:.1f}% | PnL: {res['pnl']:.2f}")
            print(f"        Params: MA={p['ma']}, SAR({p['saf']},{p['smax']}), RSI={p['rsi']} [{p['rsi_lower']},{p['rsi_upper']}], SL={p['sl_m']}x, TP={p['tp_m']}x, MACD=({p['macd_f']},{p['macd_s']},{p['macd_sig']})")

if __name__ == "__main__":
    main()
