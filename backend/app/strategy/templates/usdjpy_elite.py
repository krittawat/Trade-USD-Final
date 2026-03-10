"""
USDJPY Elite Strategy — Dual-Mode (Trend-Momentum + Tokyo-Range) for Max Profit.

Target: 300 THB/day (~$8.5 USD) from USDJPY M5.

Philosophy:
    - USDJPY has low spread (~0.7-1.5 pips) → ideal for tight SL scalping
    - Session-driven: Tokyo carry flows, London momentum, NY volatility
    - Dual-Mode:
        * Trending (ADX≥25): EMA 9/21/50 stack + RSI pullback + MACD → RR 2.5
        * Ranging  (ADX<25): BB(20,1.8) mean-reversion at session calm → RR 1.0
    - No dead zone between modes (ADX threshold = 25 for both)

Indicators:
    - EMA 9/21/50 (trend stack)
    - RSI (14)
    - ADX (14) + DI+/DI-
    - MACD (12,26,9) histogram
    - Bollinger Bands (20, 1.8)
    - ATR (14)

Target: Win Rate >55%, Profit Factor >1.5, 2-4 trades/day
"""

import pandas as pd
import numpy as np
from datetime import datetime

from app.strategy.base import BaseStrategy
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

logger = get_logger(__name__)

# ═════════════════════════════════════════════
# Parameters (USDJPY-Optimized)
# ═════════════════════════════════════════════

# EMA Trend Stack
EMA_FAST = 9
EMA_MID = 21
EMA_SLOW = 50

# Momentum
ADX_PERIOD = 14
ADX_TRENDING = 25        # ADX ≥ 25 = trending mode
ADX_STRONG = 35          # Strong trend bonus
RSI_PERIOD = 14
RSI_BUY_ZONE = (35, 50)  # BUY pullback zone
RSI_SELL_ZONE = (50, 65)  # SELL pullback zone
RSI_EXTREME_BUY = 30     # Deep oversold → confidence boost
RSI_EXTREME_SELL = 70    # Deep overbought → confidence boost

# MACD
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Bollinger Bands (Ranging mode)
BB_LEN = 20
BB_STD = 1.8
BB_RSI_BUY = 35          # RSI for ranging BUY trigger
BB_RSI_SELL = 65          # RSI for ranging SELL trigger

# Risk (USDJPY-optimized tight SL)
ATR_PERIOD = 14
SL_ATR_MULT_TREND = 1.5   # Trending: tight SL
SL_ATR_MULT_RANGE = 1.2   # Ranging: even tighter (mean-rev)
RR_TREND_BASE = 2.0       # Trending base RR
RR_TREND_HIGH = 2.5       # Trending high-confluence RR
RR_RANGE = 1.0            # Ranging: quick TP to BB mid
MIN_SL_PIPS = 5.0         # Minimum 5 pips SL (0.050 for JPY)

# Session (UTC hours) — USDJPY specific
TOKYO_START = 0    # Tokyo 00:00-03:00 UTC (strongest JPY flows)
TOKYO_END = 7
LONDON_START = 7   # London 07:00-16:00 UTC
LONDON_END = 16
NY_START = 13      # NY 13:00-21:00 UTC
NY_END = 21
# Dead zone: 03:00-06:00 UTC (avoid)

# Cooldown
MIN_BARS_BETWEEN_TRADES = 3  # 3 bars × 5min = 15 minutes

# Confidence
MIN_CONFIDENCE = 55  # Out of 100


# ═════════════════════════════════════════════
# Helper Functions
# ═════════════════════════════════════════════

def _get_usdjpy_session(candles: pd.DataFrame) -> str:
    """Get current session for USDJPY trading."""
    try:
        last_time = candles.iloc[-1].get("time")
        if last_time is None:
            return "UNKNOWN"
        if isinstance(last_time, (pd.Timestamp, datetime)):
            hour = last_time.hour
        else:
            return "UNKNOWN"

        if LONDON_START <= hour < NY_START:
            return "LONDON"
        elif NY_START <= hour < LONDON_END:
            return "OVERLAP"  # London + NY overlap = best volatility
        elif LONDON_END <= hour < NY_END:
            return "NY"
        elif TOKYO_START <= hour < TOKYO_END:
            return "TOKYO"
        else:
            return "DEAD_ZONE"
    except Exception:
        return "UNKNOWN"


def _is_bullish_engulfing(candles: pd.DataFrame) -> bool:
    """Detect bullish engulfing on last 2 bars."""
    if len(candles) < 2:
        return False
    prev = candles.iloc[-2]
    curr = candles.iloc[-1]
    return (prev["close"] < prev["open"] and
            curr["close"] > curr["open"] and
            curr["close"] > prev["open"] and
            curr["open"] <= prev["close"])


def _is_bearish_engulfing(candles: pd.DataFrame) -> bool:
    """Detect bearish engulfing on last 2 bars."""
    if len(candles) < 2:
        return False
    prev = candles.iloc[-2]
    curr = candles.iloc[-1]
    return (prev["close"] > prev["open"] and
            curr["close"] < curr["open"] and
            curr["close"] < prev["open"] and
            curr["open"] >= prev["close"])


def _is_pin_bar(candles: pd.DataFrame, atr: float, bullish: bool = True) -> bool:
    """Detect pin bar (long wick rejection)."""
    if len(candles) < 1 or atr <= 0:
        return False
    c = candles.iloc[-1]
    body = abs(c["close"] - c["open"])
    total_range = c["high"] - c["low"]
    if total_range < atr * 0.3:
        return False

    if bullish:
        lower_wick = min(c["open"], c["close"]) - c["low"]
        return (lower_wick > total_range * 0.55 and
                body < total_range * 0.35 and
                c["close"] >= c["open"])
    else:
        upper_wick = c["high"] - max(c["open"], c["close"])
        return (upper_wick > total_range * 0.55 and
                body < total_range * 0.35 and
                c["close"] <= c["open"])


def _is_strong_body(candles: pd.DataFrame, atr: float) -> tuple[bool, bool]:
    """Check if current candle has strong body (≥55% of range, body > 0.4×ATR).
    Returns (is_bullish_strong, is_bearish_strong).
    """
    if len(candles) < 1 or atr <= 0:
        return False, False
    c = candles.iloc[-1]
    body = abs(c["close"] - c["open"])
    total_range = c["high"] - c["low"]
    if total_range <= 0:
        return False, False
    body_ratio = body / total_range
    is_strong = body_ratio > 0.55 and body > atr * 0.4
    if is_strong:
        return (c["close"] > c["open"], c["close"] < c["open"])
    return False, False


# ═════════════════════════════════════════════
# Strategy Class
# ═════════════════════════════════════════════

class UsdjpyEliteStrategy(BaseStrategy):
    """
    USDJPY Elite — Dual-Mode strategy optimized for 300 THB/day profit.

    Trending (ADX≥25):
        BUY: EMA9>21>50, RSI 35-50, MACD histogram > 0
        SELL: EMA9<21<50, RSI 50-65, MACD histogram < 0
        TP = SL × 2.0-2.5 (adaptive RR)

    Ranging (ADX<25):
        BUY: close < BB Lower OR RSI < 35
        SELL: close > BB Upper OR RSI > 65
        TP = BB Middle (mean reversion)
    """

    name = "usdjpy_elite"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.RANGING,
        RegimeType.LOW_VOLATILITY,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.BREAKOUT,
    ]

    def __init__(self):
        super().__init__()
        self._last_signal_bar = -999

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "USDJPYc"

        # ─── Data check ───
        min_bars = max(EMA_SLOW + 30, MACD_SLOW + MACD_SIGNAL + 10, 120)
        if candles is None or len(candles) < min_bars:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {min_bars})",
            )

        # ─── Session filter ───
        session = _get_usdjpy_session(candles)
        if session == "DEAD_ZONE":
            return self.create_hold(
                symbol=symbol,
                reason=f"Session: DEAD_ZONE (UTC 3-7, low liquidity)",
            )

        # ─── Cooldown ───
        bar_idx = len(candles) - 1
        if (bar_idx - self._last_signal_bar) < MIN_BARS_BETWEEN_TRADES:
            return self.create_hold(
                symbol=symbol,
                reason=f"Cooldown: wait {MIN_BARS_BETWEEN_TRADES} bars",
            )

        # ─── Compute Indicators ───
        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)

        current_close = float(close.iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])
        current_open = float(open_.iloc[-1])

        # EMA Stack
        ema_fast = close.ewm(span=EMA_FAST, adjust=False).mean()
        ema_mid = close.ewm(span=EMA_MID, adjust=False).mean()
        ema_slow = close.ewm(span=EMA_SLOW, adjust=False).mean()
        ema_f_val = float(ema_fast.iloc[-1])
        ema_m_val = float(ema_mid.iloc[-1])
        ema_s_val = float(ema_slow.iloc[-1])

        # RSI
        rsi_val = self._compute_rsi(close)

        # ADX + DI
        adx_val, di_plus, di_minus = self._compute_adx(high, low, close)

        # MACD
        macd_hist = self._compute_macd(close)

        # ATR
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(ATR_PERIOD).mean()
        atr_val = float(atr_series.iloc[-1])

        # Bollinger Bands
        bb_mid = close.rolling(BB_LEN).mean()
        bb_std = close.rolling(BB_LEN).std()
        bb_upper = bb_mid + (BB_STD * bb_std)
        bb_lower = bb_mid - (BB_STD * bb_std)

        bb_mid_val = float(bb_mid.iloc[-1])
        bb_upper_val = float(bb_upper.iloc[-1])
        bb_lower_val = float(bb_lower.iloc[-1])

        # NaN check
        if any(pd.isna(v) for v in [ema_f_val, ema_m_val, ema_s_val,
                                      rsi_val, adx_val, atr_val,
                                      bb_mid_val, macd_hist]):
            return self.create_hold(symbol=symbol, reason="Indicators NaN")

        if atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR = 0")

        # ═════════════════════════════════════════
        # DETERMINE MODE
        # ═════════════════════════════════════════

        if adx_val >= ADX_TRENDING:
            mode = "TRENDING"
        else:
            mode = "RANGING"

        # ═════════════════════════════════════════
        # TRENDING MODE — EMA Stack + RSI Pullback + MACD
        # ═════════════════════════════════════════

        if mode == "TRENDING":
            action = Action.HOLD
            confidence = 0.0
            reasons = []
            sl_price = None
            tp_price = None

            # Check EMA stack alignment
            uptrend = ema_f_val > ema_m_val > ema_s_val
            downtrend = ema_f_val < ema_m_val < ema_s_val

            if not uptrend and not downtrend:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"TRENDING but EMA not aligned: {ema_f_val:.3f}/{ema_m_val:.3f}/{ema_s_val:.3f}",
                )

            # ─── BUY Setup ───
            if uptrend:
                action = Action.BUY
                confidence = 55.0
                reasons.append(f"EMA9>21>50 ({ema_f_val:.3f}>{ema_m_val:.3f}>{ema_s_val:.3f})")

                # RSI Pullback check (bonus, not hard gate)
                if RSI_BUY_ZONE[0] <= rsi_val <= RSI_BUY_ZONE[1]:
                    confidence += 12
                    reasons.append(f"RSI pullback({rsi_val:.1f})")
                elif rsi_val < RSI_EXTREME_BUY:
                    confidence += 8
                    reasons.append(f"RSI deep oversold({rsi_val:.1f})")
                elif rsi_val > 70:
                    confidence -= 10
                    reasons.append(f"RSI overbought({rsi_val:.1f}) penalty")

                # MACD confirmation
                if macd_hist > 0:
                    confidence += 10
                    reasons.append(f"MACD hist+({macd_hist:.5f})")
                else:
                    confidence -= 5
                    reasons.append(f"MACD hist-({macd_hist:.5f})")

                # DI confirmation
                if di_plus > di_minus:
                    confidence += 8
                    reasons.append(f"+DI>{'-'}DI ({di_plus:.1f}>{di_minus:.1f})")
                else:
                    confidence -= 5
                    reasons.append(f"DI mismatch")

                # Pullback to EMA (price near EMA21)
                dist_to_ema21 = abs(current_close - ema_m_val) / atr_val
                if dist_to_ema21 < 2.0:
                    confidence += 5
                    reasons.append(f"Near EMA21 ({dist_to_ema21:.1f} ATR)")
                elif dist_to_ema21 > 3.5:
                    confidence -= 8
                    reasons.append(f"Extended ({dist_to_ema21:.1f} ATR from EMA21)")

                # Candle patterns
                if _is_bullish_engulfing(candles):
                    confidence += 10
                    reasons.append("Bull engulfing")
                elif _is_pin_bar(candles, atr_val, bullish=True):
                    confidence += 8
                    reasons.append("Bull pin bar")
                else:
                    bull_strong, _ = _is_strong_body(candles, atr_val)
                    if bull_strong:
                        confidence += 5
                        reasons.append("Strong bull body")

                # SL/TP
                sl_distance = atr_val * SL_ATR_MULT_TREND
                min_sl = MIN_SL_PIPS * profile.point * 10  # Convert pips to price
                sl_distance = max(sl_distance, min_sl)
                sl_price = round(current_close - sl_distance, profile.digits)

                # Adaptive RR
                rr = RR_TREND_HIGH if confidence >= 80 else RR_TREND_BASE
                tp_price = round(current_close + sl_distance * rr, profile.digits)

            # ─── SELL Setup ───
            elif downtrend:
                action = Action.SELL
                confidence = 55.0
                reasons.append(f"EMA9<21<50 ({ema_f_val:.3f}<{ema_m_val:.3f}<{ema_s_val:.3f})")

                # RSI Pullback
                if RSI_SELL_ZONE[0] <= rsi_val <= RSI_SELL_ZONE[1]:
                    confidence += 12
                    reasons.append(f"RSI pullback({rsi_val:.1f})")
                elif rsi_val > RSI_EXTREME_SELL:
                    confidence += 8
                    reasons.append(f"RSI deep overbought({rsi_val:.1f})")
                elif rsi_val < 30:
                    confidence -= 10
                    reasons.append(f"RSI oversold({rsi_val:.1f}) penalty")

                # MACD confirmation
                if macd_hist < 0:
                    confidence += 10
                    reasons.append(f"MACD hist-({macd_hist:.5f})")
                else:
                    confidence -= 5
                    reasons.append(f"MACD hist+({macd_hist:.5f})")

                # DI confirmation
                if di_minus > di_plus:
                    confidence += 8
                    reasons.append(f"-DI>+DI ({di_minus:.1f}>{di_plus:.1f})")
                else:
                    confidence -= 5
                    reasons.append(f"DI mismatch")

                # Pullback to EMA
                dist_to_ema21 = abs(current_close - ema_m_val) / atr_val
                if dist_to_ema21 < 2.0:
                    confidence += 5
                    reasons.append(f"Near EMA21 ({dist_to_ema21:.1f} ATR)")
                elif dist_to_ema21 > 3.5:
                    confidence -= 8
                    reasons.append(f"Extended ({dist_to_ema21:.1f} ATR from EMA21)")

                # Candle patterns
                if _is_bearish_engulfing(candles):
                    confidence += 10
                    reasons.append("Bear engulfing")
                elif _is_pin_bar(candles, atr_val, bullish=False):
                    confidence += 8
                    reasons.append("Bear pin bar")
                else:
                    _, bear_strong = _is_strong_body(candles, atr_val)
                    if bear_strong:
                        confidence += 5
                        reasons.append("Strong bear body")

                # SL/TP
                sl_distance = atr_val * SL_ATR_MULT_TREND
                min_sl = MIN_SL_PIPS * profile.point * 10
                sl_distance = max(sl_distance, min_sl)
                sl_price = round(current_close + sl_distance, profile.digits)

                rr = RR_TREND_HIGH if confidence >= 80 else RR_TREND_BASE
                tp_price = round(current_close - sl_distance * rr, profile.digits)

            # ADX strength bonus
            if adx_val > ADX_STRONG:
                confidence += 5
                reasons.append(f"ADX strong({adx_val:.1f})")

        # ═════════════════════════════════════════
        # RANGING MODE — BB Mean Reversion
        # ═════════════════════════════════════════

        elif mode == "RANGING":
            action = Action.HOLD
            confidence = 0.0
            reasons = []
            sl_price = None
            tp_price = None

            # BUY: close < BB Lower OR RSI < threshold
            if current_close < bb_lower_val or rsi_val < BB_RSI_BUY:
                action = Action.BUY
                confidence = 55.0
                reasons.append(f"MR:BUY close<BB_L({current_close:.3f}<{bb_lower_val:.3f})")

                if rsi_val < BB_RSI_BUY:
                    confidence += 10
                    reasons.append(f"RSI oversold({rsi_val:.1f})")
                if rsi_val < RSI_EXTREME_BUY:
                    confidence += 10
                    reasons.append(f"RSI extreme({rsi_val:.1f})")

                # Volume confirmation
                vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
                if vol_col in candles.columns:
                    vol = candles[vol_col].astype(float)
                    vol_current = float(vol.iloc[-1])
                    vol_avg = float(vol.rolling(20).mean().iloc[-1])
                    if vol_avg > 0 and vol_current > vol_avg * 1.2:
                        confidence += 8
                        reasons.append("Vol spike confirm")

                # Candle pattern
                if _is_bullish_engulfing(candles):
                    confidence += 8
                    reasons.append("Bull engulfing")
                elif _is_pin_bar(candles, atr_val, bullish=True):
                    confidence += 6
                    reasons.append("Bull pin bar")

                # SL/TP
                sl_distance = atr_val * SL_ATR_MULT_RANGE
                min_sl = MIN_SL_PIPS * profile.point * 10
                sl_distance = max(sl_distance, min_sl)
                sl_price = round(current_close - sl_distance, profile.digits)
                tp_price = round(bb_mid_val, profile.digits)  # Mean reversion to BB mid

            # SELL: close > BB Upper OR RSI > threshold
            elif current_close > bb_upper_val or rsi_val > BB_RSI_SELL:
                action = Action.SELL
                confidence = 55.0
                reasons.append(f"MR:SELL close>BB_U({current_close:.3f}>{bb_upper_val:.3f})")

                if rsi_val > BB_RSI_SELL:
                    confidence += 10
                    reasons.append(f"RSI overbought({rsi_val:.1f})")
                if rsi_val > RSI_EXTREME_SELL:
                    confidence += 10
                    reasons.append(f"RSI extreme({rsi_val:.1f})")

                # Volume confirmation
                vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
                if vol_col in candles.columns:
                    vol = candles[vol_col].astype(float)
                    vol_current = float(vol.iloc[-1])
                    vol_avg = float(vol.rolling(20).mean().iloc[-1])
                    if vol_avg > 0 and vol_current > vol_avg * 1.2:
                        confidence += 8
                        reasons.append("Vol spike confirm")

                # Candle pattern
                if _is_bearish_engulfing(candles):
                    confidence += 8
                    reasons.append("Bear engulfing")
                elif _is_pin_bar(candles, atr_val, bullish=False):
                    confidence += 6
                    reasons.append("Bear pin bar")

                # SL/TP
                sl_distance = atr_val * SL_ATR_MULT_RANGE
                min_sl = MIN_SL_PIPS * profile.point * 10
                sl_distance = max(sl_distance, min_sl)
                sl_price = round(current_close + sl_distance, profile.digits)
                tp_price = round(bb_mid_val, profile.digits)

            else:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Ranging but no extreme: close={current_close:.3f} BB=[{bb_lower_val:.3f},{bb_upper_val:.3f}] RSI={rsi_val:.1f}",
                )

        # ═════════════════════════════════════════
        # SESSION BONUS
        # ═════════════════════════════════════════

        if session == "OVERLAP":
            confidence += 8
            reasons.append("London/NY Overlap (best)")
        elif session == "LONDON":
            confidence += 5
            reasons.append("London session")
        elif session == "NY":
            confidence += 4
            reasons.append("NY session")
        elif session == "TOKYO":
            confidence += 3
            reasons.append("Tokyo session")

        # Regime bonus
        if regime in self.suitable_regimes:
            confidence += 3

        # ═════════════════════════════════════════
        # VALIDATE & RETURN
        # ═════════════════════════════════════════

        if action == Action.HOLD:
            return self.create_hold(symbol=symbol, reason="No setup")

        # Min confidence check
        if confidence < MIN_CONFIDENCE:
            return self.create_hold(
                symbol=symbol,
                reason=f"Confidence {confidence:.0f} < {MIN_CONFIDENCE} | {'; '.join(reasons[:3])}",
            )

        # SL distance safety
        if sl_price is not None:
            sl_dist = abs(current_close - sl_price)
            min_sl_price = MIN_SL_PIPS * profile.point * 10
            if sl_dist < min_sl_price:
                if action == Action.BUY:
                    sl_price = round(current_close - min_sl_price, profile.digits)
                else:
                    sl_price = round(current_close + min_sl_price, profile.digits)
                reasons.append(f"SL adjusted to min({MIN_SL_PIPS} pips)")

        # RR calculation
        rr = 0.0
        if sl_price and tp_price:
            sl_d = abs(current_close - sl_price)
            tp_d = abs(tp_price - current_close)
            rr = tp_d / sl_d if sl_d > 0 else 0

        # Normalize confidence to 0-1 range
        confidence_norm = min(confidence / 100.0, 0.95)

        # Mark signal for cooldown
        self._last_signal_bar = bar_idx

        reason_str = f"{mode} | " + " | ".join(reasons[:6])

        logger.debug("usdjpy_elite_signal", extra={
            "symbol": symbol, "action": action.value,
            "mode": mode, "confidence": round(confidence_norm, 2),
            "adx": round(adx_val, 1), "rsi": round(rsi_val, 1),
            "macd_hist": round(macd_hist, 6),
            "session": session, "rr": round(rr, 2),
            "stage": "signal", "result": "ok",
        })

        return Decision(
            symbol=symbol,
            action=action,
            confidence=confidence_norm,
            reason=reason_str,
            stop_loss=sl_price,
            take_profit=tp_price,
            risk_reward_ratio=rr,
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["usdjpy_elite", mode.lower(), f"conf_{confidence:.0f}"],
            debug={
                "mode": mode,
                "ema_fast": round(ema_f_val, 3),
                "ema_mid": round(ema_m_val, 3),
                "ema_slow": round(ema_s_val, 3),
                "adx": round(adx_val, 1),
                "di_plus": round(di_plus, 1),
                "di_minus": round(di_minus, 1),
                "rsi": round(rsi_val, 1),
                "macd_hist": round(macd_hist, 6),
                "bb_upper": round(bb_upper_val, 3),
                "bb_lower": round(bb_lower_val, 3),
                "bb_mid": round(bb_mid_val, 3),
                "atr": round(atr_val, 5),
                "session": session,
            },
        )

    # ═════════════════════════════════════════════
    # HELPERS
    # ═════════════════════════════════════════════

    def _compute_rsi(self, close: pd.Series) -> float:
        """Compute RSI."""
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(RSI_PERIOD).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

    def _compute_adx(self, high: pd.Series, low: pd.Series, close: pd.Series) -> tuple[float, float, float]:
        """Compute ADX, DI+, DI-."""
        period = ADX_PERIOD
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

        di_plus = 100 * plus_dm_s / (atr_s + 1e-10)
        di_minus = 100 * minus_dm_s / (atr_s + 1e-10)
        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-10)
        adx = dx.rolling(period).mean()

        adx_val = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0.0
        di_p = float(di_plus.iloc[-1]) if not pd.isna(di_plus.iloc[-1]) else 0.0
        di_m = float(di_minus.iloc[-1]) if not pd.isna(di_minus.iloc[-1]) else 0.0
        return adx_val, di_p, di_m

    def _compute_macd(self, close: pd.Series) -> float:
        """Compute MACD histogram."""
        ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
        ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=MACD_SIGNAL, adjust=False).mean()
        histogram = macd_line - signal_line
        val = float(histogram.iloc[-1])
        return val if not pd.isna(val) else 0.0
