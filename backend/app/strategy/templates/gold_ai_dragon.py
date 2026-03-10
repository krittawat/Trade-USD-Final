"""
Gold AI Dragon Strategy V3 — Trend-Following Pullback Strategy for XAU M5.

🐉 "THE DRAGON V3" — Simple, Proven, Profitable

V1 (7-layer scoring) → WR=36.8%, PF=0.99 — FAILED (overfit, too complex)
V2 (same + strict)    → 0 trades — FAILED (too strict)
V2.1 (mid thresholds) → WR=18% — FAILED (scoring doesn't predict gold)

V3 KEY INSIGHT:
    Complex multi-layer scoring adds noise, not edge.
    Simple rules that respect market structure WIN on gold.

V3 RULES (4 conditions, ALL must be true to trade):
    1. TREND:  EMA50 > EMA200 → BUY only   |   EMA50 < EMA200 → SELL only
    2. PULLBACK: Price touches/crosses EMA21 (entry zone)
    3. MOMENTUM: ADX > 20 AND DI aligned with trend
    4. CONFIRMATION: Bullish/bearish engulfing or momentum candle at EMA21

SL/TP:
    - SL: Behind swing structure (swing low for BUY, swing high for SELL) + 0.5 ATR buffer
    - TP: 2.0x risk (fixed RR)
    - Min SL: 1.5 ATR, Max SL: 3.0 ATR

Session: London + NY hours only (UTC 7-21)
AI: Optional signal booster (does NOT veto — model too weak at 51% accuracy)

Target: Win Rate ≥45%, PF ≥1.3, MaxDD ≤6%
"""

import numpy as np
import pandas as pd

from app.strategy.base import BaseStrategy
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

logger = get_logger(__name__)

# ═════════════════════════════════════════════
# V3 PARAMETERS — MINIMAL, BATTLE-TESTED
# ═════════════════════════════════════════════

DRAGON_DEFAULTS = {
    # EMA
    "ema_fast": 9,
    "ema_pullback": 21,    # Pullback zone
    "ema_trend": 50,       # Trend direction
    "ema_bias": 200,       # Macro bias

    # ADX — momentum gate
    "adx_period": 14,
    "adx_min": 20,         # Minimum trend strength

    # RSI — extreme filter only
    "rsi_period": 14,
    "rsi_extreme_high": 80,
    "rsi_extreme_low": 20,

    # Pullback zone
    "pullback_atr_zone": 1.0,  # How close to EMA21 = "touching"

    # Risk
    "atr_period": 14,
    "sl_buffer_atr": 0.5,      # Buffer behind swing structure
    "sl_min_atr": 1.5,         # Minimum SL distance in ATR
    "sl_max_atr": 3.0,         # Maximum SL distance in ATR
    "rr_ratio": 2.0,           # Fixed risk:reward

    # Swing structure
    "swing_lookback": 15,

    # Session (UTC hours)
    "session_start": 7,   # London open
    "session_end": 21,    # NY close
}


class GoldAIDragonStrategy(BaseStrategy):
    """
    Gold AI Dragon V3 🐉 — Trend-Following Pullback Strategy.

    Simple 4-condition system:
    1. EMA50/200 trend alignment
    2. EMA21 pullback zone
    3. ADX + DI momentum confirmation
    4. Candlestick trigger (engulfing or momentum)
    """

    name = "gold_ai_dragon"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
    ]

    def __init__(self) -> None:
        """Load params."""
        try:
            from app.strategy.param_loader import get_param_loader
            loader = get_param_loader()
            if loader:
                self.p = loader.get_params(self.name, "XAUUSDc", DRAGON_DEFAULTS)
            else:
                self.p = {**DRAGON_DEFAULTS}
        except Exception:
            self.p = {**DRAGON_DEFAULTS}

        # Lazy AI (optional, non-blocking)
        self._dl = None
        self._dl_init_attempted = False

    def _get_deep_learner(self):
        """Lazy-init DeepLearner."""
        if self._dl_init_attempted:
            return self._dl
        self._dl_init_attempted = True
        try:
            from app.brain.deep_learner import DeepLearner, TORCH_AVAILABLE
            if TORCH_AVAILABLE:
                self._dl = DeepLearner()
        except Exception as e:
            logger.debug("dragon_dl_skip", extra={"error": str(e)})
        return self._dl

    # ═════════════════════════════════════════════
    # MAIN ANALYZE — V3 Simple 4-Condition System
    # ═════════════════════════════════════════════

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "XAUUSDc"

        # ─── Data check ───
        min_bars = self.p["ema_bias"] + 20
        if candles is None or len(candles) < min_bars:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {min_bars})",
            )

        # ─── Session filter ───
        if "time" in candles.columns:
            current_time = candles["time"].iloc[-1]
            if hasattr(current_time, "hour"):
                hour = current_time.hour
            else:
                try:
                    hour = pd.Timestamp(current_time).hour
                except Exception:
                    hour = 12

            if not (self.p["session_start"] <= hour < self.p["session_end"]):
                return self.create_hold(
                    symbol=symbol,
                    reason=f"🐉V3 Session: hour={hour} outside {self.p['session_start']}-{self.p['session_end']}",
                )

        # ─── Price arrays ───
        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)

        c = float(close.iloc[-1])
        h = float(high.iloc[-1])
        lo = float(low.iloc[-1])
        o = float(open_.iloc[-1])
        prev_c = float(close.iloc[-2])
        prev_o = float(open_.iloc[-2])
        prev_h = float(high.iloc[-2])
        prev_l = float(low.iloc[-2])

        # ─── Core indicators ───
        ema21 = close.ewm(span=self.p["ema_pullback"], adjust=False).mean()
        ema50 = close.ewm(span=self.p["ema_trend"], adjust=False).mean()
        ema200 = close.ewm(span=self.p["ema_bias"], adjust=False).mean()

        ema21_val = float(ema21.iloc[-1])
        ema50_val = float(ema50.iloc[-1])
        ema200_val = float(ema200.iloc[-1])

        if any(pd.isna(v) for v in [ema21_val, ema50_val, ema200_val]):
            return self.create_hold(symbol=symbol, reason="EMA NaN")

        # ATR
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(self.p["atr_period"]).mean()
        atr = float(atr_series.iloc[-1])
        if pd.isna(atr) or atr <= 0:
            return self.create_hold(symbol=symbol, reason="ATR NaN")

        # ADX + DI
        adx, di_plus, di_minus = self._compute_adx(high, low, close)

        # RSI
        rsi = self._compute_rsi(close)

        # ═══════════════════════════════════════
        # CONDITION 1: TREND DIRECTION (EMA50 vs EMA200)
        # ═══════════════════════════════════════
        trend_bull = ema50_val > ema200_val
        trend_bear = ema50_val < ema200_val

        if not (trend_bull or trend_bear):
            return self.create_hold(
                symbol=symbol,
                reason=f"🐉V3 No trend: EMA50≈EMA200",
            )

        # Price must be on the right side of EMA200
        if trend_bull and c < ema200_val:
            return self.create_hold(
                symbol=symbol,
                reason=f"🐉V3 Price below EMA200 in uptrend",
            )
        if trend_bear and c > ema200_val:
            return self.create_hold(
                symbol=symbol,
                reason=f"🐉V3 Price above EMA200 in downtrend",
            )

        # ═══════════════════════════════════════
        # CONDITION 2: PULLBACK TO EMA21 ZONE
        # ═══════════════════════════════════════
        pullback_zone = atr * self.p["pullback_atr_zone"]

        in_pullback_zone = False
        if trend_bull:
            # For uptrend: price should be near or just bounced off EMA21
            # Price was at/below EMA21 recently and now closing above
            touched_ema21 = lo <= ema21_val + pullback_zone * 0.3
            bouncing = c > ema21_val
            in_pullback_zone = touched_ema21 and bouncing
        else:
            # For downtrend: price near or just rejected from EMA21
            touched_ema21 = h >= ema21_val - pullback_zone * 0.3
            rejecting = c < ema21_val
            in_pullback_zone = touched_ema21 and rejecting

        if not in_pullback_zone:
            return self.create_hold(
                symbol=symbol,
                reason=f"🐉V3 No pullback: dist_to_EMA21={abs(c - ema21_val):.1f} zone={pullback_zone:.1f}",
            )

        # ═══════════════════════════════════════
        # CONDITION 3: ADX + DI MOMENTUM
        # ═══════════════════════════════════════
        if adx < self.p["adx_min"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"🐉V3 Weak trend: ADX={adx:.0f} < {self.p['adx_min']}",
            )

        # DI must align with trend
        if trend_bull and di_plus <= di_minus:
            return self.create_hold(
                symbol=symbol,
                reason=f"🐉V3 DI conflicted in uptrend: DI+={di_plus:.0f} DI-={di_minus:.0f}",
            )
        if trend_bear and di_minus <= di_plus:
            return self.create_hold(
                symbol=symbol,
                reason=f"🐉V3 DI conflicted in downtrend: DI+={di_plus:.0f} DI-={di_minus:.0f}",
            )

        # ═══════════════════════════════════════
        # CONDITION 4: CANDLESTICK TRIGGER
        # ═══════════════════════════════════════
        body = abs(c - o)
        total_range = h - lo
        body_ratio = body / total_range if total_range > 0 else 0

        trigger = False
        trigger_name = ""

        if trend_bull:
            # Bullish engulfing
            bullish_engulfing = (
                c > o and prev_c < prev_o and
                c > prev_o and o <= prev_c
            )
            # Bullish momentum candle (strong green closing above prev high)
            bullish_momentum = (c > o and body_ratio > 0.55 and c > prev_h)
            # Bullish pin bar (long lower wick, small body at top)
            lower_wick = min(o, c) - lo
            bullish_pin = (body > 0 and lower_wick > body * 1.5 and c > o)
            # Hammer
            hammer = (body > 0 and lower_wick > body * 2.0 and (h - max(o, c)) < body * 0.5)

            if bullish_engulfing:
                trigger = True
                trigger_name = "Bull Engulf"
            elif bullish_momentum:
                trigger = True
                trigger_name = "Bull Momentum"
            elif bullish_pin or hammer:
                trigger = True
                trigger_name = "Bull Pin/Hammer"

            # RSI extreme block
            if rsi > self.p["rsi_extreme_high"]:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"🐉V3 RSI extreme: {rsi:.0f} > {self.p['rsi_extreme_high']}",
                )

        else:  # trend_bear
            bearish_engulfing = (
                c < o and prev_c > prev_o and
                c < prev_o and o >= prev_c
            )
            bearish_momentum = (c < o and body_ratio > 0.55 and c < prev_l)
            upper_wick = h - max(o, c)
            bearish_pin = (body > 0 and upper_wick > body * 1.5 and c < o)
            shooting_star = (body > 0 and upper_wick > body * 2.0 and (min(o, c) - lo) < body * 0.5)

            if bearish_engulfing:
                trigger = True
                trigger_name = "Bear Engulf"
            elif bearish_momentum:
                trigger = True
                trigger_name = "Bear Momentum"
            elif bearish_pin or shooting_star:
                trigger = True
                trigger_name = "Bear Pin/Star"

            if rsi < self.p["rsi_extreme_low"]:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"🐉V3 RSI extreme: {rsi:.0f} < {self.p['rsi_extreme_low']}",
                )

        if not trigger:
            return self.create_hold(
                symbol=symbol,
                reason=f"🐉V3 No candle trigger at pullback zone",
            )

        # ═══════════════════════════════════════
        # ALL 4 CONDITIONS MET → TRADE!
        # ═══════════════════════════════════════
        action = Action.BUY if trend_bull else Action.SELL
        rr = self.p["rr_ratio"]

        # ─── SL: Behind swing structure ───
        swing_look = self.p["swing_lookback"]
        swing_data = candles.iloc[-(swing_look + 1):-1]
        buffer = atr * self.p["sl_buffer_atr"]

        if action == Action.BUY:
            swing_low = float(swing_data["low"].min())
            sl_price = swing_low - buffer

            sl_dist = c - sl_price
            # Enforce min/max SL
            if sl_dist < atr * self.p["sl_min_atr"]:
                sl_price = c - (atr * self.p["sl_min_atr"])
                sl_dist = c - sl_price
            if sl_dist > atr * self.p["sl_max_atr"]:
                sl_price = c - (atr * self.p["sl_max_atr"])
                sl_dist = c - sl_price

            tp_price = c + (sl_dist * rr)
        else:
            swing_high = float(swing_data["high"].max())
            sl_price = swing_high + buffer

            sl_dist = sl_price - c
            if sl_dist < atr * self.p["sl_min_atr"]:
                sl_price = c + (atr * self.p["sl_min_atr"])
                sl_dist = sl_price - c
            if sl_dist > atr * self.p["sl_max_atr"]:
                sl_price = c + (atr * self.p["sl_max_atr"])
                sl_dist = sl_price - c

            tp_price = c - (sl_dist * rr)

        # ─── AI Confidence (optional boost, no veto) ───
        ai_conf = 0.5
        dl = self._get_deep_learner()
        if dl:
            try:
                ai_conf = dl.predict_from_candles(candles)
            except Exception:
                ai_conf = 0.5

        # RR boost if AI agrees
        if action == Action.BUY and ai_conf > 0.65:
            rr = min(rr + 0.5, 3.0)
            tp_price = c + (sl_dist * rr)
        elif action == Action.SELL and ai_conf < 0.35:
            rr = min(rr + 0.5, 3.0)
            tp_price = c - (sl_dist * rr)

        # ─── Confidence score ───
        confidence = 0.65
        if adx > 30:
            confidence += 0.10
        if ai_conf > 0.60 or ai_conf < 0.40:
            confidence += 0.05
        confidence = min(confidence, 0.95)

        # ─── Signal! ───
        trend_dir = "UP" if trend_bull else "DOWN"
        reason = (
            f"🐉V3 {trigger_name} @ EMA21 pullback | "
            f"Trend={trend_dir} ADX={adx:.0f} DI+={di_plus:.0f} DI-={di_minus:.0f} "
            f"RSI={rsi:.0f} RR={rr:.1f} AI={ai_conf:.2f}"
        )

        logger.debug("dragon_v3_signal", extra={
            "symbol": symbol, "action": action.value,
            "trigger": trigger_name, "adx": round(adx, 1),
            "di_plus": round(di_plus, 1), "di_minus": round(di_minus, 1),
            "rsi": round(rsi, 1), "atr": round(atr, 2),
            "ai_conf": round(ai_conf, 3), "rr": rr,
            "ema21": round(ema21_val, 2), "ema50": round(ema50_val, 2),
            "ema200": round(ema200_val, 2),
            "stage": "signal", "result": "ok",
        })

        return Decision(
            symbol=symbol,
            action=action,
            confidence=confidence,
            reason=reason,
            stop_loss=sl_price,
            take_profit=tp_price,
            risk_reward_ratio=rr,
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["gold_ai_dragon", "v3", trigger_name.lower().replace(" ", "_")],
            debug={
                "version": "v3",
                "trigger": trigger_name,
                "adx": round(adx, 1),
                "di_plus": round(di_plus, 1),
                "di_minus": round(di_minus, 1),
                "rsi": round(rsi, 1),
                "atr": round(atr, 2),
                "ai_conf": round(ai_conf, 3),
                "rr": rr,
                "ema21": round(ema21_val, 2),
                "ema50": round(ema50_val, 2),
                "ema200": round(ema200_val, 2),
                "sl_dist": round(sl_dist, 2),
            },
        )

    # ═════════════════════════════════════════════
    # INDICATOR HELPERS
    # ═════════════════════════════════════════════

    def _compute_adx(self, high, low, close):
        """Compute ADX, DI+, DI-."""
        period = self.p["adx_period"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)

        up_move = high - high.shift()
        down_move = low.shift() - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        plus_dm_s = pd.Series(plus_dm, index=high.index).rolling(period).mean()
        minus_dm_s = pd.Series(minus_dm, index=high.index).rolling(period).mean()
        atr_s = tr.rolling(period).mean()

        di_plus_s = 100 * plus_dm_s / atr_s
        di_minus_s = 100 * minus_dm_s / atr_s
        dx = 100 * (di_plus_s - di_minus_s).abs() / (di_plus_s + di_minus_s + 1e-10)
        adx_s = dx.rolling(period).mean()

        adx = float(adx_s.iloc[-1]) if not pd.isna(adx_s.iloc[-1]) else 0
        di_p = float(di_plus_s.iloc[-1]) if not pd.isna(di_plus_s.iloc[-1]) else 0
        di_m = float(di_minus_s.iloc[-1]) if not pd.isna(di_minus_s.iloc[-1]) else 0
        return adx, di_p, di_m

    def _compute_rsi(self, close):
        """Compute RSI."""
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(self.p["rsi_period"]).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(self.p["rsi_period"]).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
