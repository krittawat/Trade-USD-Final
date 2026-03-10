import MetaTrader5 as mt5

def list_symbols():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return

    symbols = mt5.symbols_get()
    if symbols:
        print(f"Total symbols: {len(symbols)}")
        # Print some interesting ones
        targets = ["XAU", "XAG", "BTC", "ETH", "USOIL", "US30", "USTEC", "EURUSD", "GBPUSD"]
        for s in symbols:
            if any(t in s.name.upper() for t in targets):
                print(f"Name: {s.name}, Path: {s.path}, Spread: {s.spread}, Trade Mode: {s.trade_mode}")
    
    mt5.shutdown()

if __name__ == "__main__":
    list_symbols()
