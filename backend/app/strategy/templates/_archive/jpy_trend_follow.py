"""
JPY Trend Follow Strategy — Triple EMA Pullback for USDJPYc.

Logic:
    1. Triple EMA (9/21/50) alignment for trend direction
    2. Pullback: Price returns to EMA 21 zone (within 1.5× ATR)
    3. RSI 40–60 confirms pullback (not exhaustion)
    4. MACD histogram confirms momentum direction
    5. Session filter: London/NY (JPY trends strongly in these sessions)
    6. SL = 1.5× ATR
    7. TP = 2.5× ATR (RR ≈ 1:1.67)

Target: WR > 50%, profitable on 100-day M5 backtest
Pair: USDJPYc (strong trending, respects EMA structure)
"""

import pandas as pd
import app.analysis.indicators as ind
from datetime import datetime

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)

# ─── Parameters ──────────────────────────────────────────
EMA_FAST = 9
EMA_MID = 21
EMA_SLOW = 50
ATR_PERIOD = 14
RSI_PERIOD = 14

# Pullback zone
PULLBACK_ATR_MULT = 1.5   # Price must be within 1.5× ATR of EMA 21
RSI_PULL_LOW = 40         # RSI pullback zone (buy)
RSI_PULL_HIGH = 60        # RSI pullback zone (sell)

# SL/TP
SL_ATR_MULT = 1.5         # SL = 1.5× ATR (JPY respects levels)
TP_ATR_MULT = 2.5         # TP = 2.5× ATR (ride the trend)

# Session
LONDON_START = 7
NY_END = 21

MIN_CONFIDENCE = 0.55


class JpyTrendFollowStrategy(BaseStrategy):
    """
    JPY Trend Follow — เทรดตามเทรนด์ที่แข็งแรงของ JPY.

    ใช้ Triple EMA alignment + pullback entry.
    เหมาะกับ USDJPY ที่เทรนด์แรงใน London/NY session.
    """

    name = "jpy_trend_follow"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
    ]

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol

        # ─── Data validation ───
        if candles is None or len(candles) < 100:
            return self.create_hold(symbol=symbol, reason="Insufficient data")

        # ─── Indicators ───
        ema9 = ta.ema(candles["close"], length=EMA_FAST)
        ema21 = ta.ema(candles["close"], length=EMA_MID)
        ema50 = ta.ema(candles["close"], length=EMA_SLOW)
        rsi = ta.rsi(candles["close"], length=RSI_PERIOD)
        atr = ta.atr(candles["high"], candles["low"], candles["close"], length=ATR_PERIOD)
        macd_df = ta.macd(candles["close"], fast=12, slow=26, signal=9)

        # Extract values
        close = candles["close"].iloc[-1]
        ema9_val = ema9.iloc[-1] if ema9 is not None else None
        ema21_val = ema21.iloc[-1] if ema21 is not None else None
        ema50_val = ema50.iloc[-1] if ema50 is not None else None
        rsi_val = rsi.iloc[-1] if rsi is not None else None
        atr_val = atr.iloc[-1] if atr is not None else None

        # MACD histogram
        macd_hist = None
        if macd_df is not None:
            hist_col = [c for c in macd_df.columns if "MACDh" in c or "Histogram" in c.title()]
            if not hist_col:
                hist_col = [c for c in macd_df.columns if "h_" in c.lower()]
            if hist_col:
                macd_hist = macd_df[hist_col[0]].iloc[-1]

        # NaN check
        if any(v is None or (isinstance(v, float) and pd.isna(v))
               for v in [ema9_val, ema21_val, ema50_val, atr_val, rsi_val]):
            return self.create_hold(symbol=symbol, reason="Indicators NaN")

        if atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR ≤ 0")

        # ─── Session filter ───
        last_time = candles.iloc[-1].get("time")
        current_hour = None
        if isinstance(last_time, (pd.Timestamp, datetime)):
            current_hour = last_time.hour

        if current_hour is not None:
            if current_hour < LONDON_START or current_hour >= NY_END:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Outside London/NY (hour={current_hour})"
                )

        # ─── GATE 1: Triple EMA Alignment ───
        bullish_alignment = ema9_val > ema21_val > ema50_val
        bearish_alignment = ema9_val < ema21_val < ema50_val

        if not bullish_alignment and not bearish_alignment:
            return self.create_hold(
                symbol=symbol,
                reason=f"No EMA alignment (9={ema9_val:.3f}, 21={ema21_val:.3f}, 50={ema50_val:.3f})"
            )

        uptrend = bullish_alignment
        action = Action.BUY if uptrend else Action.SELL

        # ─── GATE 2: Pullback to EMA 21 ───
        dist_to_ema21 = abs(close - ema21_val)
        pullback_threshold = atr_val * PULLBACK_ATR_MULT

        if dist_to_ema21 > pullback_threshold:
            return self.create_hold(
                symbol=symbol,
                reason=f"Too far from EMA21 ({dist_to_ema21:.3f} > {pullback_threshold:.3f})"
            )

        # ─── Build Confidence ───
        confidence = 0.50
        reasons = []

        # Layer 1: EMA alignment
        reasons.append(f"EMA 9{'>' if uptrend else '<'}21{'>' if uptrend else '<'}50")

        # Layer 2: Pullback proximity bonus
        if dist_to_ema21 < atr_val * 0.5:
            confidence += 0.10
            reasons.append("Near EMA21 (tight pullback)")
        else:
            confidence += 0.05
            reasons.append("Pullback within threshold")

        # Layer 3: RSI in pullback zone
        if rsi_val is not None and not pd.isna(rsi_val):
            if uptrend and RSI_PULL_LOW <= rsi_val <= RSI_PULL_HIGH:
                confidence += 0.10
                reasons.append(f"RSI {rsi_val:.1f} in pullback zone")
            elif not uptrend and RSI_PULL_LOW <= rsi_val <= RSI_PULL_HIGH:
                confidence += 0.10
                reasons.append(f"RSI {rsi_val:.1f} in pullback zone")
            elif (uptrend and rsi_val > 70) or (not uptrend and rsi_val < 30):
                confidence -= 0.10
                reasons.append(f"RSI {rsi_val:.1f} extreme — exhaustion risk")

        # Layer 4: MACD histogram direction
        if macd_hist is not None and not pd.isna(macd_hist):
            if uptrend and macd_hist > 0:
                confidence += 0.10
                reasons.append(f"MACD hist +{macd_hist:.5f} confirms up")
            elif not uptrend and macd_hist < 0:
                confidence += 0.10
                reasons.append(f"MACD hist {macd_hist:.5f} confirms down")
            else:
                confidence -= 0.05
                reasons.append("MACD hist direction mismatch")

        # Layer 5: Price bouncing in trend direction
        if uptrend and close > ema21_val:
            confidence += 0.05
            reasons.append("Price above EMA21 (bounce)")
        elif not uptrend and close < ema21_val:
            confidence += 0.05
            reasons.append("Price below EMA21 (rejection)")

        # ─── Confidence check ───
        confidence = max(0.0, min(1.0, confidence))
        if confidence < MIN_CONFIDENCE:
            return self.create_hold(
                symbol=symbol,
                reason=f"Confidence {confidence:.2f} < {MIN_CONFIDENCE} | {'; '.join(reasons)}",
            )

        # ─── SL/TP Calculation ───
        sl_distance = atr_val * SL_ATR_MULT
        tp_distance = atr_val * TP_ATR_MULT

        if action == Action.BUY:
            stop_loss = round(close - sl_distance, profile.digits)
            take_profit = round(close + tp_distance, profile.digits)
        else:
            stop_loss = round(close + sl_distance, profile.digits)
            take_profit = round(close - tp_distance, profile.digits)

        rr = tp_distance / sl_distance if sl_distance > 0 else 0

        logger.debug("jpy_trend_signal", extra={
            "symbol": symbol, "action": action.value,
            "confidence": round(confidence, 3),
            "ema9": round(ema9_val, profile.digits),
            "ema21": round(ema21_val, profile.digits),
            "ema50": round(ema50_val, profile.digits),
            "rr": round(rr, 2),
        })

        return Decision(
            symbol=symbol, action=action, confidence=round(confidence, 3),
            reason="; ".join(reasons),
            stop_loss=stop_loss, take_profit=take_profit,
            risk_reward_ratio=round(rr, 2),
            strategy_name=self.name, timeframe=self.timeframe,
            tags=["jpy", "trend_follow", "ema_pullback"],
            debug={
                "ema9": round(ema9_val, profile.digits),
                "ema21": round(ema21_val, profile.digits),
                "ema50": round(ema50_val, profile.digits),
                "rsi": round(rsi_val, 1),
                "macd_hist": round(macd_hist, 5) if macd_hist and not pd.isna(macd_hist) else None,
                "atr": round(atr_val, profile.digits),
                "dist_to_ema21": round(dist_to_ema21, profile.digits),
            },
        )
