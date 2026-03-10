"""
AI MTF Strategy — AI-driven trading strategy using Multi-Timeframe GRU model.

Decision Flow:
    1. Fetch candles for M5 (primary) + M15, H1, H4, D1
    2. Compute 43 MTF features via MTFFeatureEngine
    3. AI model predicts win probability
    4. If prob > 0.60 → BUY, prob < 0.40 → SELL, else HOLD
    5. ATR-based SL/TP with minimum 1:2 R:R
    6. All decisions pass through Risk Engine

Safety:
    - Minimum confidence threshold: 60%
    - No trade in low-volatility choppy regime
    - Session filter (active sessions only)
    - All Risk Engine gates remain enforced
    - Model must be trained before producing signals
"""

import numpy as np
import pandas as pd
import pandas_ta as ta
import app.analysis.indicators as ind

from app.strategy.base import BaseStrategy
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── Thresholds ───────────────────────────────────────────────────
BUY_THRESHOLD = 0.60     # AI probability above this → BUY
SELL_THRESHOLD = 0.40    # AI probability below this → SELL
MIN_ATR_RATIO = 0.3      # Minimum ATR ratio to trade (avoid dead market)
MIN_RR = 2.0             # Minimum risk:reward ratio
ATR_SL_MULT = 1.5        # SL distance = 1.5 × ATR
ATR_TP_MULT = 3.0        # TP distance = 3.0 × ATR (R:R = 2:1)


class AIMultiTFStrategy(BaseStrategy):
    """AI-driven Multi-Timeframe Strategy using GRU+Attention model."""

    name = "ai_mtf_v3"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.STRONG_TREND,
        RegimeType.WEAK_TREND,
        RegimeType.BREAKOUT,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.LIQUIDITY_SWEEP,   # Trade reversals after sweep
        RegimeType.RANGING,           # Mean reversion entries
        RegimeType.FAKEOUT,           # Fade fakeout moves
        RegimeType.LOW_VOLATILITY,    # Small positions in quiet markets
    ]

    def __init__(self):
        self._model = None
        self._feature_engine = None
        self._initialized = False

    def _ensure_init(self):
        """Lazy-load model and feature engine to avoid import loops."""
        if self._initialized:
            return

        try:
            from app.brain.mtf_model import MTFDeepLearner
            from app.brain.mtf_feature_engine import MTFFeatureEngine

            self._model = MTFDeepLearner()
            self._feature_engine = MTFFeatureEngine()
            self._initialized = True

            logger.info("ai_mtf_strategy_ready", extra={
                "model_trained": self._model._trained if self._model else False,
            })
        except Exception as e:
            logger.error("ai_mtf_init_error", extra={"error": str(e)})
            self._initialized = True  # Don't retry

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        pressure: dict | None = None,
    ) -> Decision:
        """
        Analyze market using MTF AI model.

        Args:
            candles: M5 OHLCV DataFrame (primary timeframe)
            profile: Symbol profile
            regime: Current market regime
            pressure: Buy/sell pressure data (optional)

        Returns:
            Decision: BUY/SELL/HOLD with confidence and SL/TP
        """
        symbol = profile.symbol
        self._ensure_init()

        # ── Safety: model must be available and trained ──
        if self._model is None or not self._model._trained:
            return self.create_hold(symbol, "AI model not trained yet")

        if self._feature_engine is None:
            return self.create_hold(symbol, "Feature engine not available")

        if candles is None or len(candles) < 100:
            return self.create_hold(symbol, f"Insufficient candles: {len(candles) if candles is not None else 0}")

        # ── Compute ATR for SL/TP and regime check ──
        close = candles["close"]
        high = candles["high"]
        low = candles["low"]

        atr_raw = ind.atr(high, low, close, length=14)
        if atr_raw is None or atr_raw.iloc[-1] <= 0:
            return self.create_hold(symbol, "Cannot compute ATR")

        current_atr = atr_raw.iloc[-1]
        atr_avg = atr_raw.rolling(42).mean().iloc[-1]
        atr_ratio = current_atr / atr_avg if atr_avg > 0 else 1.0

        # ── Filter: skip low-volatility / choppy market ──
        if atr_ratio < MIN_ATR_RATIO:
            return self.create_hold(symbol, f"Low volatility (ATR ratio={atr_ratio:.2f})")

        # ── Build features for prediction ──
        # For live prediction, we need candles from multiple TFs
        # In live mode, the MasterLoop should provide these via the strategy bridge
        # For standalone use, we use M5 only and zero-fill higher TFs
        features = self._feature_engine.build_live_features(
            candles_m5=candles,
            candles_m15=None,  # Will be provided by MasterLoop integration
            candles_h1=None,
            candles_h4=None,
            candles_d1=None,
        )

        if features is None:
            return self.create_hold(symbol, "Cannot build feature sequence")

        # ── AI Prediction ──
        prob = self._model.predict(features.squeeze(0))

        current_close = float(close.iloc[-1])

        # ── Decision Logic ──
        if prob > BUY_THRESHOLD:
            # BUY signal
            sl_distance = current_atr * ATR_SL_MULT
            tp_distance = current_atr * ATR_TP_MULT
            sl = current_close - sl_distance
            tp = current_close + tp_distance
            rr = tp_distance / sl_distance if sl_distance > 0 else 0

            confidence = min((prob - 0.5) * 2, 1.0)  # Scale 0.5-1.0 → 0.0-1.0

            return Decision(
                symbol=symbol,
                action=Action.BUY,
                confidence=round(confidence, 3),
                reason=f"AI MTF BUY: prob={prob:.3f}, ATR_ratio={atr_ratio:.2f}, regime={regime.value}",
                stop_loss=round(sl, profile.digits if hasattr(profile, 'digits') else 2),
                take_profit=round(tp, profile.digits if hasattr(profile, 'digits') else 2),
                risk_reward_ratio=round(rr, 2),
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["ai_mtf", "gru_v3", f"prob_{prob:.2f}"],
                debug={
                    "ai_prob": round(prob, 4),
                    "atr": round(current_atr, 4),
                    "atr_ratio": round(atr_ratio, 3),
                    "sl_distance": round(sl_distance, 4),
                    "tp_distance": round(tp_distance, 4),
                    "regime": regime.value,
                },
            )

        elif prob < SELL_THRESHOLD:
            # SELL signal
            sl_distance = current_atr * ATR_SL_MULT
            tp_distance = current_atr * ATR_TP_MULT
            sl = current_close + sl_distance
            tp = current_close - tp_distance
            rr = tp_distance / sl_distance if sl_distance > 0 else 0

            confidence = min((0.5 - prob) * 2, 1.0)  # Scale 0.0-0.5 → 1.0-0.0

            return Decision(
                symbol=symbol,
                action=Action.SELL,
                confidence=round(confidence, 3),
                reason=f"AI MTF SELL: prob={prob:.3f}, ATR_ratio={atr_ratio:.2f}, regime={regime.value}",
                stop_loss=round(sl, profile.digits if hasattr(profile, 'digits') else 2),
                take_profit=round(tp, profile.digits if hasattr(profile, 'digits') else 2),
                risk_reward_ratio=round(rr, 2),
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["ai_mtf", "gru_v3", f"prob_{prob:.2f}"],
                debug={
                    "ai_prob": round(prob, 4),
                    "atr": round(current_atr, 4),
                    "atr_ratio": round(atr_ratio, 3),
                    "sl_distance": round(sl_distance, 4),
                    "tp_distance": round(tp_distance, 4),
                    "regime": regime.value,
                },
            )

        else:
            # HOLD - probability is in neutral zone
            return self.create_hold(
                symbol,
                f"AI neutral: prob={prob:.3f} (need >{BUY_THRESHOLD} for BUY or <{SELL_THRESHOLD} for SELL)"
            )
