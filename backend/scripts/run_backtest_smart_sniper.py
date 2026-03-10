"""
Targeted Backtest: SmartSniper vs SniperStrategy.
Compare performance of the new Smart strategy against the original.

Usage:
    python backend/scripts/run_backtest_smart_sniper.py
"""

import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add backend to path (d:\VibeCode\Trade\backend)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import logging

# pandas_ta compat (Fix for AttributeError: 'Series' object has no attribute 'category')
try:
    _orig_setattr = pd.Series.__setattr__
    def _safe_setattr(self, name, value):
        if name == "category" and isinstance(value, str):
            try: _orig_setattr(self, name, value)
            except (AttributeError, ValueError): pass
        else: _orig_setattr(self, name, value)
    pd.Series.__setattr__ = _safe_setattr
except Exception:
    pass

from app.core.logging import get_logger
from app.execution.backtester import Backtester
from app.strategy.templates.smart_sniper import SmartSniper
from app.strategy.templates.sniper import SniperStrategy

# Setup Logger
logger = get_logger("BacktestSmartSniper")
logging.getLogger().setLevel(logging.INFO)

def run_backtest_comparison():
    SYMBOL = "XAUUSDc"
    DAYS = 60 # 2 Months
    
    print(f"\n{'='*70}")
    print(f"  ⚔️ SMART SNIPER vs CLASSIC SNIPER Comparison")
    print(f"  Symbol: {SYMBOL} | Period: {DAYS} days")
    print(f"  Goal: Verify if 'Smart' filters improve PF/WinRate")
    print(f"{'='*70}\n")
    
    # 1. Connect MT5
    if not mt5.initialize():
        print(f"[ERROR] MT5 not connected: {mt5.last_error()}")
        return

    # 2. Fetch Data
    print(f"  📥 Fetching M15 Data...", flush=True)
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M15, utc_from, utc_to)
    
    if rates is None or len(rates) == 0:
        print(f"[ERROR] No data fetched for {SYMBOL}")
        return

    candles = pd.DataFrame(rates)
    candles["time"] = pd.to_datetime(candles["time"], unit="s")
    print(f"     Fetched {len(candles)} bars.", flush=True)

    # 3. Setup Strategies
    # Note: Settings() defaults are used for both
    from app.core.config import Settings
    settings = Settings()
    
    classic_sniper = SniperStrategy()
    smart_sniper = SmartSniper(settings)
    
    strategies = [
        ("Classic Sniper", classic_sniper),
        ("Smart Sniper", smart_sniper)
    ]
    
    results = []

    # 4. Run Backtests
    for name, strat in strategies:
        print(f"\n  ▶️ Testing: {name}...", flush=True)
        bt = Backtester(
            strategy=strat,
            initial_equity=1000.0, 
            risk_per_trade=0.02,
            warmup_bars=200 # Need EMA200
        )
        
        try:
            res = bt.run(candles, symbol=SYMBOL)
            results.append((name, res))
            
            print(f"     ✅ T:{res.total_trades} WR:{res.win_rate}% PF:{res.profit_factor} DD:{res.max_drawdown_pct}% PnL:${res.total_profit_usd}")
            
        except Exception as e:
            print(f"     ❌ Error: {e}")
            import traceback
            traceback.print_exc()

    # 5. Compare Results
    print(f"\n\n{'='*70}")
    print(f"  🏆 COMPARISON RESULT")
    print(f"{'='*70}")
    
    print(f"{'Strategy':<20} | {'Trades':<6} | {'WinRate':<7} | {'PF':<5} | {'DD%':<5} | {'PnL ($)':<10}")
    print(f"{'-'*20}-+-{'-'*6}-+-{'-'*7}-+-{'-'*5}-+-{'-'*5}-+-{'-'*10}")
    
    for name, res in results:
        print(f"{name:<20} | {res.total_trades:<6} | {res.win_rate:<7} | {res.profit_factor:<5} | {res.max_drawdown_pct:<5} | {res.total_profit_usd:<10}")

    mt5.shutdown()

if __name__ == "__main__":
    run_backtest_comparison()
