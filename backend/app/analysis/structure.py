import pandas as pd
import numpy as np
from typing import Dict, List, Optional

def detect_structure(df: pd.DataFrame, lb: int = 5) -> Dict:
    """
    Advanced Market Structure Analysis (Institutional/SMC Grade).
    - BOS (Break of Structure): Trend continuation.
    - CHoCH (Change of Character): Early reversal.
    - FVG (Fair Value Gap): Liquidity imbalances.
    - Candlestick Patterns: Confirmation triggers.
    """
    if len(df) < 50:
        return {}

    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values
    
    # --- 1. Fair Value Gaps (FVG) ---
    # Bullish FVG: Low[i] > High[i-2]
    # Bearish FVG: High[i] < Low[i-2]
    fvg_bull = []
    fvg_bear = []
    
    # Scan last 10 candles for active FVGs
    for i in range(len(df)-1, len(df)-11, -1):
        if i < 2: break
        # Bullish
        if lows[i] > highs[i-2]:
            fvg_bull.append({'top': lows[i], 'bottom': highs[i-2], 'index': i-1})
        # Bearish
        if highs[i] < lows[i-2]:
            fvg_bear.append({'top': lows[i-2], 'bottom': highs[i], 'index': i-1})

    # --- 2. Swing Points & Trends ---
    # Find local swing highs/lows for BOS/CHoCH
    # Higher Highs (HH), Higher Lows (HL), Lower Lows (LL), Lower Highs (LH)
    
    # Identify Fractals (3 or 5 bar)
    is_hh = (highs[-3] > highs[-4] and highs[-3] > highs[-5] and highs[-3] > highs[-2] and highs[-3] > highs[-1])
    is_ll = (lows[-3] < lows[-4] and lows[-3] < lows[-5] and lows[-3] < lows[-2] and lows[-3] < lows[-1])

    # Simplified BOS/CHoCH Logic
    # In an uptrend (HH/HL), a break below recent HL = CHoCH
    # In a downtrend (LH/LL), a break above recent LH = CHoCH
    
    # Get recent structure extremes
    recent_max = highs[-20:-1].max()
    recent_min = lows[-20:-1].min()
    
    bos_bull = closes[-1] > recent_max
    bos_bear = closes[-1] < recent_min
    
    # CHoCH Detection (Requires sequence tracking, here simplified to 'Break of recent pivot')
    # If price was making Lower Lows and now breaks previous Lower High = Bullish CHoCH
    prev_lh = highs[-40:-10].max() # Heuristic for recent LH
    prev_hl = lows[-40:-10].min()  # Heuristic for recent HL
    
    choch_bull = closes[-1] > prev_lh and closes[-5] < prev_lh
    choch_bear = closes[-1] < prev_hl and closes[-5] > prev_hl

    # --- 3. Candlestick Patterns (Institutional Triggers) ---
    body = abs(closes[-1] - opens[-1])
    prev_body = abs(closes[-2] - opens[-2])
    range_total = highs[-1] - lows[-1] or 0.000001
    upper_wick = highs[-1] - max(opens[-1], closes[-1])
    lower_wick = min(opens[-1], closes[-1]) - lows[-1]
    
    # Pin Bar (Institutional Rejection)
    is_pin_bull = lower_wick > (body * 2.5) and upper_wick < (body * 0.8)
    is_pin_bear = upper_wick > (body * 2.5) and lower_wick < (body * 0.8)
    
    # Engulfing (Momentum Shift)
    is_engulf_bull = closes[-1] > opens[-1] and closes[-2] < opens[-2] and body > prev_body and closes[-1] > highs[-2]
    is_engulf_bear = closes[-1] < opens[-1] and closes[-2] > opens[-2] and body > prev_body and closes[-1] < lows[-2]

    # Inside Bar (Contraction)
    is_inside = highs[-1] < highs[-2] and lows[-1] > lows[-2]

    # Morning Star / Evening Star (3-Bar Reversal)
    is_morning_star = (
        closes[-3] < opens[-3] and # Bearish first
        abs(closes[-2] - opens[-2]) < (abs(closes[-3] - opens[-3]) * 0.3) and # Small body second
        closes[-1] > opens[-1] and closes[-1] > (opens[-3] + closes[-3]) / 2 # Bullish third, halfway up 1st
    )
    is_evening_star = (
        closes[-3] > opens[-3] and # Bullish first
        abs(closes[-2] - opens[-2]) < (abs(closes[-3] - opens[-3]) * 0.3) and # Small body second
        closes[-1] < opens[-1] and closes[-1] < (opens[-3] + closes[-3]) / 2 # Bearish third, halfway down 1st
    )

    return {
        'fvg_bull': fvg_bull[:3], # Return top 3 FVGs
        'fvg_bear': fvg_bear[:3],
        'bos_bull': bos_bull,
        'bos_bear': bos_bear,
        'choch_bull': choch_bull,
        'choch_bear': choch_bear,
        'patterns': {
            'pin_bull': is_pin_bull,
            'pin_bear': is_pin_bear,
            'engulf_bull': is_engulf_bull,
            'engulf_bear': is_engulf_bear,
            'inside_bar': is_inside,
            'morning_star': is_morning_star,
            'evening_star': is_evening_star
        },
        'trend': "BULLISH" if bos_bull or choch_bull else "BEARISH" if bos_bear or choch_bear else "RANGING",
        'hh_detected': is_hh,
        'll_detected': is_ll
    }
