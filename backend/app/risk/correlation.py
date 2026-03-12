"""
Multi-Asset Correlation — Calculate correlation between XAUUSD and XAGUSD for Arbitrage/Hedging.
"""

import pandas as pd
import numpy as np
from app.core.logging import get_logger

logger = get_logger(__name__)

def calculate_correlation(prices_a: pd.Series, prices_b: pd.Series, window: int = 20) -> float:
    """
    Calculate Pearson correlation between two price series.
    """
    if len(prices_a) < window or len(prices_b) < window:
        return 0.0
        
    correlation = prices_a.tail(window).corr(prices_b.tail(window))
    return float(correlation)

def get_arbitrage_signal(xau_price: float, xag_price: float, history_xau: pd.Series, history_xag: pd.Series) -> str:
    """
    Identify potential divergence between XAU and XAG.
    If XAU up and XAG down (divergence), it might be an arbitrage opportunity.
    """
    corr = calculate_correlation(history_xau, history_xag)
    
    # If correlation is high (>0.8) and suddenly drops, look for trade
    if corr < 0.5:
        # Check Z-Score of the ratio XAU/XAG
        ratio = history_xau / history_xag
        mean_ratio = ratio.mean()
        std_ratio = ratio.std()
        
        if std_ratio == 0:
            return "NEUTRAL"
            
        current_ratio = xau_price / xag_price
        z_score = (current_ratio - mean_ratio) / std_ratio
        
        if z_score > 2.0:
            return "XAU_SHORT_XAG_LONG" # XAU is too expensive relative to XAG
        elif z_score < -2.0:
            return "XAU_LONG_XAG_SHORT" # XAU is too cheap relative to XAG
            
    return "NEUTRAL"
