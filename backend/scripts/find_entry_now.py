import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone

from app.strategy.templates.pullback_v2 import PullbackV2Strategy
from app.domain.models import SymbolProfile

def main():
    if not mt5.initialize():
        print("MT5 Init Failed")
        return

    symbol = "XAUUSDc"
    rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M5, datetime.now(timezone.utc), 150)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        print("No market data")
        return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    
    profile = SymbolProfile(symbol=symbol, contract_size=100.0, digits=2, point=0.01)
    
    strategy = PullbackV2Strategy()
    decision = strategy.analyze(df, profile)
    
    print("="*50)
    print(f"Latest Close: {df.iloc[-1]['close']}")
    print(f"Decision: {decision.action.value}")
    print(f"Confidence: {decision.confidence}")
    print(f"Reason: {decision.reason}")
    if decision.action.value != "HOLD":
        print(f"SL: {decision.stop_loss}")
        print(f"TP: {decision.take_profit}")
    print("="*50)

if __name__ == "__main__":
    main()
