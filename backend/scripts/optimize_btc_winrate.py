import sys
import os
import pandas as pd
import numpy as np
import optuna
import logging
import MetaTrader5 as mt5

# Add current directory to path (if running from backend/)
sys.path.append(os.getcwd())

from app.mt5.market_data import fetch_candles
from app.strategy.templates.alpha_v6_smc import AlphaV6SMCStrategy
from app.execution.backtester import Backtester
from app.domain.models import SymbolProfile

# Setup logging
logging.basicConfig(level=logging.ERROR)

def objective(trial):
    smc_pivot = trial.suggest_int("smc_pivot", 5, 20)
    ssl_baseline = trial.suggest_int("ssl_baseline", 50, 400)
    tp_mult = trial.suggest_float("tp_mult", 1.2, 4.0, step=0.1)
    mss_displacement = trial.suggest_float("mss_displacement", 0.5, 3.0, step=0.1)
    adx_threshold = trial.suggest_int("adx_threshold", 15, 30)
    risk_per_trade = trial.suggest_float("risk_per_trade", 0.01, 0.03, step=0.005)
    
    if not mt5.initialize():
        return 0.0
        
    rates = mt5.copy_rates_from_pos("BTCUSDm", mt5.TIMEFRAME_M15, 0, 5000)
    if rates is None or len(rates) < 200:
        return 0.0
        
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("time", inplace=True)
    if "tick_volume" in df.columns:
        df["volume"] = df["tick_volume"]

    strategy = AlphaV6SMCStrategy(
        symbol="BTCUSDm",
        smc_pivot=smc_pivot,
        ssl_baseline=ssl_baseline,
        tp_mult=tp_mult,
        mss_displacement=mss_displacement,
        adx_threshold=adx_threshold
    )
    strategy.risk_per_trade = risk_per_trade
    
    # EXNESS STANDARD: $0 Commission, but we add slippage (10-20 points)
    # We increase max_dd_limit to 50% for the OPTIMIZER so it can finish trials even if drawdown is bad
    bt = Backtester(
        strategy=strategy, 
        initial_equity=100.0, 
        commission_per_lot=0.0, 
        slippage_points=10.0, # 10 points = 0.10 price difference
        max_dd_limit=0.50 
    )
    
    result = bt.run(df, symbol="BTCUSDm", contract_size=1.0, point=0.01)
    
    wr = result.win_rate
    pf = result.profit_factor
    dd = result.max_drawdown_pct
    total_trades = result.total_trades
    
    if total_trades < 3: 
        return 0.0
        
    # SCORE LOGIC: Target 50-70% WR, < 6% DD
    score = wr * 10 if wr >= 50 else wr
    
    # Drawdown Penalty (Exponential for $100 account)
    if dd > 15:
        score -= (dd - 15) * 500 
    elif dd > 6:
        score -= (dd - 6) * 100 
    elif dd <= 4:
        score += 100 # Bonus for safety
        
    # PF Bonus (More aggressive)
    score += (pf * 100)
    
    # Trade frequency bonus (Avoid no-trade strategies)
    score += min(total_trades, 15) * 5

    return score

if __name__ == "__main__":
    print("🚀 Starting BTC Winrate Optimization ($100 Capital, Zero-Comm, 5000 Bars)...")
    if not mt5.initialize():
        print("MT5 initialization failed")
        sys.exit()
        
    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=100) # Deep search
    
    print("\n" + "="*50)
    print("BEST PARAMETERS FOR $100 ACCOUNT (EXNESS STANDARD)")
    print("="*50)
    best_params = study.best_params
    for k, v in best_params.items():
        print(f"{k}: {v}")
    
    print(f"Best Score: {study.best_value:.2f}")
    print("="*50)
