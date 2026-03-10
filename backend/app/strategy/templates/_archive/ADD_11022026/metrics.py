from dataclasses import dataclass, field
from typing import Dict, List, Optional
import logging

logger = logging.getLogger("StrategyMetrics")

@dataclass
class StrategyStats:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    profit_usd: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    
    # Discipline Metrics
    overtrade_attempts: int = 0
    sl_missing_incidents: int = 0
    blocked_trades: int = 0

class StrategyMetrics:
    """
    Tracks performance and 'Discipline' metrics for each strategy.
    """
    def __init__(self):
        self.stats: Dict[str, StrategyStats] = {}

    def get_stats(self, strategy_name: str) -> StrategyStats:
        if strategy_name not in self.stats:
            self.stats[strategy_name] = StrategyStats()
        return self.stats[strategy_name]

    def record_trade(self, strategy_name: str, profit: float):
        s = self.get_stats(strategy_name)
        s.total_trades += 1
        s.profit_usd += profit
        
        if profit > 0:
            s.wins += 1
        else:
            s.losses += 1
            
        # Update derived stats
        s.win_rate = (s.wins / s.total_trades) if s.total_trades > 0 else 0.0
        
        # Simple Profit Factor Approx (Need total gross profit/loss for real calc)
        # s.profit_factor = ... (omitted for simple implementation)

        logger.info(f"📊 {strategy_name} Stats: {s.wins}/{s.total_trades} (WR: {s.win_rate*100:.1f}%) PnL: ${s.profit_usd:.2f}")

    def record_violation(self, strategy_name: str, violation_type: str):
        """
        Record a discipline violation.
        Types: 'OVERTRADE', 'MISSING_SL', 'BLOCKED'
        """
        s = self.get_stats(strategy_name)
        
        if violation_type == 'OVERTRADE':
            s.overtrade_attempts += 1
        elif violation_type == 'MISSING_SL':
            s.sl_missing_incidents += 1
            logger.error(f"🚨 DISCIPLINE VIOLATION: {strategy_name} attempted trade without SL!")
        elif violation_type == 'BLOCKED':
            s.blocked_trades += 1

metrics = StrategyMetrics()
