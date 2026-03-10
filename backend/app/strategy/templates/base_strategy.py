"""
Legacy base_strategy compatibility shim.

Re-exports BaseStrategy, StrategyDecision, and SignalType from the archived
implementation so that older strategies (gold_scalp_pro, silver_mean_rev, etc.)
continue to work without import changes.

New strategies should use:
    from app.strategy.base import BaseStrategy  (framework ABC)
    from app.domain.models import Decision       (canonical Decision)
"""

from app.strategy.templates._archive.base_strategy import (
    BaseStrategy,
    StrategyBase,
    StrategyDecision,
    SignalType,
)

__all__ = ["BaseStrategy", "StrategyBase", "StrategyDecision", "SignalType"]
