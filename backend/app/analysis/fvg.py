"""
Fair Value Gap (FVG) Detection Module.

Reusable FVG utilities for any strategy.
Based on ICT/SMC Fair Value Gap concept:
    - FVG = 3-candle pattern where middle candle moves strongly,
      leaving a gap between candle 1 and candle 3 wicks.
    - Bullish FVG: candle1.high < candle3.low (gap up)
    - Bearish FVG: candle1.low > candle3.high (gap down)
    - Price returning to fill the gap = high probability entry

Reference: https://fbs.co.th/th/fbs-academy/traders-blog/fair-value-gap

Usage:
    from app.analysis.fvg import detect_fvg_zones, get_active_fvg, FVGZone

    zones = detect_fvg_zones(candles, min_body_atr_ratio=0.3)
    active = get_active_fvg(zones, current_price, atr_value)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FVGZone:
    """Represents a single Fair Value Gap zone."""
    direction: str          # "BULLISH" or "BEARISH"
    zone_top: float         # Upper boundary of the gap
    zone_bottom: float      # Lower boundary of the gap
    zone_mid: float         # Midpoint (optimal entry per Fibonacci OTE)
    candle1_idx: int        # Index of candle 1 in the DataFrame
    candle3_idx: int        # Index of candle 3 in the DataFrame
    gap_size: float         # Size of the gap in price units
    body_size: float        # Body size of the middle candle
    filled: bool = False    # Whether price has fully filled the gap
    creation_bar: int = 0   # Bar number when FVG was created
    
    @property
    def is_valid(self) -> bool:
        """FVG is valid if not yet fully filled."""
        return not self.filled
    
    def check_fill(self, current_high: float, current_low: float) -> bool:
        """Check if current bar fills this FVG zone."""
        if self.direction == "BULLISH":
            # Bullish FVG is filled when price drops through the bottom
            if current_low <= self.zone_bottom:
                self.filled = True
        else:
            # Bearish FVG is filled when price rises through the top
            if current_high >= self.zone_top:
                self.filled = True
        return self.filled
    
    def price_in_zone(self, price: float, tolerance: float = 0.0) -> bool:
        """Check if price is within or near this FVG zone."""
        return (price >= self.zone_bottom - tolerance and 
                price <= self.zone_top + tolerance)


def detect_fvg(
    candles: pd.DataFrame,
    lookback: int = 50,
    min_body_atr_ratio: float = 0.3,
    atr_value: float = 0.0,
) -> dict:
    """
    Simple FVG detection on the most recent candles.
    Returns a dict with bullish/bearish FVG status and zone boundaries.
    
    Uses numpy arrays to avoid C-level pandas crashes on DataFrame slices.
    """
    result = {
        "bullish": False,
        "bearish": False,
        "zone_top": 0.0,
        "zone_bottom": 0.0,
        "zone_mid": 0.0,
        "gap_size": 0.0,
    }
    
    if len(candles) < 4:
        return result
    
    # Convert to numpy arrays to avoid pandas iloc C-extension crashes
    highs = candles["high"].values
    lows = candles["low"].values
    opens = candles["open"].values
    closes = candles["close"].values
    
    # Last 3 completed candles (not current forming bar)
    c1_high = float(highs[-4])
    c1_low = float(lows[-4])
    c3_high = float(highs[-2])
    c3_low = float(lows[-2])
    c2_body = float(abs(closes[-3] - opens[-3]))
    
    min_body = float(atr_value * min_body_atr_ratio) if atr_value > 0 else 0.0
    
    # Bullish FVG: gap between c1 high and c3 low
    if c3_low > c1_high and c2_body > min_body:
        result["bullish"] = True
        result["zone_top"] = float(c3_low)
        result["zone_bottom"] = float(c1_high)
        result["zone_mid"] = float((c3_low + c1_high) / 2)
        result["gap_size"] = float(c3_low - c1_high)
        
    # Bearish FVG: gap between c1 low and c3 high
    if c3_high < c1_low and c2_body > min_body:
        result["bearish"] = True
        result["zone_top"] = float(c1_low)
        result["zone_bottom"] = float(c3_high)
        result["zone_mid"] = float((c1_low + c3_high) / 2)
        result["gap_size"] = float(c1_low - c3_high)
    
    return result


def detect_fvg_zones(
    candles: pd.DataFrame,
    lookback: int = 50,
    min_body_atr_ratio: float = 0.3,
    atr_value: float = 0.0,
    max_zones: int = 10,
) -> List[FVGZone]:
    """
    Detect all FVG zones within the lookback window.
    Returns a list of FVGZone objects sorted by recency (newest first).
    
    This is the advanced API for strategies that want to track multiple
    active FVG zones and check price interaction with any of them.
    
    Args:
        candles: OHLC DataFrame
        lookback: Number of bars to look back for FVG detection
        min_body_atr_ratio: Minimum middle candle body as ratio of ATR
        atr_value: Current ATR value for filtering
        max_zones: Maximum number of zones to return
        
    Returns:
        List of FVGZone objects, newest first
    """
    zones: List[FVGZone] = []
    
    if len(candles) < 3:
        return zones
    
    n = len(candles)
    start = max(2, n - lookback)
    min_body = atr_value * min_body_atr_ratio if atr_value > 0 else 0
    
    for i in range(start, n):
        c1 = candles.iloc[i - 2]
        c2 = candles.iloc[i - 1]
        c3 = candles.iloc[i]
        
        c1_high = float(c1["high"])
        c1_low = float(c1["low"])
        c3_high = float(c3["high"])
        c3_low = float(c3["low"])
        c2_body = float(abs(c2["close"] - c2["open"]))
        
        if c2_body < min_body:
            continue
            
        # Bullish FVG
        if c3_low > c1_high:
            zone = FVGZone(
                direction="BULLISH",
                zone_top=c3_low,
                zone_bottom=c1_high,
                zone_mid=(c3_low + c1_high) / 2,
                candle1_idx=i - 2,
                candle3_idx=i,
                gap_size=c3_low - c1_high,
                body_size=c2_body,
                creation_bar=i,
            )
            zones.append(zone)
            
        # Bearish FVG
        if c3_high < c1_low:
            zone = FVGZone(
                direction="BEARISH",
                zone_top=c1_low,
                zone_bottom=c3_high,
                zone_mid=(c1_low + c3_high) / 2,
                candle1_idx=i - 2,
                candle3_idx=i,
                gap_size=c1_low - c3_high,
                body_size=c2_body,
                creation_bar=i,
            )
            zones.append(zone)
    
    # Mark filled zones by scanning price action after creation
    for zone in zones:
        for j in range(zone.creation_bar + 1, n):
            bar = candles.iloc[j]
            zone.check_fill(float(bar["high"]), float(bar["low"]))
            if zone.filled:
                break
    
    # Return newest first, limited to max_zones
    zones.reverse()
    return zones[:max_zones]


def get_active_fvg(
    zones: List[FVGZone], 
    current_price: float,
    tolerance: float = 0.0,
    direction: Optional[str] = None,
) -> Optional[FVGZone]:
    """
    Find the nearest active (unfilled) FVG zone that price is near.
    
    Args:
        zones: List of FVGZone from detect_fvg_zones()
        current_price: Current close price
        tolerance: Price tolerance for "near zone" detection
        direction: Filter by "BULLISH" or "BEARISH", or None for any
        
    Returns:
        The nearest active FVGZone, or None
    """
    for zone in zones:
        if not zone.is_valid:
            continue
        if direction and zone.direction != direction:
            continue
        if zone.price_in_zone(current_price, tolerance):
            return zone
    return None


def fvg_confluence_score(
    candles: pd.DataFrame,
    current_price: float,
    atr_value: float,
    lookback: int = 30,
) -> dict:
    """
    Calculate FVG confluence score for a given price level.
    Higher score = more FVG zones supporting this level.
    
    This is useful for strategies that want a single "FVG strength" 
    number to add to their confidence calculation.
    
    Args:
        candles: OHLC DataFrame
        current_price: Current price to evaluate
        atr_value: ATR for tolerance and filtering
        lookback: Bars to look back
        
    Returns:
        dict with:
            - bullish_score: float (0.0-1.0)
            - bearish_score: float (0.0-1.0)  
            - nearest_bullish: Optional[FVGZone]
            - nearest_bearish: Optional[FVGZone]
            - total_active_zones: int
    """
    zones = detect_fvg_zones(
        candles, lookback=lookback, 
        atr_value=atr_value, min_body_atr_ratio=0.3,
    )
    
    active = [z for z in zones if z.is_valid]
    tolerance = atr_value * 0.5
    
    bullish_near = [z for z in active if z.direction == "BULLISH" 
                    and z.price_in_zone(current_price, tolerance)]
    bearish_near = [z for z in active if z.direction == "BEARISH"
                    and z.price_in_zone(current_price, tolerance)]
    
    # Score: more zones + larger gaps = higher score
    bull_score = min(1.0, sum(z.gap_size / atr_value for z in bullish_near) * 0.3) if atr_value > 0 else 0.0
    bear_score = min(1.0, sum(z.gap_size / atr_value for z in bearish_near) * 0.3) if atr_value > 0 else 0.0
    
    return {
        "bullish_score": float(bull_score),
        "bearish_score": float(bear_score),
        "nearest_bullish": bullish_near[0] if bullish_near else None,
        "nearest_bearish": bearish_near[0] if bearish_near else None,
        "total_active_zones": len(active),
    }
