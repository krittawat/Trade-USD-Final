"""
Recommender — แนะนำ strategy parameters จาก AI Brain.

หน้าที่:
    - ถาม Memory Store: "อะไรที่เคยใช้ได้ผล?"
    - แนะนำ strategy name + parameters สำหรับ Factory
    - ไม่ override Risk Engine decisions

วิธีทำงาน:
    1. ดูจาก strategy_performance table
    2. กรองตาม symbol + regime + session
    3. เรียงตาม profit_factor × win_rate
    4. Return recommendation
"""

from typing import Optional

from app.core.logging import get_logger

from app.brain.memory_store import MemoryStore
from app.brain.personality import PersonalityProfile

logger = get_logger(__name__)


class Recommender:
    """
    AI Strategy Recommender — แนะนำ strategy จากข้อมูลอดีต.
    
    ไม่มีสิทธิ์ override Risk Engine — แค่แนะนำเท่านั้น.
    """

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    def _heuristic_fallback(self, symbol: str, regime: str, session: str) -> Optional[str]:
        """
        Fallback strategy selection when no historical data exists (Cold Start).
        
        Logic:
            - TRENDING_UP/DOWN -> trend_rider
            - RANGING -> scalping
            - VOLATILE/BREAKOUT -> sniper
            - UNKNOWN -> sniper (conservative)
        """
        regime = regime.upper()
        
        # specific asset overrides
        if "XAU" in symbol.upper():
             if "TREND" in regime: return "gold_elite" # Gold Elite covers trends well
             if "VOLATI" in regime: return "gold_scalp_pro"
             if "RANG" in regime or "SIDEWAYS" in regime: return "gold_elite" # Fallback to Elite (it has sideways mode)

        if "XAG" in symbol.upper():
             if "RANG" in regime or "SIDEWAYS" in regime: return "silver_evolution"
             return "silver_evolution" # Default for Silver — WR>70% fusion

        if "BTC" in symbol.upper():
             return "btc_elite" # BTC Elite handles both Trend & MR

        if "JPY" in symbol.upper():
             return "usdjpy_elite" # Dual-mode (Trend + Range)
        
        if "TREND" in regime:
            return "trend_rider"
        elif "RANG" in regime or "SIDEWAYS" in regime:
            return "scalping" 
        elif "VOLATIL" in regime or "BREAKOUT" in regime:
            return "sniper"
        
        # Default safety
        return "sniper"

    def recommend(
        self,
        symbol: str,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
    ) -> Optional[str]:
        """
        แนะนำ strategy ที่ดีที่สุดสำหรับสภาวะปัจจุบัน.
        
        Returns:
            ชื่อ strategy (e.g., "scalping", "sniper")
            None ถ้ายังไม่มีข้อมูลเพียงพอ
        """
        recommendation = self.memory.get_best_strategy(symbol, regime, session)
        
        if recommendation:
            logger.info("brain_recommendation", extra={
                "symbol": symbol,
                "strategy": recommendation,
                "regime": regime,
                "session": session,
                "source": "memory"
            })
        else:
            # Fallback to Heuristic
            recommendation = self._heuristic_fallback(symbol, regime, session)
            logger.info("brain_recommendation", extra={
                "symbol": symbol,
                "strategy": recommendation,
                "regime": regime,
                "session": session,
                "source": "heuristic_fallback"
            })

        return recommendation

    def recommend_with_params(
        self,
        symbol: str,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
    ) -> dict:
        """
        แนะนำ strategy + evolved parameters.

        Returns:
            dict: {
                "strategy": str | None,
                "params": dict | None,  # evolved params ถ้ามี
            }
        """
        strategy = self.recommend(symbol, regime, session)
        params = None

        if strategy:
            # ดึง evolved params จาก MemoryStore
            params = self.memory.get_evolved_params(
                strategy_name=strategy,
                symbol=symbol,
                regime="ALL",  # ใช้ ALL regime ก่อน
            )

            if params:
                logger.info("brain_evolved_params_found", extra={
                    "symbol": symbol,
                    "strategy": strategy,
                    "params_keys": list(params.keys()),
                })

        return {
            "strategy": strategy,
            "params": params,
        }

    def recommend_with_patterns(
        self,
        symbol: str,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
    ) -> dict:
        """
        แนะนำ strategy + patterns ที่มี win_rate สูง.

        Returns:
            dict: {
                "strategy": str | None,
                "params": dict | None,
                "best_patterns": [{pattern_name, win_rate, total_trades}],
            }
        """
        base = self.recommend_with_params(symbol, regime, session)

        # ดึง best patterns จาก MemoryStore
        best_patterns = self.memory.get_best_patterns(
            symbol=symbol,
            regime=regime,
            min_trades=5,
            limit=5,
        )

        if best_patterns:
            logger.info("brain_pattern_guidance", extra={
                "symbol": symbol,
                "top_pattern": best_patterns[0]["pattern_name"] if best_patterns else None,
                "pattern_count": len(best_patterns),
            })

        base["best_patterns"] = best_patterns
        return base

    def recommend_with_intelligence(
        self,
        symbol: str,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
        ml_win_prob: float = 0.5,
        sentiment_score: float = 0.0,
    ) -> dict:
        """
        แนะนำ strategy โดยรวม Brain + ML + Sentiment.

        Weighted composite:
            final_confidence = 0.5 × brain + 0.3 × ml_score + 0.2 × sentiment

        Args:
            ml_win_prob: ML model win probability (0.0-1.0)
            sentiment_score: sentiment composite (-1.0 to 1.0)

        Returns:
            dict: {
                "strategy": str | None,
                "params": dict | None,
                "best_patterns": [...],
                "ml_win_prob": float,
                "sentiment_score": float,
                "confidence_boost": float,  # -0.5 to +0.5
                "intelligence_signals": {...},
            }
        """
        base = self.recommend_with_patterns(symbol, regime, session)

        # ML signal: convert 0-1 to -1 to +1 scale
        ml_signal = (ml_win_prob - 0.5) * 2.0  # 0.5→0, 1.0→+1, 0.0→-1

        # Weighted confidence boost
        # Brain score: use win_rate from best strategy performance
        brain_score = 0.0
        if base.get("strategy"):
            perf = self.memory.get_strategy_performance(
                strategy_name=base["strategy"],
                symbol=symbol,
                regime=regime,
            )
            if perf:
                brain_score = (perf.get("win_rate", 0.5) - 0.5) * 2.0

        # Fallback: if brain has no data, use shadow scoreboard
        if brain_score == 0.0 and base.get("strategy"):
            try:
                shadow_wr = self.memory.get_shadow_win_rate(
                    strategy_name=base["strategy"],
                    symbol=symbol,
                )
                if shadow_wr is not None:
                    brain_score = (shadow_wr / 100.0 - 0.5) * 2.0
                    logger.debug("brain_shadow_fallback", extra={
                        "symbol": symbol,
                        "strategy": base["strategy"],
                        "shadow_wr": shadow_wr,
                        "brain_score": round(brain_score, 3),
                    })
            except Exception:
                pass  # Shadow data not available yet

        # Composite boost: -0.5 to +0.5
        boost = (
            0.5 * brain_score +
            0.3 * ml_signal +
            0.2 * sentiment_score
        ) * 0.5  # Scale to ±0.5 range

        boost = max(-0.5, min(0.5, boost))

        intelligence = {
            "brain_score": round(brain_score, 3),
            "ml_signal": round(ml_signal, 3),
            "sentiment_signal": round(sentiment_score, 3),
            "composite_boost": round(boost, 3),
        }

        base["ml_win_prob"] = round(ml_win_prob, 3)
        base["sentiment_score"] = round(sentiment_score, 3)
        base["confidence_boost"] = round(boost, 3)
        base["intelligence_signals"] = intelligence

        logger.info("brain_intelligence_recommendation", extra={
            "symbol": symbol,
            "strategy": base.get("strategy"),
            "boost": round(boost, 3),
            **intelligence,
        })

        return base
    
    def recommend_with_personality(
        self,
        symbol: str,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
        personality: Optional["PersonalityProfile"] = None,
    ) -> dict:
        """
        แนะนำ strategy โดยใช้ข้อมูล Personality (Behavioral DNA).

        Logic:
            - ถ้า fakeout_prob สูง -> เลี่ยง Breakout / Pattern Followers
            - ถ้า volatility_score สูง -> ลด confidence ของ Grid / Averaging (ถ้ามี)
            - ถ้า trend_persistence สูง -> Boost Trend Strategies
        """
        base = self.recommend_with_patterns(symbol, regime, session)
        strategy = base.get("strategy")
        
        if not strategy or not personality:
            return base

        # Personality Adjustments
        boost = 0.0
        warnings = []

        # 1. Fakeout logic
        if personality.fakeout_probability > 0.6:
            if "breakout" in strategy.lower():
                boost -= 0.3
                warnings.append("High fakeout probability -> Avoid Breakout")
            elif "sniper" in strategy.lower():
                boost += 0.1  # Sniper อาจชอบ fakeout (entry ปลายไส้)

        # 2. Trend Persistence
        if personality.trend_persistence > 0.6: # Strong trending behavior
            if "trend" in strategy.lower():
                boost += 0.2
            elif "mean_reversion" in strategy.lower():
                boost -= 0.2
                warnings.append("Strong trend persistence -> Risky for Mean Reversion")
        
        # 3. Volatility
        if personality.volatility_score > 80: # Volatility สูงมาก
            boost -= 0.1 # General caution
            warnings.append("Extreme volatility -> Reduce size/confidence")

        # Apply boost to confidence (if available in future)
        # For now, just store in metadata
        base["personality_boost"] = round(boost, 2)
        base["personality_warnings"] = warnings
        
        logger.info("brain_personality_recommendation", extra={
            "symbol": symbol,
            "strategy": strategy,
            "boost": boost,
            "warnings": warnings
        })

        return base

