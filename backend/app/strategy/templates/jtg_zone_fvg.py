"""
JTG Bounce Zone + FVG Strategy — M15 Gold/Forex/Crypto.

EVO V2 (GA-evolved + AI Deep Model boost):
    - Ultra-tight ATR-based SL (SL = 0.5×ATR, max 1.75×ATR)
    - High RR target (2.75)
    - Low confluence (2) + AI Deep boost (+2.5pts)
    - Session filter: 15-17 UTC (NY afternoon)
    - Candle pattern detector reused from shared modulence zones
    Reel 2: FVG = price magnet, enter with candle confirmation
    Reel 3: Multi-confluence 60-70% WR setup
    Reel 4: Shallow FVG fill = directional bias (key edge)

6-Layer Confluence System (max 10 points):
    Layer 1: HTF Trend + Slope   (+1 pt)
    Layer 2: S/R Bounce Zone     (+2 pts)
    Layer 3: FVG Zone            (+2 pts)
    Layer 4: FVG Fill Depth      (+1 pt)
    Layer 5: Candle Pattern      (+2 pts)
    Layer 6: Volume + RSI        (+2 pts)

    Min Entry Score: 5/10 points
    Hard Gate: Structural zone (FVG or S/R) required

SL/TP:
    - SL: ATR-based (1.0 × ATR), tight enough for M15
    - TP: SL × 2.0 (RR target)

Evolution:
    V1: 75T WR=33% PF=0.98 DD=21% (structure SL too wide)
    V2: 21T WR=19% PF=0.60 DD=13% (threshold 7+gates too strict)
    V3: 47T WR=21% PF=0.65 DD=24% (pattern gate hurts WR)
    V4: tight ATR SL + structural gate (not pattern)
"""

import numpy as np
import pandas as pd

from app.brain.pattern_detector import PatternDetector
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Parameters
# ═════════════════════════════════════════════════════════════════════

# Session filter — GA-evolved: only 15-17 UTC (NY afternoon sweet spot)
LONDON_START = 7       # 07:00 UTC = 14:00 TH (legacy, overridden by session_start_utc)
LONDON_END = 16        # 16:00 UTC = 23:00 TH
NY_START = 12          # 12:00 UTC = 19:00 TH (legacy, overridden by session_start_utc)
NY_END = 21            # 21:00 UTC = 04:00 TH

# HTF Trend — EVO V2: faster EMA for quick trend detection
EMA_FAST = 20          # EVO V2: 20 (from 50) — faster trend response
EMA_SLOW = 160         # EVO V2: 160 (from 200) — shorter baseline

# S/R Zones
SR_LOOKBACK = 30       # bars to find swing high/low
SR_ZONE_ATR_MULT = 0.8 # EVO V2: 0.8 (from 0.3) — wider zones catch more bounces

# FVG (Fair Value Gap)
FVG_LOOKBACK = 40      # bars to scan for FVG
FVG_MIN_GAP_ATR = 0.5  # gap must be > 0.5 × ATR
FVG_SHALLOW_THRESHOLD = 0.50  # fill < 50% = shallow (Reel #4 key insight)
FVG_DEEP_THRESHOLD = 0.75     # fill > 75% = weak/invalidated

# Volume
VOL_MA = 20
VOL_SPIKE_RATIO = 1.3  # EVO V2: 1.3 (from 1.5) — lower threshold

# RSI — EVO V2: wider buy band, tighter sell band
RSI_PERIOD = 14
RSI_BUY_MAX = 50       # EVO V2: 50 (from 60)
RSI_BUY_MIN = 15       # EVO V2: 15 (from 30) — allow oversold entries
RSI_SELL_MIN = 35       # EVO V2: 35 (from 40)
RSI_SELL_MAX = 55       # EVO V2: 55 (from 70) — tighter sell band

# Risk — EVO V2: tight SL + high RR (WR=59%, PF=3.97, DD=2.8%)
ATR_PERIOD = 14
SL_ATR_MULT = 0.5      # EVO V2: 0.5 (from 1.0) — very tight SL
SL_MAX_ATR = 1.75      # EVO V2: 1.75 (from 1.5)
RR_TARGET = 2.75       # EVO V2: 2.75 (from 2.0) — bigger wins
MIN_CONFLUENCE = 2     # EVO V2: 2 (from 5) — AI boost compensates
EMA_SLOPE_PERIOD = 5   # bars to measure EMA slope
MIN_EMA_SLOPE_ATR = 0.02  # EMA slope must be > 2% of ATR per bar

# Candle pattern names that count as confirmation (from PatternDetector)
BULL_PATTERNS = {
    "bullish_engulfing", "hammer", "bullish_pin_bar",
    "morning_star", "three_white_soldiers", "bullish_marubozu",
    "inverted_hammer",
}
BEAR_PATTERNS = {
    "bearish_engulfing", "shooting_star", "bearish_pin_bar",
    "evening_star", "three_black_crows", "bearish_marubozu",
    "hanging_man",
}


class JTGZoneFVGStrategy(BaseStrategy):
    """
    JTG Bounce Zone + FVG — 7-Layer Confluence Strategy (EVO V2).

    Layers:
        L1: HTF trend alignment (EMA 20/160)
        L2: S/R zone bounce detection
        L3: Fair Value Gap with fill-depth scoring
        L4: Market structure (HH/HL or LH/LL)
        L5: Candlestick pattern confirmation
        L6: Volume spike + RSI filter
        L7: AI Deep V4 Model boost (+2.5 pts when P(dir) > 40%)

    Session: 15-17 UTC only (NY afternoon sweet spot, GA-evolved)
    Designed for M15 chart, optimized for Gold.
    """

    name = "jtg_zone_fvg"
    timeframe = "M15"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
    ]

    def __init__(self):
        self.pattern_detector = PatternDetector()
        self._ai_model = None
        self._ai_feature_engine = None
        self._ai_init_done = False

    def _get_ai_prediction(self, candles: pd.DataFrame, symbol: str) -> dict | None:
        """
        Lazy-load AI Deep V4 and get P(buy)/P(sell)/P(hold).
        Returns None if model unavailable or prediction fails.
        """
        if not self._ai_init_done:
            self._ai_init_done = True
            try:
                from app.brain.deep_model_v4 import DeepModelV4
                from app.brain.mtf_feature_engine import MTFFeatureEngine
                self._ai_model = DeepModelV4(symbol)
                if self._ai_model._trained:
                    self._ai_feature_engine = MTFFeatureEngine()
                else:
                    self._ai_model = None
            except Exception:
                self._ai_model = None

        if self._ai_model is None or self._ai_feature_engine is None:
            return None

        try:
            if len(candles) < 120:
                return None
            window = candles.iloc[-200:] if len(candles) > 200 else candles
            features = self._ai_feature_engine.build_live_features_v4(
                candles_m5=window,
                candles_m15=None, candles_h1=None,
                candles_h4=None, candles_d1=None,
            )
            if features is None:
                return None
            pred = self._ai_model.predict(features.squeeze(0))
            return {"buy": pred["buy"], "sell": pred["sell"], "hold": pred["hold"]}
        except Exception:
            return None


    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "XAUUSDc"

        # ─── Evolvable params (kwargs override module defaults) ───
        p_sl_atr_mult = float(kwargs.get("sl_atr_mult", SL_ATR_MULT))
        p_sl_max_atr = float(kwargs.get("sl_max_atr", SL_MAX_ATR))
        p_rr_target = float(kwargs.get("rr_target", RR_TARGET))
        p_min_confluence = int(kwargs.get("min_confluence", MIN_CONFLUENCE))
        p_sr_lookback = int(kwargs.get("sr_lookback", SR_LOOKBACK))
        p_sr_zone_atr = float(kwargs.get("sr_zone_atr_mult", SR_ZONE_ATR_MULT))
        p_fvg_min_gap = float(kwargs.get("fvg_min_gap_atr", FVG_MIN_GAP_ATR))
        p_vol_spike = float(kwargs.get("vol_spike_ratio", VOL_SPIKE_RATIO))
        p_rsi_buy_max = float(kwargs.get("rsi_buy_max", RSI_BUY_MAX))
        p_rsi_buy_min = float(kwargs.get("rsi_buy_min", RSI_BUY_MIN))
        p_rsi_sell_min = float(kwargs.get("rsi_sell_min", RSI_SELL_MIN))
        p_rsi_sell_max = float(kwargs.get("rsi_sell_max", RSI_SELL_MAX))
        p_ema_fast = int(kwargs.get("ema_fast", EMA_FAST))
        p_ema_slow = int(kwargs.get("ema_slow", EMA_SLOW))
        p_require_structure = bool(kwargs.get("require_structure", True))
        p_require_pattern = bool(kwargs.get("require_pattern", True))
        # V2: Session filter params
        p_session_start = int(kwargs.get("session_start_utc", 15))
        p_session_end = int(kwargs.get("session_end_utc", 17))
        # V2: AI Deep Model boost params
        p_ai_threshold = float(kwargs.get("ai_boost_threshold", 0.40))
        p_ai_points = float(kwargs.get("ai_boost_points", 2.5))

        # ─── Data check ───
        min_bars = max(p_ema_slow + 10, FVG_LOOKBACK + 10)
        if candles is None or len(candles) < min_bars:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {min_bars})",
            )

        # ─── Chop regime block ───
        regime_val = getattr(regime, "value", str(regime))
        if regime_val in ("RANGING", "LOW_VOLATILITY"):
            return self.create_hold(
                symbol=symbol,
                reason=f"Regime {regime_val} — JTG needs trending market",
            )

        # ─── EVO V2: Session filter (GA-evolved window) ───
        if "time" in candles.columns:
            current_time = candles["time"].iloc[-1]
            if hasattr(current_time, "hour"):
                hour = current_time.hour
            else:
                try:
                    hour = pd.Timestamp(current_time).hour
                except Exception:
                    hour = 12
            if p_session_start <= p_session_end:
                in_session = p_session_start <= hour < p_session_end
            else:  # wraps midnight
                in_session = hour >= p_session_start or hour < p_session_end
            if not in_session:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Session filter: hour={hour} outside {p_session_start}-{p_session_end} UTC",
                )

        # ═════════════════════════════════════════════════════════════
        # COMPUTE INDICATORS
        # ═════════════════════════════════════════════════════════════
        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)

        current_close = float(close.iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])
        current_open = float(open_.iloc[-1])

        # ATR
        tr = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr_series = tr.rolling(ATR_PERIOD).mean()
        atr_val = float(atr_series.iloc[-1])
        if pd.isna(atr_val) or atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR NaN or zero")

        # EMA for HTF trend
        ema_fast = close.ewm(span=p_ema_fast, adjust=False).mean()
        ema_slow = close.ewm(span=p_ema_slow, adjust=False).mean()
        ema_f = float(ema_fast.iloc[-1])
        ema_s = float(ema_slow.iloc[-1])

        if pd.isna(ema_f) or pd.isna(ema_s):
            return self.create_hold(symbol=symbol, reason="EMA NaN")

        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(RSI_PERIOD).mean()
        loss_s = (-delta.where(delta < 0, 0.0)).rolling(RSI_PERIOD).mean()
        rs = gain / loss_s
        rsi_series = 100 - (100 / (1 + rs))
        rsi_val = (
            float(rsi_series.iloc[-1])
            if not pd.isna(rsi_series.iloc[-1])
            else 50.0
        )

        # Volume
        vol_col = (
            "tick_volume" if "tick_volume" in candles.columns else "volume"
        )
        vol = (
            candles[vol_col].astype(float)
            if vol_col in candles.columns
            else None
        )
        vol_current = float(vol.iloc[-1]) if vol is not None else None
        vol_avg = (
            float(vol.rolling(VOL_MA).mean().iloc[-1])
            if vol is not None
            else None
        )

        # ═════════════════════════════════════════════════════════════
        # 6-LAYER CONFLUENCE CHECK
        # ═════════════════════════════════════════════════════════════

        buy_score = 0
        sell_score = 0
        buy_reasons = []
        sell_reasons = []

        # ── Layer 1: HTF Trend (EMA 50/200 alignment + slope) — +1 pt ──
        htf_bullish = ema_f > ema_s
        htf_bearish = ema_f < ema_s

        # V2: Add slope filter — trend must have momentum
        ema_slope = (ema_fast.iloc[-1] - ema_fast.iloc[-EMA_SLOPE_PERIOD]) / EMA_SLOPE_PERIOD
        slope_strong = abs(ema_slope) > atr_val * MIN_EMA_SLOPE_ATR

        if htf_bullish and (ema_slope > 0 or slope_strong):
            buy_score += 1
            buy_reasons.append(f"L1:HTF↑ slope={ema_slope:.2f}")
        elif htf_bullish:
            buy_reasons.append(f"L1:HTF↑ weak_slope={ema_slope:.2f}")

        if htf_bearish and (ema_slope < 0 or slope_strong):
            sell_score += 1
            sell_reasons.append(f"L1:HTF↓ slope={ema_slope:.2f}")
        elif htf_bearish:
            sell_reasons.append(f"L1:HTF↓ weak_slope={ema_slope:.2f}")

        # ── Layer 2: S/R Bounce Zone — +2 pts ──
        sr_zones = self._find_sr_zones(candles, p_sr_lookback, atr_val)
        swing_high = sr_zones["swing_high"]
        swing_low = sr_zones["swing_low"]
        support_zone = sr_zones["support_zone"]  # (low, high)
        resistance_zone = sr_zones["resistance_zone"]  # (low, high)

        at_support = (
            support_zone[0] <= current_close <= support_zone[1]
        )
        at_resistance = (
            resistance_zone[0] <= current_close <= resistance_zone[1]
        )

        if at_support and htf_bullish:
            buy_score += 2
            buy_reasons.append(
                f"L2:S/R↑ support [{support_zone[0]:.2f}-{support_zone[1]:.2f}]"
            )
        if at_resistance and htf_bearish:
            sell_score += 2
            sell_reasons.append(
                f"L2:S/R↓ resist [{resistance_zone[0]:.2f}-{resistance_zone[1]:.2f}]"
            )

        # ── Layer 3 + 4: FVG Zone + Fill Depth — +2 pts + 1 pt bonus ──
        fvg_results = self._find_fvg_with_depth(candles, atr_val, current_close)

        fvg_buy_score = 0
        fvg_sell_score = 0

        if fvg_results["bull_fvg"]:
            fvg = fvg_results["bull_fvg"]
            if fvg["low"] <= current_close <= fvg["high"]:
                fvg_buy_score += 2
                buy_reasons.append(
                    f"L3:FVG↑ [{fvg['low']:.2f}-{fvg['high']:.2f}]"
                )

                # Layer 4: Fill depth scoring (Reel #4)
                fill_pct = fvg.get("fill_pct", 1.0)
                if fill_pct < FVG_SHALLOW_THRESHOLD:
                    fvg_buy_score += 1
                    buy_reasons.append(
                        f"L4:ShallowFill {fill_pct:.0%} → STRONG"
                    )
                elif fill_pct > FVG_DEEP_THRESHOLD:
                    fvg_buy_score -= 1
                    buy_reasons.append(
                        f"L4:DeepFill {fill_pct:.0%} → weak"
                    )

        if fvg_results["bear_fvg"]:
            fvg = fvg_results["bear_fvg"]
            if fvg["low"] <= current_close <= fvg["high"]:
                fvg_sell_score += 2
                sell_reasons.append(
                    f"L3:FVG↓ [{fvg['low']:.2f}-{fvg['high']:.2f}]"
                )

                fill_pct = fvg.get("fill_pct", 1.0)
                if fill_pct < FVG_SHALLOW_THRESHOLD:
                    fvg_sell_score += 1
                    sell_reasons.append(
                        f"L4:ShallowFill {fill_pct:.0%} → STRONG"
                    )
                elif fill_pct > FVG_DEEP_THRESHOLD:
                    fvg_sell_score -= 1
                    sell_reasons.append(
                        f"L4:DeepFill {fill_pct:.0%} → weak"
                    )

        buy_score += max(fvg_buy_score, 0)
        sell_score += max(fvg_sell_score, 0)

        # ── Layer 5: Candle Pattern Confirmation — +2 pts ──
        try:
            pattern_signals = self.pattern_detector.detect_latest(
                candles, lookback=3
            )
        except Exception as e:
            logger.warning(
                "jtg_pattern_detect_error", extra={"error": str(e)}
            )
            pattern_signals = []

        found_bull_pattern = None
        found_bear_pattern = None

        for sig in pattern_signals:
            sig_name = sig.name if hasattr(sig, "name") else str(sig)
            sig_dir = (
                sig.direction if hasattr(sig, "direction") else "neutral"
            )

            if sig_dir == "bullish" and sig_name in BULL_PATTERNS:
                if found_bull_pattern is None:
                    found_bull_pattern = sig_name
                    buy_score += 2
                    buy_reasons.append(f"L5:{sig_name}↑")

            if sig_dir == "bearish" and sig_name in BEAR_PATTERNS:
                if found_bear_pattern is None:
                    found_bear_pattern = sig_name
                    sell_score += 2
                    sell_reasons.append(f"L5:{sig_name}↓")

        # ── Layer 6: Volume Spike + RSI — +2 pts ──
        # Volume spike (+1)
        if vol_current and vol_avg and vol_avg > 0:
            vol_ratio = vol_current / vol_avg
            if vol_ratio >= p_vol_spike:
                buy_score += 1
                sell_score += 1
                buy_reasons.append(f"L6:Vol {vol_ratio:.1f}x")
                sell_reasons.append(f"L6:Vol {vol_ratio:.1f}x")

        # RSI in range (+1)
        if p_rsi_buy_min < rsi_val < p_rsi_buy_max:
            buy_score += 1
            buy_reasons.append(f"L6:RSI {rsi_val:.0f} ok")
        if p_rsi_sell_min < rsi_val < p_rsi_sell_max:
            sell_score += 1
            sell_reasons.append(f"L6:RSI {rsi_val:.0f} ok")

        # ── Layer 7: AI Deep Model Boost — up to +2.5 pts ──
        if p_ai_points > 0:
            ai_pred = self._get_ai_prediction(candles, symbol)
            if ai_pred is not None:
                if ai_pred["buy"] >= p_ai_threshold:
                    bonus = min(p_ai_points, 3.0)
                    buy_score += int(bonus)
                    buy_reasons.append(f"L7:AI_BUY P={ai_pred['buy']:.2f} +{int(bonus)}")
                if ai_pred["sell"] >= p_ai_threshold:
                    bonus = min(p_ai_points, 3.0)
                    sell_score += int(bonus)
                    sell_reasons.append(f"L7:AI_SELL P={ai_pred['sell']:.2f} +{int(bonus)}")

        # ═════════════════════════════════════════════════════════════
        # V4: HARD GATES
        # ═════════════════════════════════════════════════════════════

        # V4: Structural zone gate (configurable)
        if p_require_structure:
            has_buy_structure = at_support or (fvg_results["bull_fvg"] is not None
                                              and fvg_results["bull_fvg"]["low"] <= current_close <= fvg_results["bull_fvg"]["high"])
            has_sell_structure = at_resistance or (fvg_results["bear_fvg"] is not None
                                                  and fvg_results["bear_fvg"]["low"] <= current_close <= fvg_results["bear_fvg"]["high"])
        else:
            has_buy_structure = True
            has_sell_structure = True

        # Pattern gate (configurable)
        if p_require_pattern:
            has_buy_pattern = found_bull_pattern is not None
            has_sell_pattern = found_bear_pattern is not None
        else:
            has_buy_pattern = True
            has_sell_pattern = True

        # ═════════════════════════════════════════════════════════════
        # DECISION
        # ═════════════════════════════════════════════════════════════

        action = Action.HOLD
        confidence = 0.0
        reasons = []
        sl_price = None
        tp_price = None

        # ── BUY signal ──
        if (buy_score >= p_min_confluence and htf_bullish
                and has_buy_structure and has_buy_pattern):
            action = Action.BUY
            confidence = min(buy_score / 10.0, 1.0)

            # ATR-based SL
            sl_dist = atr_val * p_sl_atr_mult
            max_sl = atr_val * p_sl_max_atr
            if sl_dist > max_sl:
                sl_dist = max_sl

            sl_price = current_close - sl_dist
            tp_price = current_close + (sl_dist * p_rr_target)

            reasons = buy_reasons + [
                f"SL:{sl_price:.2f} ({sl_dist:.2f})",
                f"TP:{tp_price:.2f} (RR={p_rr_target})",
            ]

        # ── SELL signal ──
        elif (sell_score >= p_min_confluence and htf_bearish
              and has_sell_structure and has_sell_pattern):
            action = Action.SELL
            confidence = min(sell_score / 10.0, 1.0)

            sl_dist = atr_val * p_sl_atr_mult
            max_sl = atr_val * p_sl_max_atr
            if sl_dist > max_sl:
                sl_dist = max_sl

            sl_price = current_close + sl_dist
            tp_price = current_close - (sl_dist * p_rr_target)

            reasons = sell_reasons + [
                f"SL:{sl_price:.2f} ({sl_dist:.2f})",
                f"TP:{tp_price:.2f} (RR={p_rr_target})",
            ]

        # ── HOLD ──
        else:
            max_score = max(buy_score, sell_score)
            side = "BUY" if buy_score > sell_score else "SELL"
            hold_reasons = (
                buy_reasons if buy_score > sell_score else sell_reasons
            )
            gate_info = []
            if side == "BUY" and not has_buy_structure:
                gate_info.append("no_structure")
            elif side == "SELL" and not has_sell_structure:
                gate_info.append("no_structure")
            gate_str = f" gates=[{','.join(gate_info)}]" if gate_info else ""
            return self.create_hold(
                symbol=symbol,
                reason=(
                    f"JTG {max_score}/10 ({side}) < {p_min_confluence}{gate_str} | "
                    f"{'; '.join(hold_reasons[:3])}"
                ),
            )

        # Log signal
        logger.debug(
            "jtg_zone_fvg_signal",
            extra={
                "symbol": symbol,
                "action": action.value,
                "confidence": round(confidence, 2),
                "buy_score": buy_score,
                "sell_score": sell_score,
                "atr": round(atr_val, 2),
                "rsi": round(rsi_val, 1),
                "htf_trend": "bull" if htf_bullish else "bear",
                "fvg_bull": bool(fvg_results.get("bull_fvg")),
                "fvg_bear": bool(fvg_results.get("bear_fvg")),
                "pattern_bull": found_bull_pattern,
                "pattern_bear": found_bear_pattern,
                "at_support": at_support,
                "at_resistance": at_resistance,
                "regime": getattr(regime, "value", str(regime)),
                "stage": "signal",
                "result": "ok",
            },
        )

        return Decision(
            symbol=symbol,
            action=action,
            confidence=confidence,
            reason="; ".join(reasons),
            stop_loss=sl_price,
            take_profit=tp_price,
            risk_reward_ratio=RR_TARGET,
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=[
                "jtg_zone_fvg",
                f"score_{buy_score if action == Action.BUY else sell_score}",
                f"pattern_{found_bull_pattern or found_bear_pattern or 'none'}",
            ],
            debug={
                "buy_score": buy_score,
                "sell_score": sell_score,
                "swing_high": round(swing_high, 2),
                "swing_low": round(swing_low, 2),
                "atr": round(atr_val, 2),
                "rsi": round(rsi_val, 1),
                "ema_fast": round(ema_f, 2),
                "ema_slow": round(ema_s, 2),
                "htf_trend": "bull" if htf_bullish else "bear",
                "fvg_bull": fvg_results.get("bull_fvg"),
                "fvg_bear": fvg_results.get("bear_fvg"),
                "regime": getattr(regime, "value", str(regime)),
            },
        )

    # ═════════════════════════════════════════════════════════════════
    # Helper: Find S/R Zones (swing high/low based)
    # ═════════════════════════════════════════════════════════════════

    def _find_sr_zones(
        self,
        candles: pd.DataFrame,
        lookback: int,
        atr: float,
    ) -> dict:
        """
        หา Support/Resistance zones จาก swing high/low.

        Support zone: swing_low ± ATR × 0.5
        Resistance zone: swing_high ± ATR × 0.5
        """
        data = candles.iloc[-(lookback + 1) : -1]  # exclude current bar
        swing_high = float(data["high"].max())
        swing_low = float(data["low"].min())

        zone_width = atr * SR_ZONE_ATR_MULT

        return {
            "swing_high": swing_high,
            "swing_low": swing_low,
            "support_zone": (
                swing_low - zone_width,
                swing_low + zone_width,
            ),
            "resistance_zone": (
                swing_high - zone_width,
                swing_high + zone_width,
            ),
        }

    # ═════════════════════════════════════════════════════════════════
    # Helper: Find FVG with Fill Depth (Reel #4 key innovation)
    # ═════════════════════════════════════════════════════════════════

    def _find_fvg_with_depth(
        self,
        candles: pd.DataFrame,
        atr: float,
        current_price: float,
    ) -> dict:
        """
        หา Fair Value Gap พร้อมคำนวณ fill depth (Reel #4).

        Bullish FVG: candle[i+2].low > candle[i].high → gap ขึ้น
        Bearish FVG: candle[i+2].high < candle[i].low → gap ลง

        Fill Depth:
            - fill_pct < 0.50 = shallow fill → strong directional bias
            - fill_pct > 0.75 = deep fill → weak/invalidated

        Returns:
            {"bull_fvg": {low, high, fill_pct} | None,
             "bear_fvg": {low, high, fill_pct} | None}
        """
        min_gap = atr * FVG_MIN_GAP_ATR
        bull_fvg = None
        bear_fvg = None

        data = candles.iloc[-FVG_LOOKBACK:]
        if len(data) < 3:
            return {"bull_fvg": None, "bear_fvg": None}

        high_vals = data["high"].values.astype(float)
        low_vals = data["low"].values.astype(float)

        for i in range(len(data) - 3, 0, -1):
            # Bullish FVG: gap between candle[i] high and candle[i+2] low
            bull_gap = low_vals[i + 2] - high_vals[i]
            if bull_gap > min_gap and bull_fvg is None:
                fvg_low = float(high_vals[i])
                fvg_high = float(low_vals[i + 2])
                fvg_range = fvg_high - fvg_low

                # Calculate fill depth: how much price has penetrated into FVG
                if fvg_range > 0 and current_price <= fvg_high:
                    # For bullish FVG, price drops from top of gap
                    # fill_pct = how far price has gone into the gap from the top
                    penetration = fvg_high - max(current_price, fvg_low)
                    fill_pct = penetration / fvg_range
                    fill_pct = max(0.0, min(1.0, fill_pct))
                else:
                    fill_pct = 0.0  # price above FVG = not filling

                bull_fvg = {
                    "low": fvg_low,
                    "high": fvg_high,
                    "fill_pct": fill_pct,
                }

            # Bearish FVG: gap between candle[i+2] high and candle[i] low
            bear_gap = low_vals[i] - high_vals[i + 2]
            if bear_gap > min_gap and bear_fvg is None:
                fvg_low = float(high_vals[i + 2])
                fvg_high = float(low_vals[i])
                fvg_range = fvg_high - fvg_low

                # For bearish FVG, price rises from bottom of gap
                if fvg_range > 0 and current_price >= fvg_low:
                    penetration = max(current_price, fvg_low) - fvg_low
                    fill_pct = penetration / fvg_range
                    fill_pct = max(0.0, min(1.0, fill_pct))
                else:
                    fill_pct = 0.0

                bear_fvg = {
                    "low": fvg_low,
                    "high": fvg_high,
                    "fill_pct": fill_pct,
                }

            if bull_fvg and bear_fvg:
                break

        return {"bull_fvg": bull_fvg, "bear_fvg": bear_fvg}
