
import pandas as pd
import pandas_ta as ta
from typing import Dict, Any, Tuple

class GhostProtocol:
    """
    GHOST PROTOCOL: Institutional Anomaly Detector
    Detects 'Hidden' Orders, Liquidity Voids, and Order Blocks (OB).
    """
    
    @staticmethod
    def analyze(df: pd.DataFrame) -> Dict[str, Any]:
        """
        Analyze for hidden institutional footprints.
        """
        if df is None or len(df) < 50:
            return {"detected": False, "signal": "NEUTRAL", "reason": "Insufficient Data"}
            
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        close = last['close']
        
        # 1. ORDER BLOCK (OB) DETECTION
        # A bullish OB is the last bearish candle before a strong up-move (Break of Structure)
        # Simplified: Look for strong engulfing after a consolidation
        
        ob_score = 0
        ob_type = "NONE"
        
        # Bullish Engulfing with Volume Spike
        body_size = abs(last['close'] - last['open'])
        prev_body = abs(prev['close'] - prev['open'])
        
        is_bullish_engulfing = (last['close'] > prev['open']) and (last['open'] < prev['close']) and (last['close'] > last['open'])
        is_bearish_engulfing = (last['close'] < prev['open']) and (last['open'] > prev['close']) and (last['close'] < last['open'])
        
        vol_spike = last['tick_volume'] > (df['tick_volume'].rolling(20).mean().iloc[-1] * 1.5)
        
        if is_bullish_engulfing and vol_spike:
            # CHECK FRESHNESS: Has price deeply penetrated this low recently?
            # Relaxed: Allow wicks, but not full body closes below
            recent_closes = df['close'].iloc[-10:-2]
            if recent_closes.min() > prev['low']: 
                ob_type = "BULLISH_OB"
                ob_score = 90
            else:
                 ob_score = 70 # Still tradeable, just lower confidence
                 
        elif is_bearish_engulfing and vol_spike:
            # CHECK FRESHNESS
            recent_closes = df['close'].iloc[-10:-2]
            if recent_closes.max() < prev['high']: 
                ob_type = "BEARISH_OB"
                ob_score = 90
            else:
                ob_score = 70
            
        # 2. LIQUIDITY VOID (FVG - Fair Value Gap)
        # Gap between candle i-2 high and candle i low (for Up move)
        fvg_detected = False
        fvg_type = "NONE"
        
        if len(df) > 3:
            # Bullish FVG
            c1_high = df.iloc[-3]['high']
            c3_low = df.iloc[-1]['low']
            if c3_low > c1_high:
                fvg_detected = True
                fvg_type = "BULLISH_FVG"
                
            # Bearish FVG
            c1_low = df.iloc[-3]['low']
            c3_high = df.iloc[-1]['high']
            if c3_high < c1_low:
                fvg_detected = True
                fvg_type = "BEARISH_FVG"

        # 3. GHOST LEVEL (Psychological 00/50 levels)
        # Check if price rejected off a X00 or X50 level recently
        ghost_level = False
        level_price = 0
        
        # Round to nearest 10
        import math
        nearest_50 = round(close / 50) * 50
        nearest_100 = round(close / 100) * 100
        
        dist_50 = abs(close - nearest_50)
        dist_100 = abs(close - nearest_100)
        
        if dist_50 < (close * 0.0005) or dist_100 < (close * 0.0005): # Very close to bank level
             ghost_level = True
        
        # === SYNTHESIS ===
        signal = "NEUTRAL"
        confidence = 0
        reasons = []
        
        if ob_type == "BULLISH_OB" and ob_score >= 70:
            signal = "BUY"
            confidence += 50
            reasons.append("Bullish Order Block")
        elif ob_type == "BEARISH_OB" and ob_score >= 70:
            signal = "SELL"
            confidence += 50
            reasons.append("Bearish Order Block")
        elif ob_type != "NONE":
             # Weaker OB
             reasons.append("Mitigated/Weak Order Block")
             # Do not trade weak OB in isolation, return Neutral or low conf
             if confidence < 30: signal = "NEUTRAL"
            
        if fvg_type == "BULLISH_FVG" and signal == "BUY":
            confidence += 30
            reasons.append("Bullish Liquidity Void (FVG)")
        elif fvg_type == "BEARISH_FVG" and signal == "SELL":
            confidence += 30
            reasons.append("Bearish Liquidity Void (FVG)")
            
        if ghost_level:
            confidence += 10
            reasons.append("Bank Level Interaction")
            
        return {
            "signal": signal,
            "confidence": min(95, confidence), # Cap at 95
            "reasons": reasons,
            "pattern": ob_type if ob_type != "NONE" else fvg_type
        }

ghost_protocol = GhostProtocol()
