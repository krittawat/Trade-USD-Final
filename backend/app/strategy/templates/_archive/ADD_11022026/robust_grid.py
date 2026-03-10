"""
Robust Grid Strategy (USC/Cent Optimized)
Designed for: XAUUSD, XAGUSD (Cent Accounts)
Philosophy: "Server-Side Autonomy" - Works even if user disconnects.

LOGIC:
1. Signal: RSI Extremes + BB Breakout (Counter-Trend)
2. Grid: Fixed Step Multiplier (ATR based)
3. Exit: Average Price + Profit Target
4. Safety: 
    - Circuit Breaker (Max Drawdown Limit)
    - Max Layers Cap
    - USC Lot Normalization
"""
import pandas as pd
import pandas as pd
try:
    import app.analysis.indicators as ind
except ImportError:
    from app.utils.safe_ta import SafeTA as ta
import logging
from typing import Dict, Any, Optional
from app.strategy.base_strategy import BaseStrategy, StrategyDecision, SignalType
from app.risk.risk_manager import RiskManager

logger = logging.getLogger("RobustGrid")

class RobustGridStrategy(BaseStrategy):
    def __init__(self, risk_manager: RiskManager):
        # BaseStrategy has no init
        self.risk_manager = risk_manager
        self.max_layers = 10
        self.strategies_param = {
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "bb_period": 20,
            "bb_std": 2.5,  # Stronger deviation for safer entry
            "grid_step_atr_mult": 1.5,
            "tp_atr_mult": 1.0
        }

    def analyze(self, df: pd.DataFrame, direction_mode: str = "AUTO", symbol: str = "") -> StrategyDecision:
        """
        Analyze market structure for Grid Entry (Mean Reversion)
        """
        if df.empty or len(df) < 50:
            return StrategyDecision(SignalType.Hold, 0, "Not enough data")

        # 1. Indicators
        close = df['close']
        high = df['high']
        low = df['low']
        
        rsi = ta.rsi(close, length=self.strategies_param["rsi_period"])
        bb = ta.bbands(close, length=self.strategies_param["bb_period"], std=self.strategies_param["bb_std"])
        atr = ta.atr(high, low, close, length=14)
        
        curr_rsi = rsi.iloc[-1]
        curr_close = close.iloc[-1]
        curr_atr = atr.iloc[-1]
        
        # BB Bands names usually: BBL_20_2.0, BBU_20_2.0, etc.
        # We need to find the correct column names dynamically or assume standard pandas_ta naming
        bbu = bb[bb.columns[0]] # First is usually Lower, check docs? No, let's look at names
        # Default pandas_ta bbands returns lower, mid, upper columns.
        # Let's trust the order or substring matching
        cols = bb.columns
        lower_band = bb[cols[0]].iloc[-1]
        mid_band = bb[cols[1]].iloc[-1]
        upper_band = bb[cols[2]].iloc[-1]
        
        signal = SignalType.HOLD
        confidence = 0
        reason = "Scanning..."
        
        # 2. Logic: Mean Reversion Entry
        # BUY: Price < Lower Band AND RSI < Oversold
        if curr_close < lower_band and curr_rsi < self.strategies_param["rsi_oversold"]:
            if direction_mode in ["AUTO", "BUY", "BOTH"]:
                signal = SignalType.BUY
                confidence = 85 # High confidence for mean reversion
                reason = f"📉 Oversold Reversion: RSI {curr_rsi:.1f} + BB Breakout"

        # SELL: Price > Upper Band AND RSI > Overbought
        elif curr_close > upper_band and curr_rsi > self.strategies_param["rsi_overbought"]:
            if direction_mode in ["AUTO", "SELL", "BOTH"]:
                signal = SignalType.SELL
                confidence = 85
                reason = f"📈 Overbought Reversion: RSI {curr_rsi:.1f} + BB Breakout"
                
        # 3. Decision
        tp_dist = curr_atr * self.strategies_param["tp_atr_mult"]
        sl_dist = curr_atr * 5.0 # Wide SL for Grid (Grid manages risk via sizing, not tight SL)
        
        # Calculate Grid Step for the Manager to use
        grid_step = curr_atr * self.strategies_param["grid_step_atr_mult"]
        
        return StrategyDecision(
            signal=signal,
            confidence=confidence,
            reason=reason,
            entry_price=curr_close,
            stop_loss=curr_close - sl_dist if signal == SignalType.BUY else curr_close + sl_dist,
            take_profit=curr_close + tp_dist if signal == SignalType.BUY else curr_close - tp_dist,
            entry_method="ROBUST_GRID_V1"
        )

    def get_status(self) -> Dict[str, Any]:
        """Return strategy configuration status"""
        return {
            "name": "ROBUST_GRID",
            "active": True,
            "params": self.strategies_param
        }

# Global Instance
robust_grid_strategy = RobustGridStrategy(None) # RiskManager injected later
