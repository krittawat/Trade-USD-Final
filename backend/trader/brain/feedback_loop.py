# -*- coding: utf-8 -*-
"""
AI Feedback Loop — Dynamic Confidence Threshold Adjuster
=========================================================
PURPOSE:
    Reads the last N shadow_experience trades per strategy and adjusts
    the MIN_CONFIDENCE threshold dynamically.

LOGIC:
    - If a strategy's recent win rate < 40% → increase confidence requirement +5%
    - If a strategy's recent win rate < 30% → increase confidence requirement +10%  
    - If a strategy's recent win rate > 60% → decrease confidence requirement -3% (reward hot strategies)
    - Base confidence floor: 0.55 (never go below)
    - Base confidence ceiling: 0.80 (never demand more than 80%)

INTEGRATION:
    Called once per cycle from main.py BEFORE signal generation.
    Returns a dict of {strategy_name: adjusted_min_confidence}.

SAFETY:
    - Read-only on shadow_experience table
    - No trade execution decisions — purely advisory thresholds
    - Falls back to global MIN_CONFIDENCE if insufficient data
"""

import logging
from typing import Dict, Optional
from backend.trader.storage.sqlite_db import db

logger = logging.getLogger("opus_logger")

# ─── CONFIGURATION ──────────────────────────────────
LOOKBACK_TRADES = 20          # Number of recent trades to evaluate per strategy
MIN_TRADES_FOR_ADJUST = 5     # Need at least 5 trades before adjusting
BASE_CONFIDENCE = 0.58        # Default (matches settings.json min_confidence_trade)
CONFIDENCE_FLOOR = 0.55       # Never go below this
CONFIDENCE_CEILING = 0.80     # Never demand more than this

# Win rate thresholds and adjustments
COLD_STREAK_THRESHOLD = 0.40  # Below 40% win rate = cold
ICE_STREAK_THRESHOLD = 0.30   # Below 30% = ice cold
HOT_STREAK_THRESHOLD = 0.60   # Above 60% = hot

COLD_PENALTY = 0.05           # +5% confidence required
ICE_PENALTY = 0.10            # +10% confidence required
HOT_REWARD = 0.03             # -3% confidence required (easier to pass)


class AIFeedbackLoop:
    """
    Adaptive confidence adjuster based on shadow trade outcomes.
    """

    def __init__(self):
        self._cache: Dict[str, float] = {}
        self._last_refresh_cycle: int = -1
        self._refresh_interval: int = 5  # Refresh every 5 cycles

    def get_adjusted_confidence(self, strategy_name: str) -> float:
        """
        Returns the adjusted MIN_CONFIDENCE for a specific strategy.
        Falls back to BASE_CONFIDENCE if no data.
        """
        return self._cache.get(strategy_name, BASE_CONFIDENCE)

    def get_all_adjustments(self) -> Dict[str, float]:
        """Returns the full cache of adjustments for logging."""
        return dict(self._cache)

    def refresh(self, current_cycle: int = 0) -> Dict[str, float]:
        """
        Queries the shadow_experience table and recalculates thresholds.
        Only refreshes every N cycles to avoid DB spam.
        """
        if current_cycle > 0 and (current_cycle - self._last_refresh_cycle) < self._refresh_interval:
            return self._cache

        self._last_refresh_cycle = current_cycle

        try:
            # Query completed shadow trades grouped by strategy
            cursor = db.conn.cursor()
            cursor.execute('''
                SELECT model, outcome, COUNT(*) as cnt
                FROM shadow_experience
                WHERE outcome != 0
                GROUP BY model, outcome
                ORDER BY model
            ''')
            rows = cursor.fetchall()

            if not rows:
                logger.debug("FEEDBACK LOOP: No completed shadow trades yet — using defaults")
                return self._cache

            # Aggregate wins/losses per strategy
            strategy_stats: Dict[str, Dict[str, int]] = {}
            for model, outcome, count in rows:
                if model not in strategy_stats:
                    strategy_stats[model] = {"wins": 0, "losses": 0, "total": 0}
                if outcome == 1:
                    strategy_stats[model]["wins"] += count
                elif outcome == -1:
                    strategy_stats[model]["losses"] += count
                strategy_stats[model]["total"] += count

            # Also get recent performance (last LOOKBACK_TRADES per strategy)
            recent_stats = self._get_recent_stats(cursor)

            # Calculate adjusted thresholds
            adjustments = {}
            for model, stats in strategy_stats.items():
                total = stats["total"]
                if total < MIN_TRADES_FOR_ADJUST:
                    adjustments[model] = BASE_CONFIDENCE
                    continue

                # Use recent stats if available, else overall
                if model in recent_stats and recent_stats[model]["total"] >= MIN_TRADES_FOR_ADJUST:
                    use_stats = recent_stats[model]
                else:
                    use_stats = stats

                win_rate = use_stats["wins"] / use_stats["total"] if use_stats["total"] > 0 else 0.5

                # Apply adjustments
                adjusted = BASE_CONFIDENCE
                if win_rate < ICE_STREAK_THRESHOLD:
                    adjusted = BASE_CONFIDENCE + ICE_PENALTY
                    logger.info(
                        f"  🧊 FEEDBACK: {model} ICE COLD (WR={win_rate:.0%}) → "
                        f"Confidence +{ICE_PENALTY:.0%} = {adjusted:.2f}"
                    )
                elif win_rate < COLD_STREAK_THRESHOLD:
                    adjusted = BASE_CONFIDENCE + COLD_PENALTY
                    logger.info(
                        f"  ❄️ FEEDBACK: {model} COLD (WR={win_rate:.0%}) → "
                        f"Confidence +{COLD_PENALTY:.0%} = {adjusted:.2f}"
                    )
                elif win_rate > HOT_STREAK_THRESHOLD:
                    adjusted = BASE_CONFIDENCE - HOT_REWARD
                    logger.info(
                        f"  🔥 FEEDBACK: {model} HOT (WR={win_rate:.0%}) → "
                        f"Confidence -{HOT_REWARD:.0%} = {adjusted:.2f}"
                    )

                # Clamp to floor/ceiling
                adjusted = max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEILING, adjusted))
                adjustments[model] = adjusted

            self._cache = adjustments
            
            if adjustments:
                active_adjustments = {k: v for k, v in adjustments.items() if v != BASE_CONFIDENCE}
                if active_adjustments:
                    logger.info(f"  🧠 FEEDBACK LOOP: {len(active_adjustments)} strategies adjusted: "
                               f"{', '.join(f'{k}={v:.2f}' for k, v in active_adjustments.items())}")

            return self._cache

        except Exception as e:
            logger.error(f"FEEDBACK LOOP error: {e}", exc_info=True)
            return self._cache

    def _get_recent_stats(self, cursor) -> Dict[str, Dict[str, int]]:
        """Get stats for only the most recent LOOKBACK_TRADES per strategy."""
        try:
            cursor.execute('''
                SELECT model, outcome FROM (
                    SELECT model, outcome, 
                           ROW_NUMBER() OVER (PARTITION BY model ORDER BY id DESC) as rn
                    FROM shadow_experience
                    WHERE outcome != 0
                ) WHERE rn <= ?
            ''', (LOOKBACK_TRADES,))
            
            rows = cursor.fetchall()
            stats: Dict[str, Dict[str, int]] = {}
            for model, outcome in rows:
                if model not in stats:
                    stats[model] = {"wins": 0, "losses": 0, "total": 0}
                if outcome == 1:
                    stats[model]["wins"] += 1
                elif outcome == -1:
                    stats[model]["losses"] += 1
                stats[model]["total"] += 1
            return stats
        except Exception:
            return {}


# Singleton instance
feedback_loop = AIFeedbackLoop()
