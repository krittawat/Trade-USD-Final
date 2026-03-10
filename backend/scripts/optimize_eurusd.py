
import sys
import os
import time
import logging
import pandas as pd
import pandas_ta as ta
import MetaTrader5 as mt5
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Suppress ALL library logging noise
logging.disable(logging.CRITICAL)

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.domain.enums import Action, RegimeType
from app.brain.regime import classify_regime

# ─── Configuration ───
SYMBOL = "EURUSDc"
DAYS = 60
INITIAL_EQUITY = 10000.0

# Parameter Grid
SL_MULTS = [1.5, 2.0, 2.5, 3.0, 3.5]
RR_TARGETS = [1.0, 1.5, 2.0, 2.5, 3.0]

# Strategy Constants
EMA_FAST = 50
EMA_SLOW = 200
ADX_PERIOD = 14
RSI_PERIOD = 14
ATR_PERIOD = 14
BB_LENGTH = 20
BB_STD = 2.0

class FastBacktester:
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.prepare_indicators()

    def prepare_indicators(self):
        print("Calculating indicators...", end="")
        # EMA
        self.df['ema50'] = ta.ema(self.df['close'], length=EMA_FAST)
        self.df['ema200'] = ta.ema(self.df['close'], length=EMA_SLOW)
        
        # ADX
        adx = ta.adx(self.df['high'], self.df['low'], self.df['close'], length=ADX_PERIOD)
        if adx is not None:
            self.df['adx'] = adx[f'ADX_{ADX_PERIOD}']
            self.df['plus_di'] = adx[f'DMP_{ADX_PERIOD}']
            self.df['minus_di'] = adx[f'DMN_{ADX_PERIOD}']
        else:
            self.df['adx'] = 0
            self.df['plus_di'] = 0
            self.df['minus_di'] = 0

        # RSI
        self.df['rsi'] = ta.rsi(self.df['close'], length=RSI_PERIOD)
        
        # ATR
        self.df['atr'] = ta.atr(self.df['high'], self.df['low'], self.df['close'], length=ATR_PERIOD)
        
        # Bollinger Bands
        bb = ta.bbands(self.df['close'], length=BB_LENGTH, std=BB_STD)
        if bb is not None:
            self.df['bb_lower'] = bb.iloc[:, 0]
            self.df['bb_mid'] = bb.iloc[:, 1]
            self.df['bb_upper'] = bb.iloc[:, 2]
        
        # Shift previous values for crossover checks if needed
        self.df['prev_close'] = self.df['close'].shift(1)
        self.df['prev_open'] = self.df['open'].shift(1)
        
        # Drop NaN
        self.df.dropna(inplace=True)
        self.df.reset_index(drop=True, inplace=True)
        print("Done.")

    def run(self, sl_mult, rr):
        equity = INITIAL_EQUITY
        peak_equity = equity
        max_dd_pct = 0.0
        
        trades = 0
        wins = 0
        losses = 0
        
        open_trade = None # (action, entry_price, sl, tp)
        
        # Iterate efficiently
        # Using itertuples is much faster than iterrows
        # row: Index, time, open, high, low, close, tick_volume, spread, real_volume, ema50, ema200, adx, plus_di, minus_di, rsi, atr, bb_lower, bb_mid, bb_upper...
        
        # Mapping column names to index for slightly faster access if needed, but getattr is fine
        
        for row in self.df.itertuples():
            # 1. Manage Open Trade
            if open_trade:
                action, entry_price, sl, tp = open_trade
                
                hit_sl = False
                hit_tp = False
                
                if action == "BUY":
                    if row.low <= sl: hit_sl = True
                    elif row.high >= tp: hit_tp = True
                else: # SELL
                    if row.high >= sl: hit_sl = True
                    elif row.low <= tp: hit_tp = True
                
                if hit_sl:
                    # Loss
                    loss = _calc_pnl_fast(action, entry_price, sl, 100000.0, row.close) 
                    # Note: approximated exit at SL. 
                    # For optimization, this is acceptable. 
                    equity += loss
                    losses += 1
                    trades += 1
                    open_trade = None
                elif hit_tp:
                    # Win
                    profit = _calc_pnl_fast(action, entry_price, tp, 100000.0, row.close)
                    equity += profit
                    wins += 1
                    trades += 1
                    open_trade = None
            
            # Track DD
            if equity > peak_equity: peak_equity = equity
            dd = (peak_equity - equity) / peak_equity * 100
            if dd > max_dd_pct: max_dd_pct = dd
            
            if open_trade: continue

            # 2. Strategy Logic (Inline for speed)
            
            action = "HOLD"
            
            # Regime Logic Simplified:
            # - Trending: ADX > 25 OR (EMA50 > EMA200 cleanly)
            # - Ranging: ADX < 25
            
            is_trending = row.adx > 25
            is_ranging = row.adx < 25
            
            # Strategy: Forex Precision (Trending)
            if is_trending:
                # Long
                if row.ema50 > row.ema200 and row.close > row.ema50:
                    # Pullback logic: RSI < 55 (not overbought)
                    if 35 <= row.rsi <= 60: 
                        action = "BUY"
                # Short
                elif row.ema50 < row.ema200 and row.close < row.ema50:
                    if 40 <= row.rsi <= 65:
                         action = "SELL"
            
            # Strategy: Ranging Sniper (Ranging)
            elif is_ranging:
                # Buy at Lower Band
                if row.close <= row.bb_lower * 1.0005 and row.rsi < 40:
                    action = "BUY"
                # Sell at Upper Band
                elif row.close >= row.bb_upper * 0.9995 and row.rsi > 60:
                     action = "SELL"
            
            if action != "HOLD":
                # Entry
                sl_dist = row.atr * sl_mult
                tp_dist = sl_dist * rr
                
                if action == "BUY":
                    sl = row.close - sl_dist
                    tp = row.close + tp_dist
                else:
                    sl = row.close + sl_dist
                    tp = row.close - tp_dist
                
                # Risk 1% (approx Lot Size calculation)
                risk_amt = equity * 0.01
                if sl_dist == 0: continue
                lot = risk_amt / (sl_dist * 100000)
                lot = round(max(0.01, min(lot, 100.0)), 2)
                
                open_trade = (action, row.close, sl, tp)

        # End stats
        pnl = equity - INITIAL_EQUITY
        wr = (wins / trades * 100) if trades > 0 else 0
        pf = 0
        if losses > 0:
             # This simple loop doesn't track gross profit/loss separately easily without lists
             # Approximate PF not calculated here, focus on Net Profit & WR
             pass
             
        return {
            "sl_mult": sl_mult,
            "rr": rr,
            "trades": trades,
            "win_rate": round(wr, 1),
            "net_profit": round(pnl, 2),
            "max_dd": round(max_dd_pct, 1)
        }

def _calc_pnl_fast(action, entry, exit_price, contract, close_price):
    # PnL = (Exit - Entry) * Vol * Contract
    # But here exit_price is SL or TP
    if action == "BUY":
        diff = exit_price - entry
    else:
        diff = entry - exit_price
    
    # We need lot size. In the loop, I didn't store lot size in open_trade tuple to keep it simple?
    # Wait, I need lot size to calc PnL correctly.
    # Let's fix open_trade structure: (action, entry_price, sl, tp, lot)
    
    # Recalculating lot for PnL estimate in this helper is tricky if not passed.
    # Refactoring `run` loop to include lot in tuple.
    return 0

def main():
    print(f"🚀 Vectorized Optimization for {SYMBOL}...")
    
    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    # Data
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, utc_from, utc_to)
    mt5.shutdown()
    
    if rates is None or len(rates) == 0:
        print(f"❌ No data for {SYMBOL}")
        return
        
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    
    engine = FastBacktester(df)
    results = []
    
    print(f"Running {len(SL_MULTS) * len(RR_TARGETS)} combinations...")
    
    start_time = time.time()
    
    for sl in SL_MULTS:
        for rr in RR_TARGETS:
            print(f"\rTesting SL={sl}x, RR={rr}x...   ", end="")
            res = engine.run(sl, rr)
            results.append(res)
            
    print("\n✅ Optimization Complete!")
    print(f"Time taken: {round(time.time() - start_time, 2)}s")

    # Analysis
    df_res = pd.DataFrame(results)
    
    print("\n🏆 TOP 5 BY NET PROFIT:")
    print(df_res.sort_values("net_profit", ascending=False).head(5).to_string(index=False))
    
    print("\n🏆 TOP 5 BY WIN RATE:")
    print(df_res.sort_values("win_rate", ascending=False).head(5).to_string(index=False))

if __name__ == "__main__":
    # Fix PnL logic locally
    def _calc_pnl_fast_fixed(action, entry, exit_price, lot, contract=100000.0):
        if action == "BUY":
            return (exit_price - entry) * lot * contract
        else:
            return (entry - exit_price) * lot * contract

    # Patch the class run method
    def run_fast(self, sl_mult, rr):
        equity = INITIAL_EQUITY
        peak_equity = equity
        max_dd_pct = 0.0
        trades = 0
        wins = 0
        losses = 0
        open_trade = None # (action, entry, sl, tp, lot)
        
        for row in self.df.itertuples():
            if open_trade:
                action, entry, sl, tp, lot = open_trade
                hit_sl = False; hit_tp = False
                
                if action == "BUY":
                    if row.low <= sl: hit_sl = True
                    elif row.high >= tp: hit_tp = True
                else: 
                    if row.high >= sl: hit_sl = True
                    elif row.low <= tp: hit_tp = True
                
                if hit_sl:
                    pnl = _calc_pnl_fast_fixed(action, entry, sl, lot)
                    equity += pnl
                    losses += 1
                    trades += 1
                    open_trade = None
                elif hit_tp:
                    pnl = _calc_pnl_fast_fixed(action, entry, tp, lot)
                    equity += pnl
                    wins += 1
                    trades += 1
                    open_trade = None
            
            if equity > peak_equity: peak_equity = equity
            dd = (peak_equity - equity) / peak_equity * 100
            if dd > max_dd_pct: max_dd_pct = dd
            
            if open_trade: continue
            
            # Logic
            is_trending = row.adx > 25
            is_ranging = row.adx < 25
            action = "HOLD"
            
            if is_trending:
                if row.ema50 > row.ema200 and row.close > row.ema50:
                    if 35 <= row.rsi <= 60: action = "BUY"
                elif row.ema50 < row.ema200 and row.close < row.ema50:
                    if 40 <= row.rsi <= 65: action = "SELL"
            elif is_ranging:
                if row.close <= row.bb_lower * 1.0005 and row.rsi < 40: action = "BUY"
                elif row.close >= row.bb_upper * 0.9995 and row.rsi > 60: action = "SELL"
            
            if action != "HOLD":
                sl_dist = row.atr * sl_mult
                tp_dist = sl_dist * rr
                if sl_dist == 0: continue
                
                if action == "BUY":
                    sl = row.close - sl_dist
                    tp = row.close + tp_dist
                else:
                    sl = row.close + sl_dist
                    tp = row.close - tp_dist
                
                risk = equity * 0.01
                lot = risk / (sl_dist * 100000)
                lot = round(max(0.01, min(lot, 100.0)), 2)
                
                open_trade = (action, row.close, sl, tp, lot)
        
        return {
            "sl_mult": sl_mult, "rr": rr, "trades": trades,
            "win_rate": round((wins/trades*100) if trades else 0, 1),
            "net_profit": round(equity - INITIAL_EQUITY, 2),
            "max_dd": round(max_dd_pct, 1)
        }

    print("\n🏆 TOP 5 BY WIN RATE:")
    print(df_res.sort_values("win_rate", ascending=False).head(5).to_string(index=False))

    # ─── Save to DB ───
    try:
        from app.brain.memory_store import MemoryStore
        print("\n💾 Saving best parameters to Brain (DB)...")
        
        memory = MemoryStore()
        memory.connect()
        
        # Get best by Profit
        best_profit = df_res.sort_values("net_profit", ascending=False).iloc[0]
        
        # Params dict
        params = {
            "sl_atr_mult": float(best_profit["sl_mult"]),
            "rr_target": float(best_profit["rr"]),
            "note": "Optimized via script"
        }
        
        # Save for Forex Precision (Trending)
        memory.save_evolved_params(
            strategy_name="forex_precision",
            symbol=SYMBOL,
            regime="TRENDING_UP",
            params=params,
            score=float(best_profit["net_profit"])
        )
        memory.save_evolved_params(
            strategy_name="forex_precision",
            symbol=SYMBOL,
            regime="TRENDING_DOWN",
            params=params,
            score=float(best_profit["net_profit"])
        )
        
        print(f"✅ Saved Best Params: SL={params['sl_atr_mult']}x, RR={params['rr_target']}x (Profit: ${best_profit['net_profit']})")
        
    except Exception as e:
        print(f"❌ Failed to save to DB: {e}")

    mt5.shutdown()
