"""
Google Gravity Strategy — กลยุทธ์หลักสำหรับ BTCUSD (Production Grade).

สถาปัตยกรรม:
    GoogleGravityStrategy ทำหน้าที่เป็น "Dispatcher" เลือกกลยุทธ์ย่อยตาม Regime:

    ┌─────────────────────────────────────────────────────┐
    │                 Google Gravity                       │
    │                                                     │
    │  ① Ghost Protocol  → ตรวจ Liquidity Sweep ก่อนเสมอ  │
    │  ② Trend Pullback  → ถ้า Regime = TRENDING          │
    │  ③ Ranging Sniper  → ถ้า Regime = RANGING/FAKEOUT   │
    └─────────────────────────────────────────────────────┘

กฎหลัก:
    - Ghost Protocol มีลำดับสูงสุด (ตรวจก่อนทุกรอบ)
    - ทุก Decision ติดแท็ก "google_gravity" + ชื่อกลยุทธ์ย่อย
    - Strategy Name ใช้แบบ Composite เช่น "google_gravity:ghost_protocol"
    - Gate จัดการ Time Window / Daily Limit (Strategy ไม่ซ้ำซ้อน)
"""

import pandas as pd
import pandas_ta as pta
from typing import Optional

from app.core.logging import get_logger
from app.domain.models import Decision, SymbolProfile, RegimeContext
from app.domain.enums import Action, RegimeType
from app.strategy.base import BaseStrategy

# ── กลยุทธ์ย่อย ──
from app.strategy.templates.trend_pullback import TrendPullbackStrategy
from app.strategy.templates.ranging_sniper import RangingSniperStrategy
from app.strategy.templates.ghost_protocol import GhostProtocol
from app.strategy.templates.ghost_oracle_tuning import get_tuning

logger = get_logger(__name__)

# Regime ที่แต่ละกลยุทธ์ย่อยรับผิดชอบ
_TREND_REGIMES = frozenset([RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN])
_RANGE_REGIMES = frozenset([RegimeType.RANGING, RegimeType.LOW_VOLATILITY, RegimeType.FAKEOUT])

# ค่า ATR เริ่มต้นสำหรับ Ghost Protocol SL/TP
_GHOST_ATR_LEN = 14
_GHOST_MIN_CONFIDENCE = 70  # จาก 0-100


class GoogleGravityStrategy(BaseStrategy):
    """กลยุทธ์ Gravity — Dispatcher ที่เลือกกลยุทธ์ย่อยตามสภาวะตลาด."""

    name = "google_gravity"
    timeframe = "M5"  # ลอจิกหลักบน M5, ยืนยันเทรนด์ด้วย H1

    def __init__(self):
        super().__init__()
        self.trend_strategy = TrendPullbackStrategy()
        self.range_strategy = RangingSniperStrategy()
        self.ghost_strategy = GhostProtocol()

    # ────────────────────────────────────────────────────
    # analyze() — จุดเข้าหลัก
    # ────────────────────────────────────────────────────

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        session: str = "CLOSED",
        h1_candles: pd.DataFrame = None,
        regime_context: RegimeContext | None = None,
        **kwargs,
    ) -> Decision:

        # ── Guard: ป้องกัน argument สลับกัน ──
        if not isinstance(profile, SymbolProfile):
            if isinstance(candles, SymbolProfile):
                logger.warning("gravity_args_swapped", extra={
                    "received_type": type(profile).__name__,
                })
                candles, profile = profile, candles
            else:
                return self.create_hold("UNKNOWN", "profile ไม่ใช่ SymbolProfile")

        symbol = profile.symbol
        current_regime = regime_context.regime if regime_context else regime

        # ── ลำดับ 1: Ghost Protocol (Liquidity Sweep → กลับตัว) ──
        ghost = self._check_ghost_protocol(candles, profile)
        if ghost and ghost.action != Action.HOLD:
            return self._tag_decision(ghost, "ghost_protocol")

        # ── ลำดับ 2: Dispatch ตาม Regime ──
        decision: Decision | None = None

        if current_regime in _TREND_REGIMES:
            decision = self.trend_strategy.analyze(
                candles=candles,
                profile=profile,
                regime=current_regime,
                session=session,
                h1_candles=h1_candles,
                **kwargs,
            )
        elif current_regime in _RANGE_REGIMES:
            decision = self.range_strategy.analyze(
                candles=candles,
                profile=profile,
                regime=current_regime,
                **kwargs,
            )

        # ── Fallback: HOLD พร้อมเหตุผล ──
        if decision is None or decision.action == Action.HOLD:
            decision = self.create_hold(
                symbol,
                f"Gravity HOLD | regime={current_regime.value} session={session}",
            )

        return self._tag_decision(decision, decision.strategy_name or "hold")

    # ────────────────────────────────────────────────────
    # Ghost Protocol — ตรวจจับ Liquidity Sweep
    # ────────────────────────────────────────────────────

    def _check_ghost_protocol(
        self, candles: pd.DataFrame, profile: SymbolProfile
    ) -> Optional[Decision]:
        """
        เรียก GhostProtocol แล้วแปลงผลลัพธ์ (StrategyDecision)
        ให้เป็น Decision มาตรฐานพร้อม ATR-based SL/TP.
        """
        try:
            # ── ความปลอดภัย: ตรวจสอบว่ามี tick_volume (ChartIntelligence จำเป็นต้องใช้) ──
            _candles = candles
            if "tick_volume" not in candles.columns:
                _candles = candles.copy()
                _candles["tick_volume"] = _candles.get("volume", 0)

            raw = self.ghost_strategy.analyze(_candles, symbol=profile.symbol)

            # ── แปลงผลลัพธ์เป็น dict ──
            if hasattr(raw, "model_dump"):
                result = raw.model_dump()
            elif hasattr(raw, "__dict__"):
                result = vars(raw)
            elif isinstance(raw, dict):
                result = raw
            else:
                return None

            signal = str(result.get("signal", "")).upper()
            confidence = float(result.get("confidence", 0))

            if signal not in ("BUY", "SELL") or confidence < _GHOST_MIN_CONFIDENCE:
                return None

            # ─── คำนวณ SL/TP ด้วย Per-Symbol Tuning ──
            tuning = get_tuning(profile.symbol)
            atr_series = pta.atr(
                candles["high"], candles["low"], candles["close"],
                length=_GHOST_ATR_LEN,
            )
            if atr_series is None or atr_series.iloc[-1] != atr_series.iloc[-1]:
                return None  # ATR เป็น NaN → ข้อมูลไม่เพียงพอ

            atr = float(atr_series.iloc[-1])
            price = float(candles["close"].iloc[-1])
            sl_dist = tuning["atr_sl_mult"] * atr
            tp_dist = tuning["atr_tp_mult"] * atr

            action = Action.BUY if signal == "BUY" else Action.SELL
            if action == Action.BUY:
                sl, tp = price - sl_dist, price + tp_dist
            else:
                sl, tp = price + sl_dist, price - tp_dist

            # ─── Structure-Aware SL (PRIMARY) ───
            # SL = swing high/low + 0.5 ATR buffer. Pure ATR as max cap.
            lookback = tuning.get("lookback", 15)
            atr_sl_cap = sl_dist  # Pure ATR SL as max width cap

            if action == Action.BUY:
                swing_low = float(candles["low"].iloc[-lookback:].min())
                sl = swing_low - (0.5 * atr)
                if abs(price - sl) > atr_sl_cap:
                    sl = price - atr_sl_cap
            else:  # SELL
                swing_high = float(candles["high"].iloc[-lookback:].max())
                sl = swing_high + (0.5 * atr)
                if abs(sl - price) > atr_sl_cap:
                    sl = price + atr_sl_cap

            # Recalculate after structure SL
            sl_dist = abs(price - sl)
            tp_dist = abs(tp - price)
            rr = tp_dist / sl_dist if sl_dist > 0 else 0

            # ─── R:R Enforcement ───
            min_rr = tuning.get("min_rr", 1.0)
            if rr < min_rr:
                logger.debug("ghost_rr_rejected", extra={
                    "symbol": profile.symbol, "rr": round(rr, 2),
                    "min_rr": min_rr, "sl_dist": round(sl_dist, 2),
                })
                return None

            # ─── สร้าง reasons string อย่างปลอดภัย ──
            reasons = result.get("reasons", result.get("reason", "Ghost signal"))
            if isinstance(reasons, list):
                reason_str = " | ".join(str(r) for r in reasons)
            else:
                reason_str = str(reasons)

            return Decision(
                symbol=profile.symbol,
                action=action,
                confidence=confidence / 100.0,
                reason=f"Ghost Protocol: {reason_str}",
                stop_loss=round(sl, profile.digits),
                take_profit=round(tp, profile.digits),
                risk_reward_ratio=round(rr, 2),
                strategy_name="ghost_protocol",
                tags=["ghost", "reversal", "liquidity_sweep"],
                timeframe="M5",
            )

        except Exception as e:
            logger.error("ghost_protocol_error", extra={
                "symbol": profile.symbol,
                "error": str(e),
            })
            return None

    # ────────────────────────────────────────────────────
    # เครื่องมือติดแท็ก
    # ────────────────────────────────────────────────────

    @staticmethod
    def _tag_decision(decision: Decision, sub_strategy: str) -> Decision:
        """
        ติดแท็ก Google Gravity ให้ทุก Decision.

        เพิ่ม:
            - tags: ["google_gravity", "gravity_sub:<sub>"]
            - strategy_name: "google_gravity:<sub>"
        """
        decision.tags.append("google_gravity")
        decision.tags.append(f"gravity_sub:{sub_strategy}")
        decision.strategy_name = f"google_gravity:{sub_strategy}"
        return decision
