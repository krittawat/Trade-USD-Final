
import sys
import os
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, timezone
import pandas_ta as ta

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../backend')))

from app.strategy.templates.forex_precision import ForexPrecisionStrategy
from app.domain.models import SymbolProfile, RegimeType

def debug_usdjpy():
    if not mt5.initialize():
        print("MT5 init failed")
        return

    symbol = "USDJPYm"
    print(f"DEBUG: Fetching data for {symbol}...")
    
    # Fetch 2000 bars
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 2000)
    if rates is None:
        print("No data")
        return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    
    print(f"DEBUG: Data fetched. Last close: {df['close'].iloc[-1]}")
    
    # Calculate indicators manually to double check
    df["EMA_50"] = ta.ema(df["close"], length=50)
    df["EMA_200"] = ta.ema(df["close"], length=200)
    df["ATR"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    
    print(f"DEBUG: Last ATR: {df['ATR'].iloc[-1]}")
    
    profile = SymbolProfile(
        symbol=symbol,
        digits=3,
        point=0.001,
        contract_size=100000.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01,
        spread_avg=10, trade_mode=0, currency_profit="JPY"
    )
    
    strategy = ForexPrecisionStrategy()
    
    print("DEBUG: Running analyze() on last 50 bars...")
    for i in range(1950, 2000):
        window = df.iloc[:i+1]
        decision = strategy.analyze(window, profile, regime=RegimeType.TRENDING_UP)
        if decision.action != "HOLD":
            print(f"[{i}] ACTION: {decision.action} | Conf: {decision.confidence} | Reason: {decision.reason}")
            print(f"    SL: {decision.stop_loss} | TP: {decision.take_profit}")
        else:
            pass
            # print(f"[{i}] HOLD: {decision.reason}")

    mt5.shutdown()

if __name__ == "__main__":
    try:
        debug_usdjpy()
    except Exception as e:
        import traceback
        traceback.print_exc()
