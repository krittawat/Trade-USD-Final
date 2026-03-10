import sys
from pathlib import Path

# Provide resolving path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.trader.execution.mt5_order import Executor
from backend.trader.data.mapper import mapper
import MetaTrader5 as mt5

def force_trade():
    print("Initialize MT5...")
    if not mt5.initialize():
        print("MT5 Not initialized")
        return

    # User requested to see a SELL trade. Let's do a 0.01 XAUUSD SELL
    symbol = "XAUUSD"
    broker_symbol = mapper.to_broker(symbol)
    
    # Get current price
    tick = mt5.symbol_info_tick(broker_symbol)
    if tick is None:
        print(f"Failed to get tick for {broker_symbol}")
        return
        
    current_bid = tick.bid
    print(f"Current Bid for {broker_symbol}: {current_bid}")
    
    # Create fake signal for Executor
    sl_points = 3.0 # $3 SL
    tp_points = 6.0 # $6 TP
    
    signal = {
        "symbol": symbol,
        "side": "BUY",
        "entry_price": tick.ask,
        "sl": tick.ask - sl_points,
        "tp1": tick.ask + tp_points,
        "tp2": tick.ask + (tp_points * 1.5),
        "tp3": tick.ask + (tp_points * 2.0),
        "model": "MANUAL_FORCE",
        "rationale": ["User requested force BUY trade"]
    }
    
    print("Executing through OPUS Executor in LIVE mode...")
    executor = Executor(mode="live")
    result = executor.place_order(signal, lot_size=0.01)
    
    print("Result:", result)

if __name__ == "__main__":
    force_trade()
