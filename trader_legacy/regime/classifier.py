import pandas as pd

def classify_regime(df: pd.DataFrame, config: dict) -> dict:
    """
    Determines market regime based on enriched dataframe (requires volatility and structure features).
    Returns: {regime: str, confidence: float, reasons: list}
    """
    if len(df) < 50:
        return {"regime": "UNKNOWN", "confidence": 0.0, "reasons": ["Not enough data"]}
        
    latest = df.iloc[-1]
    
    reasons = []
    
    # 1. Volatility Assessment
    comp_thresh = config.get("volatility_compression_threshold", 0.5)
    exp_thresh = config.get("volatility_expansion_threshold", 1.5)
    
    if latest['compression_ratio'] < comp_thresh:
        reasons.append(f"Compression ratio {latest['compression_ratio']:.2f} < {comp_thresh}")
        return {"regime": "Volatility Compression", "confidence": 1.0 - latest['compression_ratio'], "reasons": reasons}
        
    if latest['atr'] > latest['atr_baseline'] * exp_thresh:
        reasons.append(f"Current ATR {latest['atr']:.2f} > Baseline * {exp_thresh}")
        return {"regime": "Volatility Expansion", "confidence": min(1.0, latest['atr'] / (latest['atr_baseline']*exp_thresh) - 1), "reasons": reasons}
        
    # 2. Trend Assessment (Simplified Structural Check)
    # Count recent HH/HL vs LH/LL
    recent_structs = df.tail(20)['structure']
    bullish_structs = recent_structs.isin(['HH', 'HL']).sum()
    bearish_structs = recent_structs.isin(['LH', 'LL']).sum()
    total_structs = bullish_structs + bearish_structs
    
    if total_structs > 0:
        bull_ratio = bullish_structs / total_structs
        bear_ratio = bearish_structs / total_structs
        
        trend_thresh = config.get("trend_threshold", 0.6)
        
        if bull_ratio >= trend_thresh:
            reasons.append(f"Bullish structure ratio {bull_ratio:.2f} >= {trend_thresh}")
            if bull_ratio > 0.8:
                return {"regime": "Strong Trend (Up)", "confidence": bull_ratio, "reasons": reasons}
            return {"regime": "Weak Trend (Up)", "confidence": bull_ratio, "reasons": reasons}
            
        if bear_ratio >= trend_thresh:
            reasons.append(f"Bearish structure ratio {bear_ratio:.2f} >= {trend_thresh}")
            if bear_ratio > 0.8:
                return {"regime": "Strong Trend (Down)", "confidence": bear_ratio, "reasons": reasons}
            return {"regime": "Weak Trend (Down)", "confidence": bear_ratio, "reasons": reasons}
            
    # 3. Sideways (Distribution / Accumulation)
    # If not trending or expanding/compressing heavily, we are sideways.
    # A simple proxy: if price is near bottom of 50 bar range -> Accumulation
    range_high = df['high'].tail(50).max()
    range_low = df['low'].tail(50).min()
    price_position = (latest['close'] - range_low) / (range_high - range_low + 1e-9)
    
    if price_position > 0.7:
        reasons.append(f"Price position near top of range: {price_position:.2f}")
        return {"regime": "Distribution", "confidence": price_position, "reasons": reasons}
    elif price_position < 0.3:
        reasons.append(f"Price position near bottom of range: {price_position:.2f}")
        return {"regime": "Accumulation", "confidence": 1.0 - price_position, "reasons": reasons}
        
    reasons.append("Defaulting to chop/sideways")
    return {"regime": "Sideways", "confidence": 0.5, "reasons": reasons}

