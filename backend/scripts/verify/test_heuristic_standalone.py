
def heuristic_fallback(symbol, regime):
    regime = regime.upper()
    
    # specific asset overrides
    if "XAU" in symbol.upper():
            if "TREND" in regime: return "gold_elite"
            if "VOLATI" in regime: return "gold_scalp_pro"
            if "RANG" in regime or "SIDEWAYS" in regime: return "gold_elite"

    if "XAG" in symbol.upper():
            if "RANG" in regime or "SIDEWAYS" in regime: return "silver_mean_rev"
            return "silver_elite"

    if "BTC" in symbol.upper():
            return "btc_elite"

    if "JPY" in symbol.upper():
            return "usdjpy_elite"
    
    if "TREND" in regime:
        return "trend_rider"
    elif "RANG" in regime or "SIDEWAYS" in regime:
        return "scalping" 
    elif "VOLATIL" in regime or "BREAKOUT" in regime:
        return "sniper"
    
    return "sniper"

# Test
print(f"XAU RANGING: {heuristic_fallback('XAUUSD', 'RANGING')}")
print(f"XAG RANGING: {heuristic_fallback('XAGUSD', 'RANGING')}")
print(f"XAG TRENDING: {heuristic_fallback('XAGUSD', 'TRENDING_UP')}")
print(f"BTC ANY: {heuristic_fallback('BTCUSD', 'HIGH_VOLATILITY')}")
print(f"JPY ANY: {heuristic_fallback('USDJPY', 'RANGING')}")
print(f"EUR RANGING: {heuristic_fallback('EURUSD', 'RANGING')}")
