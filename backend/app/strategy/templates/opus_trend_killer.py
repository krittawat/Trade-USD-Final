"""
OPUS Trend Killer — Model B (Secondary Strategy).

Fast-follow trend entry after BOS with pullback entry.
เข้าเทรดตามเทรนด์หลัง Break of Structure (BOS) โดยรอ pullback ก่อนเข้า.

Activation: Only in STRONG_TREND or clean BOS with ATR expansion
Entry: BOS confirmed + Pullback 38-50% Fib + continuation candle + volume alignment
SL: Below/above swing + ATR buffer
TP: Partial at 1R, runner to 2-4R based on regime

Tags: ["OPUS", "TREND_KILLER", "MODEL_B"]
"""

import numpy as np
import pandas as pd
from typing import Optional

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy
from app.brain.opus_regime import classify_opus_regime, _detect_swings

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Parameters
# ═══════════════════════════════════════════════════════════════════
MIN_CONFIDENCE = 0.65
FIB_PULLBACK_MIN = 0.38       # Minimum pullback depth (38.2% Fibonacci)
FIB_PULLBACK_MAX = 0.55       # Maximum pullback depth (55% — slightly beyond 50%)
CONTINUATION_BODY_ATR = 0.5   # Continuation candle body >= 50% ATR
VOLUME_CONFIRM_MULT = 1.0     # Volume must be >= average (1.0x)
ATR_SL_BUFFER = 0.4           # ATR fraction added below swing for SL

# TP configuration
TP_PARTIAL_1_R = 1.0
TP_PARTIAL_1_PCT = 0.40       # Close 40% at 1R
TP_RUNNER_MIN_R = 2.0         # Runner target minimum
TP_RUNNER_MAX_R = 4.0         # Runner target maximum in strong trend


class OpusTrendKillerStrategy(BaseStrategy):
    """
    Model B — Trend Killer.

    Secondary OPUS strategy: trades BOS pullbacks in strong trends.
    Only activates when structure is clear and ATR is expanding.

    Suitable regimes: STRONG_TREND, VOL_EXPANSION, BREAKOUT
    """

    name = "opus_trend_killer"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.STRONG_TREND,
        RegimeType.VOL_EXPANSION,
        RegimeType.BREAKOUT,
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
    ]

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        pressure: dict | None = None,
    ) -> Decision:
        """
        Analyze for BOS + pullback entry.

        Flow:
            1. Run OPUS regime classification
            2. Require STRONG_TREND or VOL_EXPANSION + BOS
            3. Detect swing structure
            4. Measure pullback depth (38-50% Fibonacci)
            5. Confirm continuation candle + volume
            6. Compute SL/TP
            7. Return Decision
        """
        symbol = profile.symbol

        # ─── Guard ───
        if candles is None or len(candles) < 80:
            return self.create_hold(symbol, "Insufficient candles for OPUS Trend Killer")

        high = candles["high"].values.astype(float)
        low = candles["low"].values.astype(float)
        close = candles["close"].values.astype(float)
        open_ = candles["open"].values.astype(float)
        n = len(close)
        current_price = float(close[-1])

        volume = (candles["tick_volume"].values.astype(float)
                  if "tick_volume" in candles.columns
                  else np.ones(n))

        # ─── Step 1: ATR ───
        atr = self._compute_atr(high, low, close)
        if atr <= 0:
            return self.create_hold(symbol, "ATR is zero")

        # ─── Step 2: OPUS Regime ───
        opus = classify_opus_regime(candles)

        # Only activate in strong trend / vol expansion
        allowed_regimes = {
            RegimeType.STRONG_TREND, RegimeType.VOL_EXPANSION,
            RegimeType.BREAKOUT, RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN,
        }
        if opus.regime not in allowed_regimes:
            return self.create_hold(
                symbol,
                f"Trend Killer requires trend regime, got {opus.regime.value}"
            )

        if opus.confidence < MIN_CONFIDENCE:
            return self.create_hold(
                symbol,
                f"OPUS confidence {opus.confidence:.2f} < {MIN_CONFIDENCE}"
            )

        # ─── Step 3: BOS Check ───
        if not opus.bos_detected:
            return self.create_hold(symbol, "No BOS detected — Trend Killer needs BOS")

        structure = opus.structure  # BULLISH or BEARISH

        # ─── Step 4: Swing Detection + Pullback Depth ───
        swings = _detect_swings(high, low, lookback=30)
        swing_highs = swings["swing_highs"]
        swing_lows = swings["swing_lows"]

        if structure == "BULLISH":
            if len(swing_highs) < 2 or len(swing_lows) < 1:
                return self.create_hold(symbol, "Not enough swings for bullish pullback")

            # Impulse = distance from last swing low to last swing high
            impulse_low = swing_lows[-1][1]
            impulse_high = swing_highs[-1][1]
            impulse_range = impulse_high - impulse_low

            if impulse_range <= 0:
                return self.create_hold(symbol, "Invalid impulse range")

            # Pullback depth = how much price has retraced from the high
            pullback = (impulse_high - current_price) / impulse_range

            if pullback < FIB_PULLBACK_MIN:
                return self.create_hold(
                    symbol,
                    f"Pullback {pullback:.2f} < {FIB_PULLBACK_MIN} (too shallow)"
                )
            if pullback > FIB_PULLBACK_MAX:
                return self.create_hold(
                    symbol,
                    f"Pullback {pullback:.2f} > {FIB_PULLBACK_MAX} (too deep — structure broken)"
                )

            action = Action.BUY
            sl_anchor = impulse_low  # SL below the swing low

        elif structure == "BEARISH":
            if len(swing_lows) < 2 or len(swing_highs) < 1:
                return self.create_hold(symbol, "Not enough swings for bearish pullback")

            impulse_high = swing_highs[-1][1]
            impulse_low = swing_lows[-1][1]
            impulse_range = impulse_high - impulse_low

            if impulse_range <= 0:
                return self.create_hold(symbol, "Invalid impulse range")

            pullback = (current_price - impulse_low) / impulse_range

            if pullback < FIB_PULLBACK_MIN:
                return self.create_hold(
                    symbol,
                    f"Pullback {pullback:.2f} < {FIB_PULLBACK_MIN}"
                )
            if pullback > FIB_PULLBACK_MAX:
                return self.create_hold(
                    symbol,
                    f"Pullback {pullback:.2f} > {FIB_PULLBACK_MAX}"
                )

            action = Action.SELL
            sl_anchor = impulse_high  # SL above the swing high

        else:
            return self.create_hold(symbol, f"Unclear structure: {structure}")

        # ─── Step 5: Continuation Candle Check ───
        last_body = abs(close[-1] - open_[-1])
        if last_body < atr * CONTINUATION_BODY_ATR:
            return self.create_hold(
                symbol,
                f"Weak continuation candle: body={last_body:.5f} < {atr * CONTINUATION_BODY_ATR:.5f}"
            )

        # Direction alignment
        if action == Action.BUY and close[-1] <= open_[-1]:
            return self.create_hold(symbol, "Last candle bearish — no BUY continuation")
        if action == Action.SELL and close[-1] >= open_[-1]:
            return self.create_hold(symbol, "Last candle bullish — no SELL continuation")

        # ─── Step 6: Volume Confirmation ───
        vol_avg = float(np.mean(volume[-20:])) if len(volume) >= 20 else float(np.mean(volume))
        vol_current = float(volume[-1]) if len(volume) > 0 else 0
        if vol_avg > 0 and vol_current < vol_avg * VOLUME_CONFIRM_MULT:
            return self.create_hold(
                symbol,
                f"Volume {vol_current:.0f} < avg {vol_avg:.0f} — no confirmation"
            )

        # ─── Step 7: Compute SL / TP ───
        if action == Action.BUY:
            stop_loss = sl_anchor - atr * ATR_SL_BUFFER
        else:
            stop_loss = sl_anchor + atr * ATR_SL_BUFFER

        sl_distance = abs(current_price - stop_loss)
        if sl_distance <= 0:
            return self.create_hold(symbol, "SL distance is zero")

        # TP based on regime strength
        if opus.regime == RegimeType.STRONG_TREND and opus.confidence >= 0.80:
            runner_r = TP_RUNNER_MAX_R  # 4R in strong trend
        elif opus.aggressive_mode:
            runner_r = 3.0
        else:
            runner_r = TP_RUNNER_MIN_R

        tp_distance = sl_distance * runner_r
        if action == Action.BUY:
            take_profit = current_price + tp_distance
        else:
            take_profit = current_price - tp_distance

        # ─── Step 8: Confidence ───
        combined_confidence = opus.confidence * 0.6 + 0.2  # Base from regime
        # Boost for clean pullback
        pullback_quality = 1.0 - abs(pullback - 0.44) / 0.2  # Best at 44%
        combined_confidence += max(0, pullback_quality) * 0.15
        # Boost for volume
        if vol_current > vol_avg * 1.5:
            combined_confidence += 0.05
        combined_confidence = min(1.0, combined_confidence)

        # ─── Step 9: Build Decision ───
        trailing_config = {
            "type": "opus_trend",
            "partial_1_r": TP_PARTIAL_1_R,
            "partial_1_pct": TP_PARTIAL_1_PCT,
            "runner_target_r": runner_r,
            "atr_trail_mult": 1.8,
            "structure_based_exit": True,
        }

        reason_parts = [
            f"OPUS Trend Killer — {action.value}",
            f"BOS + Pullback {pullback:.1%}",
            f"Regime={opus.regime.value} (conf={opus.confidence:.2f})",
            f"Target={runner_r:.1f}R",
        ]
        if opus.aggressive_mode:
            reason_parts.append("🔥 AGGRESSIVE")

        return Decision(
            symbol=symbol,
            action=action,
            confidence=round(combined_confidence, 3),
            reason=" | ".join(reason_parts),
            stop_loss=round(stop_loss, profile.digits),
            take_profit=round(take_profit, profile.digits),
            risk_pct=0.015,
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["OPUS", "TREND_KILLER", "MODEL_B"],
            debug={
                "opus_regime": opus.regime.value,
                "opus_confidence": opus.confidence,
                "opus_structure": structure,
                "opus_bos": opus.bos_detected,
                "pullback_depth": round(pullback, 3),
                "impulse_range": round(impulse_range, 5),
                "continuation_body": round(last_body, 5),
                "volume_ratio": round(vol_current / max(vol_avg, 1), 2),
                "atr": round(atr, 5),
                "sl_distance": round(sl_distance, 5),
                "runner_r": runner_r,
                "trailing_config": trailing_config,
            },
        )

    # ─── Helper ───

    def _compute_atr(self, high: np.ndarray, low: np.ndarray,
                     close: np.ndarray, period: int = 14) -> float:
        """Quick ATR computation."""
        if len(close) < period + 1:
            return 0.0
        tr1 = high[1:] - low[1:]
        tr2 = np.abs(high[1:] - close[:-1])
        tr3 = np.abs(low[1:] - close[:-1])
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        return float(np.mean(tr[-period:]))
