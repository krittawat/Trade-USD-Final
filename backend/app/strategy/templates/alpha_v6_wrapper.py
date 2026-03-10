# -*- coding: utf-8 -*-
"""
Alpha V6 Strategy Wrapper
Bridges the functional Alpha V6 logic with the BaseStrategy class for Backtesting.
"""

import pandas as pd
from typing import Dict, Any, Optional

from app.strategy.base import BaseStrategy
from app.domain.models import SymbolProfile, Decision
from app.domain.enums import Action, RegimeType
from app.core.logging import get_logger
from trader.strategy.antigravity_alpha_v6 import signal_antigravity_alpha_v6, _get_cfg

logger = get_logger("AlphaV6Wrapper")

class AlphaV6Strategy(BaseStrategy):
    """
    Wrapper for signal_antigravity_alpha_v6 to use with FullFeatureBacktester.
    """
    name = "ALPHA_V6_INSTITUTIONAL"
    timeframe = "M5"
    
    def __init__(self, overrides: Dict[str, Any] = None):
        self.overrides = overrides or {}

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        pressure: dict | None = None,
        **kwargs
    ) -> Decision:
        """
        Adapts the functional signal_antigravity_alpha_v6 to canonical Decision.
        """
        symbol = profile.symbol
        # Prepare context (same as selector.py)
        context = {
            "symbol": symbol,
            "timeframe": self.timeframe,
            "htf_ema_align": kwargs.get("htf_ema_align", "NEUTRAL"),
            "macro_sentiment": kwargs.get("macro_sentiment", 0),
            "params": self.overrides # Pass parameter overrides for Optimization
        }
        
        signal_result = signal_antigravity_alpha_v6(candles, context)
        
        if signal_result and signal_result.get("side") in ["BUY", "SELL"]:
            side = signal_result["side"]
            action = Action.BUY if side == "BUY" else Action.SELL
            
            return Decision(
                symbol=symbol,
                action=action,
                confidence=signal_result.get("confidence", 0.5),
                reason=" | ".join(signal_result["rationale"]),
                stop_loss=signal_result["sl"],
                take_profit=signal_result["tp1"],
                strategy_name=self.name,
                timeframe=self.timeframe
            )
            
        return self.create_hold(symbol, "No confirmation")

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "overrides": self.overrides
        }
