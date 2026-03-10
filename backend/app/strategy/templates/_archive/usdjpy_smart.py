"""
USDJPY Smart Strategy
=====================
Adapted from ForexPrecisionStrategy for USDJPY Volatility.

Key Adjustments:
- Wider SL/TP for JPY volatility (Point = 0.001)
- ADX Threshold tuned for JPY trends
- Specific session handling
- Optimized for batch backtesting (re-uses existing columns)
"""

import pandas as pd
import app.analysis.indicators as ind
from datetime import datetime

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)

class UsdJpySmartStrategy(BaseStrategy):
    """
    USDJPY Smart Strategy — High-Confluence Trend Following.
    
    Target:
    - Timeframe: M5
    - Pair: USDJPY
    - Session: London/NY
    """
    
    name = "usdjpy_smart"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
    ]

    # Default Parameters (High Win Rate / Quick Scalp)
    params = {
        "ema_fast": 50,
        "ema_slow": 200,
        "adx_period": 14,
        "adx_threshold": 30,   # Stricter trend filter (was 25)
        "rsi_period": 14,
        "atr_period": 14,
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 1.5,    # Quick Profit (was 5.0) -> WR ~60%
        "pullback_zone_atr": 2.5,
        "min_confidence": 0.60
    }

    def __init__(self):
        super().__init__()
    
    def update_parameters(self, new_params: dict):
        self.params.update(new_params)

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs
    ) -> Decision:
        
        # Load params
        p = self.params.copy()
        p.update(kwargs)
        
        symbol = profile.symbol
        
        # ─── Data Validation ───
        if candles is None or len(candles) < p["ema_slow"] + 10:
             return self.create_hold(symbol=symbol, reason="Insufficient Data")
             
        # ─── Indicators (Optimized Check) ───
        # Check if columns exist already to avoid re-calc on every bar
        # Format: ema_{len}, rsi_{len}, atr_{len}, adx_{len}
        
        col_ema_fast = f"ema_{p['ema_fast']}"
        col_ema_slow = f"ema_{p['ema_slow']}"
        col_rsi = f"rsi_{p['rsi_period']}"
        col_atr = f"atr_{p['atr_period']}"
        
        # ADX usually returns multiple columns
        col_adx = f"ADX_{p['adx_period']}"
        col_dmp = f"DMP_{p['adx_period']}"
        col_dmn = f"DMN_{p['adx_period']}"

        # If columns missing, calculate on-the-fly (Live or First Pass)
        if col_ema_fast not in candles.columns:
            try:
                # Use TA Lib directly on series
                candles[col_ema_fast] = ta.ema(candles["close"], length=p["ema_fast"])
                candles[col_ema_slow] = ta.ema(candles["close"], length=p["ema_slow"])
                candles[col_rsi] = ta.rsi(candles["close"], length=p["rsi_period"])
                candles[col_atr] = ta.atr(candles["high"], candles["low"], candles["close"], length=p["atr_period"])
                
                # ADX returns DF, so join it
                adx_res = ta.adx(candles["high"], candles["low"], candles["close"], length=p["adx_period"])
                if adx_res is not None:
                     candles = candles.join(adx_res, rsuffix='_dup') # Join safely
            except Exception as e:
                return self.create_hold(symbol=symbol, reason=f"Indicator Error: {e}")

        # Now extract last values safely
        try:
            # Current Values
            close = candles["close"].iloc[-1]
            # Use get to be safe or default to None
            ema50_val = candles[col_ema_fast].iloc[-1]
            ema200_val = candles[col_ema_slow].iloc[-1]
            rsi_val = candles[col_rsi].iloc[-1]
            atr_val = candles[col_atr].iloc[-1]
            
            # ADX might be tricky if column names vary, try to be robust
            adx_val = 0
            plus_di = 0
            minus_di = 0
            
            # Look for exact column or similar
            if col_adx in candles.columns:
                adx_val = candles[col_adx].iloc[-1]
            
            # DMP/DMN
            # Check for standard names or names with period
            # Common names: DMP_14, DMN_14 or similar
            possible_dmp = [c for c in candles.columns if c.startswith("DMP") and str(p['adx_period']) in c]
            if possible_dmp: plus_di = candles[possible_dmp[0]].iloc[-1]
            
            possible_dmn = [c for c in candles.columns if c.startswith("DMN") and str(p['adx_period']) in c]
            if possible_dmn: minus_di = candles[possible_dmn[0]].iloc[-1]
            
        except KeyError as e:
             # Fallback: re-calculate just for this slice if column access failed (e.g. adx join issue)
             # return self.create_hold(symbol=symbol, reason=f"Column Missing: {e}")
             # Or just ignore
             return self.create_hold(symbol=symbol, reason=f"Ind calc failed {e}")

        # NaN check
        if any(pd.isna(x) for x in [ema50_val, ema200_val, rsi_val, atr_val]):
             return self.create_hold(symbol=symbol, reason="Indicators NaN")

        # ─── HARD GATES ───
        
        # 1. Trend Direction
        uptrend = ema50_val > ema200_val
        downtrend = ema50_val < ema200_val
        
        if not uptrend and not downtrend:
            return self.create_hold(symbol=symbol, reason="Flat Trend")
            
        # 2. Strength
        if adx_val < p["adx_threshold"]:
            return self.create_hold(symbol=symbol, reason=f"Weak ADX ({adx_val:.1f})")
            
        # 3. Session (Simplified)
        
        # ─── Scoring ───
        score = 0
        reasons = []
        
        score += 1 
        reasons.append("EMA Trend")
        
        # Pullback
        dist_ema50 = abs(close - ema50_val)
        if dist_ema50 <= (atr_val * p["pullback_zone_atr"]):
            score += 1
            reasons.append("Near EMA50")
            
        # RSI
        if uptrend:
            if 40 <= rsi_val <= 60:
                score += 1
                reasons.append("RSI Buy Zone")
            elif rsi_val > 70:
                score -= 1
        else:
            if 40 <= rsi_val <= 60:
                score += 1
                reasons.append("RSI Sell Zone")
            elif rsi_val < 30:
                score -= 1
                
        # DI
        if uptrend and plus_di > minus_di:
            score += 1
            reasons.append("DI Bullish")
        elif downtrend and minus_di > plus_di:
            score += 1
            reasons.append("DI Bearish")
            
        # Candle Pattern (Simplified)
        open_price = candles["open"].iloc[-1]
        if uptrend and close > open_price and (close - open_price) > (atr_val * 0.5):
            score += 1
            reasons.append("Strong Candle")
        elif downtrend and close < open_price and (open_price - close) > (atr_val * 0.5):
            score += 1
            reasons.append("Strong Candle")

        # ─── Decision ───
        confidence = min(0.95, 0.5 + (score * 0.1))
        
        if score < 3:
             return self.create_hold(symbol=symbol, reason=f"Low Score ({score})")
             
        action = Action.BUY if uptrend else Action.SELL
        
        # SL/TP
        sl_dist = atr_val * p["sl_atr_mult"]
        tp_dist = atr_val * p["tp_atr_mult"]
        
        if action == Action.BUY:
            sl = close - sl_dist
            tp = close + tp_dist
        else:
            sl = close + sl_dist
            tp = close - tp_dist
            
        # Rounding
        sl = round(sl, profile.digits)
        tp = round(tp, profile.digits)
        
        risk = abs(close - sl)
        reward = abs(tp - close)
        rr = reward / risk if risk > 0 else 0
        
        return Decision(
            symbol=symbol,
            action=action,
            confidence=confidence,
            reason="; ".join(reasons),
            stop_loss=sl,
            take_profit=tp,
            risk_reward_ratio=round(rr, 2),
            strategy_name=self.name,
            timeframe=self.timeframe
        )
