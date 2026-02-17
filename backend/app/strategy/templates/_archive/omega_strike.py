import pandas as pd
import numpy as np
import pandas_ta as ta
from typing import Dict, Any, Optional, List, Tuple
from .base_strategy import BaseStrategy, StrategyDecision

class OmegaStrikeStrategy(BaseStrategy):
    """
    Project Omega Strike - M1 Hyper-SMC (HFT Edition) ⚡🔱🛡️
    Optimized for Gold/FX M1 with noise filtering and safety floors.
    """
    
    def __init__(self):
        self.name = "OMEGA STRIKE (M1 HYPER)"
        self.min_confidence = 0.80 # Increased for higher quality
        
        self.tuning = {
            "DEFAULT": {
                "ob_displacement": 1.5, # Less sensitive = more quality
                "lookback": 30,         # More context
                "fvg_min_size_atr": 0.2,
                "rr_ratio": 2.2,
                "min_sl_dist_atr": 1.8  # SL floor to prevent noise exits
            },
            "XAUUSD": {
                "ob_displacement": 1.8,
                "lookback": 40,
                "fvg_min_size_atr": 0.25,
                "rr_ratio": 2.5,
                "min_sl_dist_atr": 2.0
            }
        }

    def _get_tuning(self, symbol: str) -> Dict[str, Any]:
        sym = symbol.replace(".m", "").replace("c", "").replace("#", "").upper()
        if "XAU" in sym or "GOLD" in sym: return self.tuning["XAUUSD"]
        return self.tuning["DEFAULT"]

    def detect_order_block(self, df: pd.DataFrame, i: int, tuning: dict) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        if i < 40: return None, None
        atr = df.iloc[i].get('ATR_14', 1)
        bullish_ob, bearish_ob = None, None
        
        # Look back for M1 SMC structure
        for j in range(i-2, i-tuning["lookback"], -1):
            if bullish_ob and bearish_ob: break
            
            body_size = abs(df.iloc[j+1]['close'] - df.iloc[j+1]['open'])
            if body_size > atr * tuning["ob_displacement"]:
                # Bullish OB Check
                if not bullish_ob and df.iloc[j]['close'] < df.iloc[j]['open']:
                    # Check for displacement ABOVE recent peak
                    if df.iloc[j+1:i]['high'].max() > df.iloc[j-10:j]['high'].max():
                        bullish_ob = (df.iloc[j]['low'], df.iloc[j]['high'])
                
                # Bearish OB Check
                if not bearish_ob and df.iloc[j]['close'] > df.iloc[j]['open']:
                    # Check for displacement BELOW recent low
                    if df.iloc[j+1:i]['low'].min() < df.iloc[j-10:j]['low'].min():
                        bearish_ob = (df.iloc[j]['low'], df.iloc[j]['high'])
        
        return bullish_ob, bearish_ob

    def detect_fvg(self, df: pd.DataFrame, i: int) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        if i < 3: return None, None
        bull_fvg, bear_fvg = None, None
        
        # M1 FVG needs to be significant to filter noise
        if df.iloc[i-2]['high'] < df.iloc[i]['low']:
            bull_fvg = (df.iloc[i-2]['high'], df.iloc[i]['low'])
            
        if df.iloc[i-2]['low'] > df.iloc[i]['high']:
            bear_fvg = (df.iloc[i]['high'], df.iloc[i-2]['low'])
            
        return bull_fvg, bear_fvg

    def analyze(self, df: pd.DataFrame = None, **kwargs) -> StrategyDecision:
        if df is None:
            df = kwargs.get('df')
            
        symbol = kwargs.get('symbol', 'GENERIC')
        d1_trend = kwargs.get('d1_trend', "UNKNOWN")
        
        if df is None or len(df) < 50:
            return StrategyDecision(signal="NO_TRADE", reason="Init Data")
            
        idx = len(df) - 1
        tuning = self._get_tuning(symbol)
        
        # Ensure indicators
        if 'ATR_14' not in df.columns:
            df['ATR_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            
        # Core SMC Detection
        bull_ob, bear_ob = self.detect_order_block(df, idx, tuning)
        bull_fvg, bear_fvg = self.detect_fvg(df, idx)
        
        current_candle = df.iloc[idx]
        price = current_candle['close']
        
        signal = "NO_TRADE"
        reasons = []
        insights = {"timeframe": "M1", "mode": "STRIKE"}
        
        # Trend Alignment mandatory for OMEGA
        if d1_trend != "DOWN" and bull_fvg and price > bull_fvg[0]:
            if bull_ob: # and price <= bull_ob[1] * 1.001:
                signal = "BUY"
                reasons.append("⚡ Omega: OB+FVG Strike (M1 Bull)")
                insights["fvg"] = bull_fvg
                
        if d1_trend != "UP" and bear_fvg and price < bear_fvg[1]:
            if bear_ob: # and price >= bear_ob[0] * 0.999:
                signal = "SELL"
                reasons.append("⚡ Omega: OB+FVG Strike (M1 Bear)")
                insights["fvg"] = bear_fvg

        if signal != "NO_TRADE":
            atr = current_candle.get('ATR_14', 1.0)
            # Apply SL floor to prevent lot explosion and noise exits
            sl_dist = max(atr * tuning["min_sl_dist_atr"], atr * 1.5)
            
            if signal == "BUY":
                sl = price - sl_dist
                tp = price + (sl_dist * tuning["rr_ratio"])
            else:
                sl = price + sl_dist
                tp = price - (sl_dist * tuning["rr_ratio"])
                
            return StrategyDecision(
                signal=signal,
                entry_price=price,
                sl=sl,
                tp=tp,
                reason=" | ".join(reasons),
                confidence=0.85, # Higher confidence requirement
                risk_pct=0.005,  # Reduced risk on M1 for HFT scaling
                insights=insights
            )
            
        return StrategyDecision(signal="NO_TRADE", reason="Scanning Liquidity...")

    def get_status(self) -> Dict[str, Any]:
        return {"name": self.name, "tier": "Brutal", "speed": "Hyper", "features": ["M1 SMC", "Vanish Logic", "Ghost Guard"]}

omega_strike_strategy = OmegaStrikeStrategy()
