from app.domain.enums import Action
from .base_strategy import BaseStrategy, StrategyDecision
from typing import Dict, Any, Optional

class TrendFilterM15Strategy(BaseStrategy):
    """
    Strategy using M15 EMA 50/200 Trend Filter for M5 trading.
    As requested by user: M15 Trend Filter M15 for M5 Trading.
    """
    EMA_FAST = 50
    EMA_SLOW = 200

    def analyze(self, df, **kwargs) -> StrategyDecision:
        # This strategy specifically looks at M15 data
        # In multi-symbol StreamManager, this will be fed from the M15 aggregator
        ema_fast_m15 = kwargs.get('ema50_m15', 0)
        ema_slow_m15 = kwargs.get('ema200_m15', 0)
        price_m15 = kwargs.get('price_m15', 0)
        
        # Also need current M5 signal to filter
        m5_signal = kwargs.get('m5_signal', Action.HOLD)
        m5_reason = kwargs.get('m5_reason', "")
        
        trend_bias = "NEUTRAL"
        if ema_fast_m15 > ema_slow_m15 and price_m15 > ema_fast_m15:
            trend_bias = "LONG_ONLY"
        elif ema_fast_m15 < ema_slow_m15 and price_m15 < ema_fast_m15:
            trend_bias = "SHORT_ONLY"
            
        final_signal = Action.HOLD
        reason = f"Trend Filter (M15): {trend_bias}"
        
        if trend_bias == "LONG_ONLY" and m5_signal == Action.BUY:
            final_signal = Action.BUY
            reason = f"CONFIRMED LONG (M15 Bias): {m5_reason}"
        elif trend_bias == "SHORT_ONLY" and m5_signal == Action.SELL:
            final_signal = Action.SELL
            reason = f"CONFIRMED SHORT (M15 Bias): {m5_reason}"
        else:
            reason = f"FILTERED: M15 {trend_bias}, M5 {m5_signal}"

        # Convert Action to string for StrategyDecision
        signal_str = final_signal.value if hasattr(final_signal, "value") else str(final_signal)
        
        return StrategyDecision(
            signal=signal_str,
            sl=kwargs.get('sl') if final_signal != Action.HOLD else None,
            tp=kwargs.get('tp') if final_signal != Action.HOLD else None,
            reason=reason,
            confidence=0.9 if final_signal != Action.HOLD else 0.0,
            strategy_name="TrendFilterM15",
            # debug={"m15_bias": trend_bias, "m5_signal": m5_signal} # debug field might not exist in StrategyDecision definition?
            # StrategyDecision in base_strategy.py does NOT have debug field. It has entry_price, risk_pct etc.
            # Removing debug to be safe or checking base_strategy.py again.
            # BaseStrategy: entry_method, risk_reward_ratio... no debug.
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": "M15 Trend Filter",
            "config": {
                "ema_fast": self.EMA_FAST,
                "ema_slow": self.EMA_SLOW,
                "tf": "M15"
            }
        }
