import sys
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import optuna

# Setup paths
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
from app.execution.backtester import Backtester
from app.strategy.templates.alpha_v6_smc import AlphaV6SMCStrategy
from app.core.logging import get_logger

# Mute heavy logging
import logging
for log_name in ["app.core.logging", "app.risk", "app.risk.sizing", "FullBacktest", "__main__"]:
    logging.getLogger(log_name).setLevel(logging.ERROR)

def fetch_data(symbol: str, tf, days: int):
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, tf, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df

class Objective:
    def __init__(self, symbol, df, initial_equity):
        self.symbol = symbol
        self.df = df
        self.initial_equity = initial_equity

    def __call__(self, trial):
        # Hyperparameters Search Space
        smc_pivot = trial.suggest_int('smc_pivot', 3, 20)
        ssl_baseline = trial.suggest_int('ssl_baseline', 20, 150)
        tp_mult = trial.suggest_float('tp_mult', 1.0, 4.0, step=0.5)

        # Initialize Strategy with suggested params
        strategy = AlphaV6SMCStrategy(
            symbol=self.symbol,
            smc_pivot=smc_pivot, 
            ssl_baseline=ssl_baseline, 
            tp_mult=tp_mult
        )
        
        bt = Backtester(strategy, initial_equity=self.initial_equity)
        
        try:
            res = bt.run(self.df, self.symbol)
        except Exception:
            return -9999.0 # Penalty for crash
            
        # Target Function: Maximize Profit Factor, but heavily penalize low win rates or massive drawdowns
        if res.total_trades < 15: # Not enough statistical significance
            return -100.0
            
        score = 0
        if res.win_rate >= 45:
            score += res.win_rate
        else:
            score -= (45 - res.win_rate) * 2
            
        if res.profit_factor >= 1.2:
            score += res.profit_factor * 20
        else:
            score -= (1.2 - res.profit_factor) * 50
            
        if res.max_drawdown_pct > 15.0:
            score -= res.max_drawdown_pct * 2
            
        return score

def main():
    if not mt5.initialize():
        print("❌ MT5 Init failed")
        sys.exit(1)

    symbols_to_test = ["XAGUSDm", "BTCUSDm"]
    days = 15
    initial_equity = 10000.0

    best_configurations = {}

    for symbol in symbols_to_test:
        print(f"\n🚀 Running Optuna Machine Learning on {symbol} ({days} Days)...")
        df = fetch_data(symbol, mt5.TIMEFRAME_M15, days)
        if df is None:
            print(f"❌ No data for test symbol: {symbol}")
            continue

        print(f"✅ Loaded {len(df)} candles. Starting Trials...")

        objective = Objective(symbol, df, initial_equity)
        study = optuna.create_study(direction='maximize', study_name=f"AlphaV6SMC_{symbol}")
        
        # Suppress optuna stdout slightly
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        
        study.optimize(objective, n_trials=10, show_progress_bar=True)

        if len(study.trials) > 0:
            best_trial = study.best_trial
            print(f"\n🏆 Best Result for {symbol}:")
            print(f"Score:  {best_trial.value:.2f}")
            print("Optimal Parameters:")
            for key, value in best_trial.params.items():
                print(f"  {key}: {value}")
                
            best_configurations[symbol] = best_trial.params
        else:
            print(f"❌ Failed to find optimal configuration for {symbol}")

    print("\n==================================")
    print("OPTUNA RESULTS SUMMARY:")
    for sym, params in best_configurations.items():
        print(f"{sym}: {params}")
    print("==================================")
        
    mt5.shutdown()

if __name__ == "__main__":
    main()
