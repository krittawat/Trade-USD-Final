"""
AI Deep V4 Strategy — Transformer-GRU per-symbol strategy with 3-class prediction.

Decision Flow:
    1. Load per-symbol V4 model (deep_v4_XAUUSDc.pth, etc.)
    2. Build 55-feature sequence from live MTF candles
    3. Model predicts: P(BUY), P(HOLD), P(SELL)
    4. Trade only when max signal > threshold AND P(HOLD) is low
    5. ATR-based SL/TP with symbol-specific tuning
    6. Falls back to V3 model if V4 not trained

Safety:
    - 3-class output means model explicitly votes "don't trade"
    - Minimum confidence: BUY/SELL prob > 0.45, HOLD prob < 0.40
    - Session + volatility filters remain active
    - All Risk Engine gates enforced
    - Per-symbol model = no cross-contamination
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
BUY_THRESHOLD = 0.45     # P(BUY) must exceed this
SELL_THRESHOLD = 0.45    # P(SELL) must exceed this
HOLD_MAX = 0.40          # P(HOLD) must be below this to trade
MIN_ATR_RATIO = 0.3      # Minimum ATR ratio to trade
MIN_RR = 2.0             # Minimum risk:reward ratio

# Symbol-specific SL/TP multipliers (tuned per asset class)
SYMBOL_PARAMS = {
    "XAUUSDc": {"sl_mult": 1.5, "tp_mult": 3.0},
    "XAGUSDc": {"sl_mult": 1.8, "tp_mult": 3.6},
    "BTCUSDc": {"sl_mult": 1.2, "tp_mult": 2.4},
}
DEFAULT_PARAMS = {"sl_mult": 1.5, "tp_mult": 3.0}


class AIDeepV4Strategy(BaseStrategy):
    """AI-driven per-symbol strategy using Transformer-GRU V4 model."""

    name = "ai_deep_v4"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.STRONG_TREND,
        RegimeType.WEAK_TREND,
        RegimeType.BREAKOUT,
        RegimeType.HIGH_VOLATILITY,
    ]

    def __init__(self):
        self._models = {}           # {symbol: DeepModelV4}
        self._feature_engine = None
        self._fallback_model = None  # V3 fallback
        self._initialized = False

    def _ensure_init(self, symbol: str):
        """Lazy-load per-symbol model and feature engine."""
        if not self._initialized:
            try:
                from app.brain.mtf_feature_engine import MTFFeatureEngine
                self._feature_engine = MTFFeatureEngine()
                self._initialized = True
            except Exception as e:
                logger.error("deep_v4_strategy_init_error", extra={"error": str(e)})
                self._initialized = True
                return

        # Load symbol-specific model if not yet loaded
        if symbol not in self._models:
            try:
                from app.brain.deep_model_v4 import DeepModelV4
                model = DeepModelV4(symbol)
                self._models[symbol] = model
                logger.info("deep_v4_model_loaded_for_strategy", extra={
                    "symbol": symbol,
                    "trained": model._trained,
                })
            except Exception as e:
                logger.error("deep_v4_model_load_error", extra={
                    "symbol": symbol,
                    "error": str(e),
                })

    def _get_fallback(self):
        """Get V3 fallback model."""
        if self._fallback_model is None:
            try:
                from app.brain.mtf_model import MTFDeepLearner
                self._fallback_model = MTFDeepLearner()
            except Exception:
                pass
        return self._fallback_model

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        pressure: dict | None = None,
    ) -> Decision:
        """
        Analyze market using per-symbol V4 deep model.

        Args:
            candles: M5 OHLCV DataFrame (primary timeframe)
            profile: Symbol profile
            regime: Current market regime

        Returns:
            Decision: BUY/SELL/HOLD with confidence and SL/TP
        """
        symbol = profile.symbol
        self._ensure_init(symbol)

        # ── Check model availability ──
        model = self._models.get(symbol)
        if model is None or not model._trained:
            # Try V3 fallback
            fallback = self._get_fallback()
            if fallback and fallback._trained:
                return self._fallback_analyze(candles, profile, regime, fallback)
            return self.create_hold(symbol, f"V4 model not trained for {symbol}")

        if self._feature_engine is None:
            return self.create_hold(symbol, "Feature engine not available")

        if candles is None or len(candles) < 120:
            return self.create_hold(
                symbol,
                f"Insufficient candles: {len(candles) if candles is not None else 0}"
            )

        # ── Compute ATR ──
        close = candles["close"]
        high = candles["high"]
        low = candles["low"]

        atr_raw = ta.atr(high, low, close, length=14)
        if atr_raw is None or atr_raw.iloc[-1] <= 0:
            return self.create_hold(symbol, "Cannot compute ATR")

        current_atr = atr_raw.iloc[-1]
        atr_avg = atr_raw.rolling(42).mean().iloc[-1]
        atr_ratio = current_atr / atr_avg if atr_avg > 0 else 1.0

        # ── Filter: skip low-volatility ──
        if atr_ratio < MIN_ATR_RATIO:
            return self.create_hold(symbol, f"Low volatility (ATR ratio={atr_ratio:.2f})")

        # ── Build V4 features ──
        features = self._feature_engine.build_live_features_v4(
            candles_m5=candles,
            candles_m15=None,
            candles_h1=None,
            candles_h4=None,
            candles_d1=None,
        )

        if features is None:
            return self.create_hold(symbol, "Cannot build V4 feature sequence")

        # ── AI Prediction (3-class) ──
        pred = model.predict(features.squeeze(0))
        p_buy = pred["buy"]
        p_hold = pred["hold"]
        p_sell = pred["sell"]
        action = pred["action"]
        confidence = pred["confidence"]

        current_close = float(close.iloc[-1])
        params = SYMBOL_PARAMS.get(symbol, DEFAULT_PARAMS)
        sl_mult = params["sl_mult"]
        tp_mult = params["tp_mult"]

        # ── Build Decision ──
        if action == "BUY" and p_buy > BUY_THRESHOLD and p_hold < HOLD_MAX:
            sl_distance = current_atr * sl_mult
            tp_distance = current_atr * tp_mult
            sl = current_close - sl_distance
            tp = current_close + tp_distance
            rr = tp_distance / sl_distance if sl_distance > 0 else 0

            return Decision(
                symbol=symbol,
                action=Action.BUY,
                confidence=round(confidence, 3),
                reason=(
                    f"V4 BUY: P(buy)={p_buy:.3f} P(hold)={p_hold:.3f} P(sell)={p_sell:.3f} "
                    f"ATR_ratio={atr_ratio:.2f} regime={regime.value}"
                ),
                stop_loss=round(sl, profile.digits if hasattr(profile, 'digits') else 2),
                take_profit=round(tp, profile.digits if hasattr(profile, 'digits') else 2),
                risk_reward_ratio=round(rr, 2),
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["ai_deep_v4", "transformer_gru", f"pbuy_{p_buy:.2f}"],
                debug={
                    "p_buy": round(p_buy, 4),
                    "p_hold": round(p_hold, 4),
                    "p_sell": round(p_sell, 4),
                    "atr": round(current_atr, 4),
                    "atr_ratio": round(atr_ratio, 3),
                    "sl_distance": round(sl_distance, 4),
                    "tp_distance": round(tp_distance, 4),
                    "regime": regime.value,
                },
            )

        elif action == "SELL" and p_sell > SELL_THRESHOLD and p_hold < HOLD_MAX:
            sl_distance = current_atr * sl_mult
            tp_distance = current_atr * tp_mult
            sl = current_close + sl_distance
            tp = current_close - tp_distance
            rr = tp_distance / sl_distance if sl_distance > 0 else 0

            return Decision(
                symbol=symbol,
                action=Action.SELL,
                confidence=round(confidence, 3),
                reason=(
                    f"V4 SELL: P(buy)={p_buy:.3f} P(hold)={p_hold:.3f} P(sell)={p_sell:.3f} "
                    f"ATR_ratio={atr_ratio:.2f} regime={regime.value}"
                ),
                stop_loss=round(sl, profile.digits if hasattr(profile, 'digits') else 2),
                take_profit=round(tp, profile.digits if hasattr(profile, 'digits') else 2),
                risk_reward_ratio=round(rr, 2),
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["ai_deep_v4", "transformer_gru", f"psell_{p_sell:.2f}"],
                debug={
                    "p_buy": round(p_buy, 4),
                    "p_hold": round(p_hold, 4),
                    "p_sell": round(p_sell, 4),
                    "atr": round(current_atr, 4),
                    "atr_ratio": round(atr_ratio, 3),
                    "sl_distance": round(sl_distance, 4),
                    "tp_distance": round(tp_distance, 4),
                    "regime": regime.value,
                },
            )

        else:
            return self.create_hold(
                symbol,
                f"V4 HOLD: P(buy)={p_buy:.3f} P(hold)={p_hold:.3f} P(sell)={p_sell:.3f}"
            )

    def _fallback_analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType,
        model,
    ) -> Decision:
        """Fallback to V3 GRU model."""
        symbol = profile.symbol

        if self._feature_engine is None or candles is None or len(candles) < 120:
            return self.create_hold(symbol, "V4 fallback: insufficient data")

        features = self._feature_engine.build_live_features(
            candles_m5=candles,
        )
        if features is None:
            return self.create_hold(symbol, "V4 fallback: cannot build features")

        prob = model.predict(features.squeeze(0))
        close = candles["close"]
        current_close = float(close.iloc[-1])

        atr_raw = ta.atr(candles["high"], candles["low"], close, length=14)
        current_atr = atr_raw.iloc[-1] if atr_raw is not None else 0

        params = SYMBOL_PARAMS.get(symbol, DEFAULT_PARAMS)

        if prob > 0.60:
            sl_distance = current_atr * params["sl_mult"]
            tp_distance = current_atr * params["tp_mult"]
            return Decision(
                symbol=symbol,
                action=Action.BUY,
                confidence=round(min((prob - 0.5) * 2, 1.0), 3),
                reason=f"V4→V3 fallback BUY: prob={prob:.3f}",
                stop_loss=round(current_close - sl_distance, getattr(profile, 'digits', 2)),
                take_profit=round(current_close + tp_distance, getattr(profile, 'digits', 2)),
                risk_reward_ratio=round(tp_distance / max(sl_distance, 0.0001), 2),
                strategy_name=f"{self.name}_fallback_v3",
                timeframe=self.timeframe,
                tags=["ai_deep_v4", "fallback_v3"],
                debug={"v3_prob": prob},
            )
        elif prob < 0.40:
            sl_distance = current_atr * params["sl_mult"]
            tp_distance = current_atr * params["tp_mult"]
            return Decision(
                symbol=symbol,
                action=Action.SELL,
                confidence=round(min((0.5 - prob) * 2, 1.0), 3),
                reason=f"V4→V3 fallback SELL: prob={prob:.3f}",
                stop_loss=round(current_close + sl_distance, getattr(profile, 'digits', 2)),
                take_profit=round(current_close - tp_distance, getattr(profile, 'digits', 2)),
                risk_reward_ratio=round(tp_distance / max(sl_distance, 0.0001), 2),
                strategy_name=f"{self.name}_fallback_v3",
                timeframe=self.timeframe,
                tags=["ai_deep_v4", "fallback_v3"],
                debug={"v3_prob": prob},
            )
        else:
            return self.create_hold(symbol, f"V4→V3 fallback HOLD: prob={prob:.3f}")
