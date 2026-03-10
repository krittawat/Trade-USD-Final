
import sys
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import pandas_ta as ta
import MetaTrader5 as mt5

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.execution.backtester import Backtester, BacktestTrade, BacktestResult
from app.domain.models import SymbolProfile, AccountState, Decision
from app.strategy.templates.usdjpy_smart import UsdJpySmartStrategy
from app.risk.sizing import calculate_lot_size

# Suppress warnings
logging.disable(logging.CRITICAL)

def precalculate_indicators(df, strategy):
    """Pre-calculate all indicators needed by strategy."""
    p = strategy.params
    
    # Calculate using pandas_ta
    # This appends columns to df in-place if using df.ta.ema(append=True)
    # OR assigns if using functional.
    
    # Functional approach for control
    df[f"ema_{p['ema_fast']}"] = ta.ema(df["close"], length=p['ema_fast'])
    df[f"ema_{p['ema_slow']}"] = ta.ema(df["close"], length=p['ema_slow'])
    df[f"rsi_{p['rsi_period']}"] = ta.rsi(df["close"], length=p['rsi_period'])
    df[f"atr_{p['atr_period']}"] = ta.atr(df["high"], df["low"], df["close"], length=p['atr_period'])
    
    # ADX returns a DataFrame
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=p['adx_period'])
    if adx_df is not None:
        # Join columns: ADX_14, DMP_14, DMN_14
        df = df.join(adx_df)
        
    return df

def run_backtest(strategy, symbol="USDJPYc", days=100, show_trades=False):
    """Run a single backtest pass."""
    
    # 1. Copty Data
    if not mt5.initialize():
        print("MT5 Init Failed")
        return None

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        print(f"No data for {symbol}")
        return None
        
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    
    # 2. Setup
    profile = SymbolProfile(
        symbol=symbol,
        digits=3, # JPY
        point=0.001,
        contract_size=100000.0,
        spread_avg=10,
        volume_min=0.01, volume_max=100.0, volume_step=0.01
    )
    
    # Pre-calculate Indicators
    # print("Calculating indicators...")
    df = precalculate_indicators(df, strategy)
    # print("Indicators done.")
    
    initial_equity = 70.0
    equity = initial_equity
    peak_equity = equity
    max_dd_pct = 0.0
    
    trades = []
    open_trade = None
    
    # print(f"Running Backtest: {symbol} | {days} Days | {len(df)} Candles")
    
    # 3. Main Loop
    # Only iterate where we have enough history for indicators (200 + buffer)
    start_idx = 300
    
    # Optimization: Iterate over rows using itertuples for speed?
    # Or just index since we need window slicing?
    # Actually, now that indicators are in DF, we just need the Current Row + History for context if needed?
    # Strategy analyze expects a dataframe. 
    # If we pass a slice, it copies data.
    # To be super fast: pass the FULL DF and the CURRENT INDEX.
    # But BaseStrategy interface usually receives a dataframe `candles`.
    # UsdJpySmartStrategy checks last row of received df.
    # So we must pass a slice that includes the last row as "current".
    # Slice is df.iloc[start:i+1].
    # Slicing is still copy overhead but better than re-calc.
    
    total_len = len(df)
    
    for i in range(start_idx, total_len):
        # Window of 500 is enough for any lookback logic slightly beyond indicators
        # Strategy expects at least EMA_SLOW (200) + buffer
        window = df.iloc[max(0, i-300):i+1]
        
        # Current bar info
        bar = df.iloc[i]
        bar_time = bar["time"]
        close = bar["close"]
        high = bar["high"]
        low = bar["low"]
        
        # --- Check Exit ---
        if open_trade:
            # Check SL/TP
            sl_hit = False
            tp_hit = False
            exit_price = 0.0
            
            if open_trade.action == "BUY":
                if low <= open_trade.sl:
                    sl_hit = True
                    exit_price = open_trade.sl
                elif high >= open_trade.tp:
                    tp_hit = True
                    exit_price = open_trade.tp
            elif open_trade.action == "SELL":
                if high >= open_trade.sl:
                    sl_hit = True
                    exit_price = open_trade.sl
                elif low <= open_trade.tp:
                    tp_hit = True
                    exit_price = open_trade.tp
                    
            if sl_hit or tp_hit:
                open_trade.exit_time = bar_time
                open_trade.exit_price = exit_price
                open_trade.exit_reason = "SL" if sl_hit else "TP"
                
                # Calc PnL
                if open_trade.action == "BUY":
                    profit = (exit_price - open_trade.entry_price) * open_trade.lot_size * profile.contract_size
                else:
                    profit = (open_trade.entry_price - exit_price) * open_trade.lot_size * profile.contract_size
                    
                open_trade.profit_usd = profit
                equity += profit
                trades.append(open_trade)
                open_trade = None
                
                # Update Max DD
                if equity > peak_equity: peak_equity = equity
                dd = (peak_equity - equity) / peak_equity * 100
                if dd > max_dd_pct: max_dd_pct = dd
                
                continue # Trade closed, wait for next bar (no immediate re-entry same bar)

        # --- Check Entry ---
        if not open_trade:
            try:
                # Optimized: window has pre-calculated columns
                decision = strategy.analyze(window, profile)
                
                if decision.action.value in ("BUY", "SELL") and decision.confidence >= strategy.params["min_confidence"]:
                    # print(f"DEBUG: Signal Accepted {decision.action} at {bar_time}")
                    # Execute
                    entry_price = close
                    sl = decision.stop_loss
                    tp = decision.take_profit
                    
                    # Risk Manager (Simple fixed lot or % based)
                    # For speed, let's use fixed 0.1 or simple calc
                    risk_usd = equity * 0.01 # 1% risk
                    dist_to_sl = abs(entry_price - sl)
                    
                    if dist_to_sl == 0: 
                        # print("DEBUG: SL distance 0")
                        continue
                        
                    pip_value = profile.contract_size * profile.point
                    lot_size = risk_usd / (dist_to_sl * profile.contract_size)
                    raw_lot = lot_size
                    lot_size = round(lot_size, 2)
                    
                    if lot_size < 0.01: 
                        # print(f"DEBUG: Lot too small {raw_lot} -> 0.01")
                        lot_size = 0.01
                    
                    # print(f"DEBUG: Opening Trade {lot_size} lots")
                    
                    open_trade = BacktestTrade(
                        trade_id=len(trades)+1,
                        symbol=symbol,
                        strategy=strategy.name,
                        action=decision.action.value,
                        entry_price=entry_price,
                        entry_time=bar_time,
                        sl=sl,
                        tp=tp,
                        lot_size=lot_size,
                        regime="TREND"
                    )
            except Exception as e:
                # print(f"Error: {e}")
                pass
        
        # Debug: Print first 5 rejections
        if not open_trade and i % 1000 == 0:
             try:
                 d = decision
                 print(f"Bar {i}: {d.action} | {d.reason} | Conf: {d.confidence}")
             except: pass

                
    # End Loop - Close open
    if open_trade:
        # Close at last price
        exit_price = df.iloc[-1]["close"]
        open_trade.exit_price = exit_price
        open_trade.exit_time = df.iloc[-1]["time"]
        open_trade.exit_reason = "END"
        
        if open_trade.action == "BUY":
            profit = (exit_price - open_trade.entry_price) * open_trade.lot_size * profile.contract_size
        else:
            profit = (open_trade.entry_price - exit_price) * open_trade.lot_size * profile.contract_size
        
        open_trade.profit_usd = profit
        equity += profit
        trades.append(open_trade)

    # Stats
    wins = len([t for t in trades if t.profit_usd > 0])
    total = len(trades)
    wr = (wins / total * 100) if total > 0 else 0
    
    gross_profit = sum([t.profit_usd for t in trades if t.profit_usd > 0])
    gross_loss = abs(sum([t.profit_usd for t in trades if t.profit_usd < 0]))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 0
    
    net_profit = equity - initial_equity
    
    return {
        "trades": total,
        "win_rate": wr,
        "profit_factor": pf,
        "net_profit": net_profit,
        "max_drawdown": max_dd_pct,
        "params": strategy.params.copy()
    }

def optimize():
    print("Starting Optimization...")
    
    # Grid Search Ranges (High WR Focus)
    sl_mults = [1.5, 2.0, 2.5]
    tp_mults = [1.0, 1.5, 2.0, 3.0] # Lower TP = Higher WR
    adx_thresholds = [20, 25, 30]   # Stronger trend required?
    
    results = []
    
    total_iterations = len(sl_mults) * len(tp_mults) * len(adx_thresholds)
    count = 0
    
    best_pf = 0
    best_params = {}
    
    for sl in sl_mults:
        for tp in tp_mults:
            for adx in adx_thresholds:
                count += 1
                curr_params = {
                    "sl_atr_mult": sl,
                    "tp_atr_mult": tp,
                    "adx_threshold": adx
                }
                
                strat = UsdJpySmartStrategy()
                strat.update_parameters(curr_params)
                
                print(f"[{count}/{total_iterations}] Test: SL={sl}, TP={tp}, ADX={adx}...", end="", flush=True)
                
                # Shorter period for optimization
                res = run_backtest(strat, days=60) 
                
                if res:
                    print(f" -> WR: {res['win_rate']:.1f}%, PF: {res['profit_factor']:.2f}, Profit: ${res['net_profit']:.2f}")
                    
                    if res['trades'] > 10: # Min trades filter
                        results.append(res)
                        if res['profit_factor'] > best_pf:
                            best_pf = res['profit_factor']
                            best_params = curr_params
                else:
                     print(" -> No Data/Error")

    print(f"\nOptimization Complete! Best PF: {best_pf}")
    print(f"Best Params: {best_params}")
    
    # Save results
    results_df = pd.DataFrame(results)
    # Extract params to cols
    if not results_df.empty:
        results_df["sl"] = results_df["params"].apply(lambda p: p["sl_atr_mult"])
        results_df["tp"] = results_df["params"].apply(lambda p: p["tp_atr_mult"])
        results_df["adx"] = results_df["params"].apply(lambda p: p["adx_threshold"])
        results_df = results_df.drop(columns=["params"])
        
        results_df = results_df.sort_values(by="profit_factor", ascending=False)
        print(results_df.head(10))
        
        output_path = "backend/data/optimization_usdjpy.csv"
        # Ensure dir exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        results_df.to_csv(output_path, index=False)
        print(f"Saved to {output_path}")

if __name__ == "__main__":
    mode = "run" 
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        
    if mode == "optimize":
        optimize()
    else:
        # Single Run
        strat = UsdJpySmartStrategy()
        # Ensure default params or custom
        res = run_backtest(strat, days=100, show_trades=True)
        if res:
            print("\nFinal Results:")
            print(f"Trades: {res['trades']}")
            print(f"Win Rate: {res['win_rate']:.2f}%")
            print(f"Profit Factor: {res['profit_factor']:.2f}")
            print(f"Net Profit: ${res['net_profit']:.2f}")
            print(f"Max DD: {res['max_drawdown']:.2f}%")
