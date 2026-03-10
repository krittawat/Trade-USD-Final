import sys
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mt5.client import MT5Client
from app.core.config import get_settings
from app.execution.pipeline import ExecutionPipeline
import app.analysis.indicators as ind

def find_gold_entry():
    print("Scanning Gold (XAUUSDc) for entry opportunities...")
    
    settings = get_settings()
    mt5_client = MT5Client(settings)
    
    if not mt5_client.connect():
        print("❌ Failed to connect to MT5.")
        return

    symbol = "XAUUSDc"
    
    # 1. Check if market is open
    symbol_info = mt5_client.get_symbol_info(symbol)
    if not symbol_info:
        print(f"❌ Could not retrieve symbol info for {symbol}")
        return
        
    bid, ask = mt5_client.get_current_price(symbol)
    spread = mt5_client.get_current_spread(symbol)
    
    print(f"\n📊 --- Market Data for {symbol} ---")
    print(f"Ask: {ask}")
    print(f"Bid: {bid}")
    print(f"Spread: {spread}")
    
    # 2. Fetch recent data to analyze trend
    rates = mt5_client.get_historical_candles(symbol, "M5", 300)
    
    if rates is not None and len(rates) > 0:
        import pandas as pd
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        
        # Calculate some basic indicators
        df['EMA_14'] = ind.ema(df['close'], 14)
        df['EMA_50'] = ind.ema(df['close'], 50)
        df['RSI_14'] = ind.rsi(df['close'], 14)
        df['ATR_14'] = ind.atr(df['high'], df['low'], df['close'], 14)
        
        last_row = df.iloc[-1]
        
        print("\n📈 --- Technical Indicators (M5) ---")
        print(f"EMA14: {last_row['EMA_14']:.4f}")
        print(f"EMA50: {last_row['EMA_50']:.4f}")
        print(f"RSI14: {last_row['RSI_14']:.2f}")
        print(f"ATR14: {last_row['ATR_14']:.4f}")
        
        trend = "UP" if last_row['EMA_14'] > last_row['EMA_50'] else "DOWN"
        strength = "STRONG" if last_row['RSI_14'] > 60 or last_row['RSI_14'] < 40 else "WEAK"
        print(f"\nCurrent M5 Trend: {trend} ({strength})")
        
        # Check Gold Evolution strategy signals
        from app.strategy.templates.gold_evolution import GoldEvolutionStrategy
        from app.domain.models import Decision
        
        strat = GoldEvolutionStrategy()
        decision = strat.analyze(df, symbol_info)
        
        print("\n🤖 --- Strategy Analysis (Gold Evolution) ---")
        print(f"Action: {decision.action.name}")
        print(f"Confidence: {decision.confidence:.2f}")
        print(f"Reason: {decision.reason}")
        if decision.action.name != "HOLD":
            if hasattr(decision, 'stop_loss_distance'):
                print(f"Suggested SL Distance: {decision.stop_loss_distance:.4f}")
            if hasattr(decision, 'take_profit_distance'):
                print(f"Suggested TP Distance: {decision.take_profit_distance:.4f}")
    else:
        print("❌ Failed to fetch candle data.")

if __name__ == "__main__":
    find_gold_entry()
