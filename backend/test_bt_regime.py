
import logging
import pandas as pd
from app.mt5.market_data import get_history
from app.execution.backtester import Backtester
from app.strategy.templates.ADD_18022026.regime_adaptive import RegimeAdaptiveStrategy
from app.domain.models import SymbolProfile
from dotenv import load_dotenv
import MetaTrader5 as mt5

logging.basicConfig(level=logging.DEBUG)
load_dotenv()

def test_backtest():
    if not mt5.initialize():
        print("MT5 Init failed")
        return
        
    symbol = "XAUUSDm"
    history = get_history(symbol, "M15", days=5)
    if history is None or history.empty:
        print("No history")
        return
        
    strategy = RegimeAdaptiveStrategy()
    profile = SymbolProfile(symbol=symbol, contract_size=100.0, point=0.01)
    
    bt = Backtester(
        strategy=strategy,
        risk_per_trade=0.01,
        max_dd_limit=0.10
    )
    
    print("Starting backtest...")
    result = bt.run(
        candles=history,
        symbol=symbol,
        contract_size=100.0,
        point=0.01,
        profile=profile
    )
    print(f"Backtest Done: {result.total_trades} trades, PF={result.profit_factor}")

if __name__ == "__main__":
    test_backtest()
