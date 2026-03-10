"""
Regime Intelligence Engine — Orchestrator ที่รวมทุกอย่าง.

หน้าที่:
    1. เรียก classify_regime() → regime + confidence
    2. ดึง risk profile จาก regime_risk_profile
    3. ถาม recommender → strategy ที่ดีที่สุด
    4. ถาม memory_store → best_params (learning loop)
    5. Return RegimeIntelligence JSON

ใช้ใน master_loop.py แทนที่ classify_regime() เดิม.
"""

from typing import Optional

import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import RegimeType
from app.domain.models import RegimeContext, RegimeIntelligence
from app.brain.regime import classify_regime
from app.brain.regime_risk_profile import get_risk_profile, is_tradable

logger = get_logger(__name__)


class RegimeIntelligenceEngine:
    """
    Orchestrator — วิเคราะห์สภาวะตลาดแบบครบวงจร.

    รวม:
        - Regime classifier (8 regimes)
        - Risk profile mapping
        - Brain recommender (optional)
        - Best params from learning loop (optional)
    """

    def __init__(
        self,
        memory_store=None,
        recommender=None,
    ) -> None:
        self.memory = memory_store
        self.recommender = recommender

    def analyze(
        self,
        symbol: str,
        candles: pd.DataFrame,
        session: str = "CLOSED",
        profile=None,
    ) -> RegimeIntelligence:
        """
        วิเคราะห์สภาวะตลาดและ return JSON ตัดสินใจรวม.

        Args:
            symbol: สัญลักษณ์ เช่น BTCUSD
            candles: DataFrame with OHLCV
            session: ASIA/LONDON/NY/OVERLAP/CLOSED
            profile: PersonalityProfile (optional)

        Returns:
            RegimeIntelligence: JSON output รวม
        """
        # ─── 1. Classify regime ───
        regime_ctx: RegimeContext = classify_regime(candles, profile)

        # Primary regime (new v2) vs compat regime (for strategy registry)
        primary_regime_str = regime_ctx.details.get("primary_regime", regime_ctx.regime.value)
        try:
            primary_regime = RegimeType(primary_regime_str)
        except ValueError:
            primary_regime = regime_ctx.regime

        direction = RegimeType.UNKNOWN
        dir_str = regime_ctx.details.get("direction", "UNKNOWN")
        try:
            direction = RegimeType(dir_str)
        except ValueError:
            pass

        confidence = regime_ctx.details.get("confidence", regime_ctx.score)

        # ─── 2. Risk profile ───
        risk_profile = get_risk_profile(primary_regime)

        # Override with best_params from learning loop
        if self.memory:
            try:
                best = self.memory.get_best_params(symbol, primary_regime.value)
                if best:
                    # Merge: best_params overrides defaults
                    for key in ("sl_atr", "tp_rr", "lot_multiplier"):
                        if key in best and best[key] is not None:
                            risk_profile[key] = best[key]
                    logger.debug("regime_best_params_applied", extra={
                        "symbol": symbol, "regime": primary_regime.value,
                        "params": best,
                    })
            except Exception as e:
                logger.debug("regime_best_params_error", extra={"error": str(e)})

        # ─── 3. Trade allowed ───
        # If regime has a positive lot_multiplier, allow trading (risk is managed by lot size)
        # Only block if lot_multiplier is 0 (ACCUMULATION, UNKNOWN)
        trade_allowed = is_tradable(primary_regime)

        # ─── 4. Strategy recommendation ───
        recommended = ""
        if self.recommender:
            try:
                rec = self.recommender.recommend(
                    symbol=symbol,
                    regime=regime_ctx.regime.value,  # use compat regime for existing brain data
                    session=session,
                )
                if rec:
                    recommended = rec
            except Exception:
                pass

        if not recommended:
            recommended = risk_profile.get("strategy_hint", "")

        # ─── 5. Build output ───
        reason_parts = [regime_ctx.reason]
        if not trade_allowed:
            reason_parts.append(f"trade_blocked: regime={primary_regime.value}")

        result = RegimeIntelligence(
            symbol=symbol,
            regime=primary_regime,
            direction=direction,
            confidence=round(confidence, 3),
            recommended_strategy=recommended,
            risk_profile=risk_profile,
            trade_allowed=trade_allowed,
            reason="; ".join(r for r in reason_parts if r),
            details=regime_ctx.details,
        )

        logger.info("regime_intelligence_result", extra={
            "symbol": symbol,
            "regime": primary_regime.value,
            "direction": direction.value,
            "confidence": result.confidence,
            "trade_allowed": trade_allowed,
            "lot_mult": risk_profile.get("lot_multiplier", 0),
            "strategy": recommended,
        })

        return result

    def get_regime_context(self, intelligence: RegimeIntelligence) -> RegimeContext:
        """
        แปลง RegimeIntelligence → RegimeContext (backward compat).
        ใช้ใน master_loop เพื่อไม่ต้องเปลี่ยน pipeline เดิม.
        """
        # compat: ใช้ directional type สำหรับ existing strategies
        compat_regime = intelligence.direction
        if compat_regime == RegimeType.UNKNOWN:
            compat_regime = intelligence.regime

        return RegimeContext(
            regime=compat_regime,
            actionable=intelligence.trade_allowed,
            score=intelligence.confidence,
            reason=intelligence.reason,
            details=intelligence.details,
        )
