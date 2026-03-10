import asyncio
import sys
import os
import pandas as pd

sys.path.insert(0, os.path.abspath('.'))

from app.mt5.client import MT5Client
from app.core.config import get_settings
from app.strategy.templates.gold_evolution import GoldEvolutionStrategy
import MetaTrader5 as mt5_lib

def run():
    settings = get_settings()
    mt5 = MT5Client(settings)
    if not mt5.connect():
        print("Failed to connect to MT5 via client")
        return

    symbol = "XAUUSDc"
    profile = mt5.get_symbol_info(symbol)
    if not profile:
        print("Could not get profile")
        return

    def fetch_candles(tf, count):
        rates = mt5_lib.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0: return None
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df

    candles_m5 = fetch_candles(mt5_lib.TIMEFRAME_M5, 250)
    candles_h1 = fetch_candles(mt5_lib.TIMEFRAME_H1, 250)

    if candles_m5 is None or len(candles_m5) == 0:
        print("No M5 candles found")
        return
        
    current_time = candles_m5.iloc[-1]['time']
    current_close = candles_m5.iloc[-1]['close']
    
    print(f"\n[ Time: {current_time} | Close: {current_close} ]\n")

    # 1. Gold Evolution
    print(f"--- Gold Evolution ---")
    s1 = GoldEvolutionStrategy()
    # Mocking kwargs that brain would send
    kwargs1 = {"sl_atr_mult": 1.5, "tp_atr_mult": 2.0, "confidence_min": 0.50, "h1_candles": candles_h1}
    try:
        r1 = s1.analyze(candles=candles_m5, profile=profile, regime="UNKNOWN", **kwargs1)
        print(f"Action: {r1.action.name}")
        print(f"Confidence: {r1.confidence:.2f}")
        print(f"Reason: {r1.reason}")
        if hasattr(r1, 'debug'): print(f"Debug: {r1.debug}")
    except Exception as e:
        import traceback
        traceback.print_exc()

    mt5.disconnect()

if __name__ == "__main__":
    run()
