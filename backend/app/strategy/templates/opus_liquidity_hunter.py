"""
OPUS Liquidity Hunter — Model A (Primary Strategy).

Aggressive smart-money entry: sweep + displacement + re-acceptance.
ล่าสภาพคล่อง (Liquidity) ที่ถูกกวาดโดย Smart Money แล้วเข้าตามทิศทางจริง.

Timeframe: M5 execution, M15/H1 bias confirmation
Entry: Sweep + Displacement + Re-acceptance + Wick anomaly
SL: Above/below sweep wick + ATR buffer
TP: 50% at 1R, 25% at 1.8R, 25% ATR trail (runner)
Min RR: 1:2.5 in vol expansion, 1:2 otherwise

Tags: ["OPUS", "LIQUIDITY_HUNTER", "MODEL_A"]
"""

import numpy as np
import pandas as pd
from typing import Optional

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy
from app.brain.opus_regime import classify_opus_regime, OpusRegimeResult
from app.brain.liquidity_hunter import LiquidityHunter, LiquiditySignal

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Parameters — HARDENED 2026-03-08 for PF≥1.20, DD<20%, WR>53%
# ═══════════════════════════════════════════════════════════════════
MIN_CONFIDENCE = 0.70          # Hardened: 0.65→0.70
MIN_LIQUIDITY_CONF = 0.65      # Hardened: 0.60→0.65
MIN_RR = 2.5                   # Hardened: 2.0→2.5
MIN_RR_EXPANSION = 3.0         # Hardened: 2.5→3.0
SPREAD_MAX_MULT = 1.5
WICK_ANOMALY_MIN = 0.40        # Hardened: 0.35→0.40

# Partial TP configuration (passed via trailing_config on Decision)
TP_PARTIAL_1_PCT = 0.50       # Close 50% at 1R
TP_PARTIAL_1_R = 1.0
TP_PARTIAL_2_PCT = 0.25       # Close 25% at 1.8R
TP_PARTIAL_2_R = 1.8
TP_RUNNER_PCT = 0.25          # Keep 25% as runner

ATR_SL_BUFFER = 0.3           # ATR fraction added to SL


class OpusLiquidityHunterStrategy(BaseStrategy):
    """
    Model A — Liquidity Hunter.

    Primary OPUS strategy: hunts liquidity sweeps and trades
    the reversal with tight invalidation and aggressive partial TP.

    Suitable regimes: VOL_EXPANSION, HIGH_VOLATILITY, DISTRIBUTION,
                      ACCUMULATION, LIQUIDITY_SWEEP
    """

    name = "opus_liquidity_hunter"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.VOL_EXPANSION,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.DISTRIBUTION,
        RegimeType.ACCUMULATION,
        RegimeType.LIQUIDITY_SWEEP,
    ]

    def __init__(self):
        self._hunter = LiquidityHunter()

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        pressure: dict | None = None,
    ) -> Decision:
        """
        Analyze market for liquidity sweep patterns.

        Flow:
            1. Run OPUS regime classification
            2. Check regime confidence >= 0.65
            3. Scan for liquidity sweep
            4. Verify displacement + re-acceptance
            5. Check spread sanity
            6. Compute SL/TP with minimum RR
            7. Return Decision
        """
        symbol = profile.symbol

        # ─── Guard: Minimum candles ───
        if candles is None or len(candles) < 80:
            return self.create_hold(symbol, "Insufficient candles for OPUS")

        high = candles["high"].values.astype(float)
        low = candles["low"].values.astype(float)
        close = candles["close"].values.astype(float)
        open_ = candles["open"].values.astype(float)
        n = len(close)
        current_price = float(close[-1])

        # ─── Step 1: ATR ───
        atr = self._compute_atr(high, low, close)
        if atr <= 0:
            return self.create_hold(symbol, "ATR is zero")

        # ─── Step 2: OPUS Regime Check ───
        opus = classify_opus_regime(candles)
        if opus.confidence < MIN_CONFIDENCE:
            return self.create_hold(
                symbol,
                f"OPUS confidence {opus.confidence:.2f} < {MIN_CONFIDENCE} "
                f"(regime={opus.regime.value})"
            )

        if not opus.actionable:
            return self.create_hold(
                symbol,
                f"OPUS regime {opus.regime.value} not actionable"
            )

        # ─── Step 3: Liquidity Scan ───
        liq_signal = self._hunter.scan(candles, atr=atr)

        if not liq_signal.sweep_detected:
            return self.create_hold(symbol, "No liquidity sweep detected")

        if liq_signal.confidence < MIN_LIQUIDITY_CONF:
            return self.create_hold(
                symbol,
                f"Liquidity confidence {liq_signal.confidence:.2f} < {MIN_LIQUIDITY_CONF}"
            )

        # ─── Step 4: Direction Alignment ───
        action_str = liq_signal.sweep_direction
        if action_str not in ("BUY", "SELL"):
            return self.create_hold(symbol, "No clear sweep direction")

        action = Action.BUY if action_str == "BUY" else Action.SELL

        # Check regime agrees with direction
        if opus.structure == "BULLISH" and action == Action.SELL:
            # Selling against bullish structure is risky — reduce confidence
            if opus.confidence < 0.75:
                return self.create_hold(
                    symbol,
                    f"SELL against BULLISH structure with low confidence {opus.confidence:.2f}"
                )
        elif opus.structure == "BEARISH" and action == Action.BUY:
            if opus.confidence < 0.75:
                return self.create_hold(
                    symbol,
                    f"BUY against BEARISH structure with low confidence {opus.confidence:.2f}"
                )

        # ─── Step 5: Spread Check ───
        spread = profile.spread_avg
        max_spread = spread * SPREAD_MAX_MULT
        if profile.spread_avg > 0 and profile.spread_avg > max_spread:
            return self.create_hold(
                symbol,
                f"Spread {profile.spread_avg:.1f} > max {max_spread:.1f}"
            )

        # ─── Step 6: Wick Anomaly Check ───
        if opus.wick_anomaly < WICK_ANOMALY_MIN:
            return self.create_hold(
                symbol,
                f"Wick anomaly {opus.wick_anomaly:.2f} < {WICK_ANOMALY_MIN}"
            )

        # ─── Step 7: Compute SL / TP ───
        if liq_signal.invalidation_price > 0:
            stop_loss = liq_signal.invalidation_price
        else:
            if action == Action.BUY:
                stop_loss = float(np.min(low[-5:])) - atr * ATR_SL_BUFFER
            else:
                stop_loss = float(np.max(high[-5:])) + atr * ATR_SL_BUFFER

        sl_distance = abs(current_price - stop_loss)
        if sl_distance <= 0:
            return self.create_hold(symbol, "SL distance is zero")

        # Minimum RR
        min_rr = MIN_RR_EXPANSION if opus.aggressive_mode else MIN_RR
        take_profit_distance = sl_distance * min_rr

        if action == Action.BUY:
            take_profit = current_price + take_profit_distance
        else:
            take_profit = current_price - take_profit_distance

        # ─── Step 8: Confidence Calculation ───
        # Combined confidence from regime + liquidity
        combined_confidence = (opus.confidence * 0.5 + liq_signal.confidence * 0.5)
        # Boost for displacement strength
        combined_confidence += liq_signal.displacement_strength * 0.15
        # Boost for re-acceptance
        if liq_signal.re_acceptance:
            combined_confidence += 0.05
        combined_confidence = min(1.0, combined_confidence)

        # ─── Step 9: Build Decision ───
        reason_parts = [
            f"OPUS Liquidity Hunter — {action_str}",
            f"Regime={opus.regime.value} (conf={opus.confidence:.2f})",
            f"Sweep conf={liq_signal.confidence:.2f}",
            f"Displacement={liq_signal.displacement_strength:.2f}",
            f"Structure={opus.structure}",
        ]
        if opus.aggressive_mode:
            reason_parts.append("🔥 AGGRESSIVE MODE")
        if liq_signal.trap_type != "NONE":
            reason_parts.append(f"Trap={liq_signal.trap_type}")

        # Trailing config for partial TP management
        trailing_config = {
            "type": "opus_partial",
            "partial_1_r": TP_PARTIAL_1_R,
            "partial_1_pct": TP_PARTIAL_1_PCT,
            "partial_2_r": TP_PARTIAL_2_R,
            "partial_2_pct": TP_PARTIAL_2_PCT,
            "runner_pct": TP_RUNNER_PCT,
            "atr_trail_mult": 1.5,
        }

        return Decision(
            symbol=symbol,
            action=action,
            confidence=round(combined_confidence, 3),
            reason=" | ".join(reason_parts),
            stop_loss=round(stop_loss, profile.digits),
            take_profit=round(take_profit, profile.digits),
            risk_pct=0.015,  # 1.5% default, sized by risk engine
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["OPUS", "LIQUIDITY_HUNTER", "MODEL_A"],
            debug={
                "opus_regime": opus.regime.value,
                "opus_confidence": opus.confidence,
                "opus_aggressive": opus.aggressive_mode,
                "opus_structure": opus.structure,
                "opus_bos": opus.bos_detected,
                "liq_confidence": liq_signal.confidence,
                "liq_displacement": liq_signal.displacement_strength,
                "liq_re_acceptance": liq_signal.re_acceptance,
                "liq_trap": liq_signal.trap_type,
                "liq_zones_count": len(liq_signal.liquidity_zones),
                "atr": round(atr, 5),
                "sl_distance": round(sl_distance, 5),
                "rr_ratio": round(take_profit_distance / sl_distance, 2),
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
