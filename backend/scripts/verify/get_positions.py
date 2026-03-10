import MetaTrader5 as mt5
import pandas as pd

def main():
    if not mt5.initialize():
        print("MT5 init failed")
        return
        
    positions = mt5.positions_get()
    if not positions:
        print("No open positions")
    else:
        df = pd.DataFrame([p._asdict() for p in positions])
        print("OPEN POSITIONS:")
        print(df[['ticket', 'symbol', 'type', 'volume', 'price_open', 'price_current', 'sl', 'tp', 'profit']].to_string())
        
    mt5.shutdown()

if __name__ == '__main__':
    main()
