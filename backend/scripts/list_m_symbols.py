import MetaTrader5 as mt5

def list_standard_m_symbols():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return

    symbols = mt5.symbols_get()
    if symbols:
        m_symbols = [s.name for s in symbols if s.name.endswith('m')]
        print("--- Standard 'm' Symbols ---")
        for s in sorted(m_symbols):
            if any(t in s.upper() for t in ["XAU", "XAG", "BTC", "ETH", "USOIL", "US30", "USTEC", "DE30", "DXY"]):
                print(s)
    
    mt5.shutdown()

if __name__ == "__main__":
    list_standard_m_symbols()
