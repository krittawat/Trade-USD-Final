import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
from app.execution.backtester import Backtester
from app.strategy.templates.usoil_elite import USOilEliteStrategy
from app.domain.models import SymbolProfile

def run_usoil_backtest():
    if not mt5.initialize():
        print("MT5 Init Failed")
        return

    symbol = "USOILm"
    tf = mt5.TIMEFRAME_M5
    days = 7
    
    # Range: last 7 days
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    print(f"Fetching data for {symbol} from {start_date} to {end_date}...")
    rates = mt5.copy_rates_range(symbol, tf, start_date, end_date)
    mt5.shutdown()
    
    if rates is None or len(rates) == 0:
        print("No data fetched.")
        return
        
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    # Strategy
    strat = USOilEliteStrategy()
    
    # Backtester
    # Initial equity $10,000 for relative comparison or use actual? 
    # Let's use $1,000 to be closer to user scale.
    bt = Backtester(strat, initial_equity=1000.0, risk_per_trade=0.01)
    
    print(f"Running backtest with {len(df)} candles...")
    # USOILm properties
    # Usually: point=0.001 (for 3 digits) or similar.
    # We'll use 100 for contract size as typical for Mini/Standard Oil.
    result = bt.run(df, symbol=symbol, contract_size=100.0, point=0.001)
    
    print("\n" + "="*40)
    print(f"BACKTEST RESULTS: {result.symbol} | {result.strategy}")
    print("="*40)
    print(f"Total Trades:    {result.total_trades}")
    print(f"Win Rate:       {result.win_rate}%")
    print(f"Profit Factor:  {result.profit_factor}")
    print(f"Total Profit:  ${result.total_profit_usd}")
    print(f"Max Drawdown:   {result.max_drawdown_pct}%")
    print(f"Final Equity:  ${result.final_equity}")
    print("-" * 40)
    
    if len(result.trades) > 0:
        trades_df = pd.DataFrame(result.trades)
        print("\nLast 5 trades:")
        print(trades_df.tail(5)[['action', 'entry', 'exit', 'pnl', 'reason']].to_string(index=False))

if __name__ == "__main__":
    run_usoil_backtest()
