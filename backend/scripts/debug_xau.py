import sys
import os
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import MetaTrader5 as mt5
from app.strategy.templates.gold_smart_money import GoldSmartMoneyStrategy
from app.domain.models import SymbolProfile

def run_debug():
    if not mt5.initialize():
        print("MT5 Init failed")
        return
        
    symbol = "XAUUSDc"
    tf = mt5.TIMEFRAME_M15
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=30)
    rates = mt5.copy_rates_range(symbol, tf, start_date, end_date)
    mt5.shutdown()
    
    if rates is None or len(rates) == 0:
        print("No data")
        return
        
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    
    strat = GoldSmartMoneyStrategy()
    # Relax conditions:
    strat.p["min_confluence"] = 3
    strat.p["sweep_wick_min"] = 0.2
    
    profile = SymbolProfile(symbol=symbol)
    
    holds = {}
    buys = 0
    sells = 0
    
    for i in range(850, len(df)):
        window = df.iloc[i-850:i+1].copy()
        decision = strat.analyze(window, profile)
        
        if decision.action.value == "HOLD":
            reason = decision.reason
            # Aggregate reasons to see what's blocking everything
            if "Session filter" in reason:
                cat = "Session filter"
            elif "chop/sideways" in reason:
                cat = "Regime filter"
            elif "Score" in reason:
                cat = "Score too low"
            elif "Data insufficient" in reason:
                cat = "Data insufficient"
            else:
                cat = reason[:50]
                
            holds[cat] = holds.get(cat, 0) + 1
        elif decision.action.value == "BUY":
            buys += 1
        elif decision.action.value == "SELL":
            sells += 1
            
    print(f"Buys: {buys}, Sells: {sells}")
    print("Hold Reasons:")
    for k, v in holds.items():
        print(f"  {k}: {v}")

if __name__ == "__main__":
    run_debug()
