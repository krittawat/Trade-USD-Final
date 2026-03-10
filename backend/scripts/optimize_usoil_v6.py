import sys
import os
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
import optuna

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

try:
    import MetaTrader5 as mt5
    from backend.trader.data.mapper import mapper
    from backend.trader.features.volatility import add_volatility_features
    from backend.trader.features.structure import add_structure_features
    from backend.trader.data.time_utils import time_utils
except ImportError as e:
    print(f"FAIL Import error: {e}")
    sys.exit(1)

# --- Configuration ---
SYMBOL = "USOIL"
BARS = 80000  # ~1.1 Year M5 data
INITIAL_EQUITY = 300.0
SIM_SPREAD = 45  # Points
CONTRACT_SIZE = 1000.0

def precompute_base_features(df):
    df = df.copy()
    # Add ATR
    df = add_volatility_features(df)
    
    # ATR & Volatility Health
    df['atr_avg_50'] = df['atr'].rolling(50).mean()
    df['vol_healthy'] = df['atr'] >= df['atr_avg_50'] * 0.8
    
    # Trends
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['uptrend_htf'] = df['close'] > df['ema_200']
    
    # Premium/Discount
    df['range_100_high'] = df['high'].rolling(100).max()
    df['range_100_low'] = df['low'].rolling(100).min()
    df['range_100_mid'] = (df['range_100_high'] + df['range_100_low']) / 2
    df['is_discount'] = df['close'] < df['range_100_mid']
    df['is_premium'] = df['close'] > df['range_100_mid']
    
    # Volume
    df['vol_avg_20'] = df['tick_volume'].rolling(20).mean()
    
    # FVG & Displacement
    df['body'] = abs(df['close'] - df['open'])
    df['range_bar'] = df['high'] - df['low']
    df['avg_body_5'] = df['body'].rolling(5).mean().shift(1)
    
    # Conviction: Close in top/bottom 15%
    df['bullish_conviction'] = (df['close'] > df['open']) & (df['close'] >= df['high'] - (df['range_bar'] * 0.15))
    df['bearish_conviction'] = (df['close'] < df['open']) & (df['close'] <= df['low'] + (df['range_bar'] * 0.15))
    
    # FVG sizes
    df['fvg_bull_raw'] = (df['low'] - df['high'].shift(2)).clip(lower=0)
    df['fvg_bear_raw'] = (df['low'].shift(2) - df['high']).clip(lower=0)
    
    return df

class VectorizedObjective:
    def __init__(self, df):
        self.df = df

    def __call__(self, trial):
        min_fvg_atr = trial.suggest_float("min_fvg_atr", 0.6, 1.8, step=0.1)
        sl_buffer_atr = trial.suggest_float("sl_buffer_atr", 0.3, 1.5, step=0.1)
        displacement_body_mult = trial.suggest_float("displacement_body_mult", 1.5, 3.5, step=0.1)
        vol_surge_mult = trial.suggest_float("vol_surge_mult", 1.0, 2.0, step=0.1)
        sweep_lookback = trial.suggest_int("sweep_lookback", 15, 60, step=5)
        
        df = self.df
        mid_body = df['body'].shift(1)
        mid_range = df['range_bar'].shift(1)
        mid_conv_bull = df['bullish_conviction'].shift(1)
        mid_conv_bear = df['bearish_conviction'].shift(1)
        
        is_displaced = (mid_body >= df['avg_body_5'] * displacement_body_mult) & \
                      ((mid_body / mid_range.replace(0, 1)) >= 0.55)
        
        vol_surge = df['tick_volume'] > df['vol_avg_20'] * vol_surge_mult
        
        swing_high = df['high'].shift(2).rolling(sweep_lookback).max()
        swing_low = df['low'].shift(2).rolling(sweep_lookback).min()
        
        sweep_bull = (df['low'].rolling(5).min() < swing_low)
        sweep_bear = (df['high'].rolling(5).max() > swing_high)
        
        buy_sig = df['vol_healthy'] & df['uptrend_htf'] & df['is_discount'] & \
                    (df['fvg_bull_raw'] >= df['atr'] * min_fvg_atr) & is_displaced & sweep_bull & mid_conv_bull
                    
        sell_sig = df['vol_healthy'] & (~df['uptrend_htf']) & df['is_premium'] & \
                     (df['fvg_bear_raw'] >= df['atr'] * min_fvg_atr) & is_displaced & sweep_bear & mid_conv_bear

        trades = []
        equity = INITIAL_EQUITY
        peak_equity = INITIAL_EQUITY
        max_dd = 0.0
        
        sig_indices = np.where(buy_sig | sell_sig)[0]
        processed_until = 0
        spread_usd = SIM_SPREAD * 0.01 * CONTRACT_SIZE * 0.01 
        
        for idx in sig_indices:
            if idx <= processed_until: continue
            if idx >= len(df) - 50: break
            
            side = "BUY" if buy_sig[idx] else "SELL"
            row = df.iloc[idx]
            entry = float(row['close'])
            atr = float(row['atr'])
            
            if side == "BUY":
                lowest = df['low'].iloc[idx-4:idx+1].min()
                sl = lowest - (atr * sl_buffer_atr)
                tp = entry + (abs(entry - sl) * 1.5)
            else:
                highest = df['high'].iloc[idx-4:idx+1].max()
                sl = highest + (atr * sl_buffer_atr)
                tp = entry - (abs(entry - sl) * 1.5)
                
            risk = abs(entry - sl)
            if risk <= 0: continue
            
            future = df.iloc[idx+1:idx+51]
            pnl_usd = 0
            found_exit = False
            
            for _, f_row in future.iterrows():
                if side == "BUY":
                    if f_row['low'] <= sl: 
                        pnl_usd = (sl - entry) * 0.01 * CONTRACT_SIZE - spread_usd
                        found_exit = True; break
                    if f_row['high'] >= tp:
                        pnl_usd = (tp - entry) * 0.01 * CONTRACT_SIZE - spread_usd
                        found_exit = True; break
                else:
                    if f_row['high'] >= sl:
                        pnl_usd = (entry - sl) * 0.01 * CONTRACT_SIZE - spread_usd
                        found_exit = True; break
                    if f_row['low'] <= tp:
                        pnl_usd = (entry - tp) * 0.01 * CONTRACT_SIZE - spread_usd
                        found_exit = True; break
            
            if not found_exit:
                pnl_usd = (future.iloc[-1]['close'] - entry if side == "BUY" else entry - future.iloc[-1]['close']) * 0.01 * CONTRACT_SIZE - spread_usd
                
            trades.append(pnl_usd)
            equity += pnl_usd
            if equity > peak_equity: peak_equity = equity
            dd = (peak_equity - equity) / peak_equity * 100
            if dd > max_dd: max_dd = dd
            processed_until = idx + 20 

        if not trades: return -1000.0
        
        wins = [p for p in trades if p > 0]
        losses = [p for p in trades if p <= 0]
        wr = len(wins) / len(trades) * 100
        total_loss = abs(sum(losses))
        pf = sum(wins) / total_loss if total_loss > 0 else 10.0
        
        score = (wr * 2.0) + (pf * 50.0) + (len(trades) * 0.5)
        if max_dd > 6.0: score -= (max_dd - 6.0) * 100.0
        if len(trades) < 10: score -= 500.0
        
        return score

def main():
    if not mt5.initialize():
        print("FAIL MT5 Init"); return

    broker_sym = mapper.to_broker(SYMBOL)
    print(f"Fetching {BARS} bars for {broker_sym}...")
    rates = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M5, 0, BARS)
    if rates is None:
        print("FAIL No data"); return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = precompute_base_features(df)
    
    print(f"Data ready: {len(df)} rows. Starting Optuna...")
    
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize")
    study.optimize(VectorizedObjective(df), n_trials=50, show_progress_bar=True)
    
    print("\n" + "="*40)
    print("BEST PARAMETERS FOR USOIL (1 YEAR)")
    print("="*40)
    print(json.dumps(study.best_params, indent=2))
    print(f"Best Score: {study.best_value}")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
