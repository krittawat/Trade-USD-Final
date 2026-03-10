from typing import Dict, Any, Optional, Tuple
import logging
from app.strategy.base_strategy import StrategyDecision, SignalType
from app.services.questdb_service import questdb_service
from app.config import GLOBAL_DAILY_MAX_USD, DEFAULT_MAX_DAILY_LOSS_PCT

logger = logging.getLogger("StrategyValidator")

class StrategyValidator:
    """
    Governance Layer for Strategy Decisions.
    Enforces Hard Blocks and Safety Rules.
    """
    
    @staticmethod
    def validate_decision(decision: StrategyDecision, 
                         symbol: str, 
                         account_state: Dict,
                         market_state: Dict) -> StrategyDecision:
        """
        Validates and potentially overrides a strategy decision based on global rules.
        """
        
        # 0. Pass-through HOLD/WAIT
        if decision.action in [SignalType.HOLD, SignalType.WAIT, SignalType.NO_TRADE]:
            return decision

        # 1. Hard Block: Spread Guard
        # Need spread info from market_state
        spread = market_state.get('spread', 0.0)
        max_spread = market_state.get('max_spread', 500) # Default high
        if spread > max_spread:
            logger.warning(f"🚫 BLOCKED {symbol}: Spread {spread} > {max_spread}")
            return StrategyDecision(
                action=SignalType.HOLD,
                confidence=0,
                reason=f"SPREAD_GUARD: {spread} > {max_spread}"
            )

        # 2. Hard Block: Floating Drawdown > 10%
        # (This is also in RiskManager, but good to have here for redundancy)
        equity = account_state.get('equity', 0)
        balance = account_state.get('balance', 0)
        if balance > 0:
            dd_pct = (balance - equity) / balance
            if dd_pct > 0.10:
                logger.warning(f"🚫 BLOCKED {symbol}: Floating DD {dd_pct*100:.1f}% > 10%")
                return StrategyDecision(
                    action=SignalType.HOLD,
                    confidence=0,
                    reason=f"FLOATING_DD_BLOCK: {dd_pct*100:.1f}%"
                )

        # 3. Hard Block: News Filter (if provided in state)
        news_blocked = market_state.get('news_blocked', False)
        if news_blocked:
             return StrategyDecision(
                action=SignalType.HOLD,
                confidence=0,
                reason="NEWS_BLOCK"
            )

        # 4. CRITICAL: Stop Loss Check
        if decision.stop_loss is None or decision.stop_loss <= 0:
            logger.error(f"❌ REJECTED {symbol}: Strategy {decision.action} missing STOP LOSS")
            return StrategyDecision(
                action=SignalType.HOLD,
                confidence=0,
                reason="MISSING_SL: Safety Violation"
            )

        return decision

validator = StrategyValidator()
