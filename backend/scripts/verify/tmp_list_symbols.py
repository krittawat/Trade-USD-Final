import MetaTrader5 as mt5
import pandas as pd

def list_symbols():
    if not mt5.initialize():
        print("Init failed")
        return
        
    symbols = mt5.symbols_get()
    print(f"Total symbols: {len(symbols)}")
    
    data = []
    for s in symbols:
        if any(x in s.name for x in ["XAU", "XAG", "BTC", "OIL"]):
            data.append({
                "name": s.name,
                "path": s.path,
                "visible": s.visible,
                "trade_mode": s.trade_mode
            })
            
    df = pd.DataFrame(data)
    if not df.empty:
        print(df)
    else:
        print("No matching symbols found.")
    mt5.shutdown()

if __name__ == "__main__":
    list_symbols()
