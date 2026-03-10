"""
AUD Mean Revert Strategy — Bollinger Band Mean-Reversion for AUDUSDm.

Logic:
    1. Bollinger Bands (20, 1.5 std) — tighter for AUD's narrow range
    2. RSI (14) — OS < 30 (buy), OB > 70 (sell)
    3. ADX < 25 — only trade in ranging/low-volatility conditions
    4. Volume spike filter — skip if tick_volume > 2× average
    5. TP = Middle band (mean reversion target)
    6. SL = 1.0× ATR beyond entry band

Target: WR > 50%, profitable on 100-day M5 backtest
Pair: AUDUSDm (low volatility, range-bound, mean-reverting)
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
BB_LENGTH = 20
BB_STD = 1.5           # Tighter than standard 2.0 for AUD's narrow range
RSI_LENGTH = 14
RSI_OS = 30            # Oversold — buy signal
RSI_OB = 70            # Overbought — sell signal
ADX_MAX = 25           # Only trade when ADX < 25 (ranging)
ATR_PERIOD = 14
SL_ATR_MULT = 1.0      # Tight SL for ranging
VOLUME_SPIKE_MULT = 2.0 # Skip if volume > 2× avg
MIN_CONFIDENCE = 0.55
MIN_RR = 0.8           # Minimum risk:reward ratio


class AudMeanRevertStrategy(BaseStrategy):
    """
    AUD Mean Revert — เทรด mean-reversion ในกรอบ Bollinger Band.

    เหมาะกับ AUDUSD ที่มี volatility ต่ำและ mean-revert ดี.
    """

    name = "aud_mean_revert"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.RANGING,
        RegimeType.LOW_VOLATILITY,
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
        # ADX
        adx_df = ta.adx(candles["high"], candles["low"], candles["close"], length=14)
        adx_val = 50.0
        if adx_df is not None:
            adx_col = "ADX_14"
            if adx_col in adx_df.columns:
                adx_val = adx_df[adx_col].iloc[-1]

        # Bollinger Bands
        bb = ta.bbands(candles["close"], length=BB_LENGTH, std=BB_STD)
        if bb is None:
            return self.create_hold(symbol=symbol, reason="BB calc failed")

        # Column order: 0=Lower, 1=Mid, 2=Upper, 3=Bandwidth, 4=%B
        lower = bb.iloc[-1, 0]
        mid = bb.iloc[-1, 1]
        upper = bb.iloc[-1, 2]

        # RSI
        rsi = ta.rsi(candles["close"], length=RSI_LENGTH)
        rsi_val = rsi.iloc[-1] if rsi is not None else 50.0

        # ATR
        atr = ta.atr(candles["high"], candles["low"], candles["close"], length=ATR_PERIOD)
        atr_val = atr.iloc[-1] if atr is not None else 0.0

        if pd.isna(atr_val) or atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR invalid")

        close = candles["close"].iloc[-1]
        band_range = upper - lower

        # ─── GATE 1: Regime check — must be ranging ───
        if regime in [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN]:
            return self.create_hold(
                symbol=symbol,
                reason=f"Skip: Market TRENDING ({regime.value})"
            )

        # ─── GATE 2: ADX must be low (ranging) ───
        if not pd.isna(adx_val) and adx_val > ADX_MAX:
            return self.create_hold(
                symbol=symbol,
                reason=f"ADX {adx_val:.1f} > {ADX_MAX} — too directional"
            )

        # ─── GATE 3: Volume spike filter ───
        if "tick_volume" in candles.columns:
            vol_avg = candles["tick_volume"].rolling(50).mean().iloc[-1]
            vol_now = candles["tick_volume"].iloc[-1]
            if not pd.isna(vol_avg) and vol_avg > 0:
                if vol_now > vol_avg * VOLUME_SPIKE_MULT:
                    return self.create_hold(
                        symbol=symbol,
                        reason=f"Volume spike ({vol_now:.0f} > {vol_avg * VOLUME_SPIKE_MULT:.0f}) — possible news"
                    )

        # ─── Signal Detection ───
        action = Action.HOLD
        confidence = 0.0
        reasons = []

        # BUY: Price at/near lower band + RSI oversold
        near_lower = close <= (lower + band_range * 0.10)
        if near_lower:
            confidence += 0.30
            reasons.append(f"Price near Lower BB ({lower:.5f})")

            if rsi_val < RSI_OS:
                confidence += 0.25
                reasons.append(f"RSI {rsi_val:.1f} oversold")
            elif rsi_val < 40:
                confidence += 0.10
                reasons.append(f"RSI {rsi_val:.1f} low")

            # Bullish candle confirmation
            if candles["close"].iloc[-1] > candles["open"].iloc[-1]:
                confidence += 0.08
                reasons.append("Bullish candle")

            if confidence >= MIN_CONFIDENCE:
                action = Action.BUY

        # SELL: Price at/near upper band + RSI overbought
        near_upper = close >= (upper - band_range * 0.10)
        if near_upper and action == Action.HOLD:
            confidence = 0.0  # Reset
            reasons = []

            confidence += 0.30
            reasons.append(f"Price near Upper BB ({upper:.5f})")

            if rsi_val > RSI_OB:
                confidence += 0.25
                reasons.append(f"RSI {rsi_val:.1f} overbought")
            elif rsi_val > 60:
                confidence += 0.10
                reasons.append(f"RSI {rsi_val:.1f} high")

            # Bearish candle confirmation
            if candles["close"].iloc[-1] < candles["open"].iloc[-1]:
                confidence += 0.08
                reasons.append("Bearish candle")

            if confidence >= MIN_CONFIDENCE:
                action = Action.SELL

        if action == Action.HOLD:
            return self.create_hold(symbol=symbol, reason="No BB extremes")

        # ─── SL/TP Calculation ───
        # TP = Middle band (mean reversion)
        # SL = ATR buffer beyond band
        if action == Action.BUY:
            tp = mid
            sl = lower - (atr_val * SL_ATR_MULT)
            risk = close - sl
            reward = tp - close
        else:
            tp = mid
            sl = upper + (atr_val * SL_ATR_MULT)
            risk = sl - close
            reward = close - tp

        # RR check
        if risk <= 0:
            return self.create_hold(symbol=symbol, reason="Risk distance ≤ 0")
        rr = reward / risk
        if rr < MIN_RR:
            return self.create_hold(
                symbol=symbol,
                reason=f"Low RR ({rr:.2f} < {MIN_RR})"
            )

        confidence = max(0.0, min(1.0, confidence))

        stop_loss = round(sl, profile.digits)
        take_profit = round(tp, profile.digits)

        logger.debug("aud_mean_revert_signal", extra={
            "symbol": symbol, "action": action.value,
            "confidence": round(confidence, 3),
            "rsi": round(rsi_val, 1),
            "adx": round(adx_val, 1),
            "bb_width": round(band_range, profile.digits),
            "rr": round(rr, 2),
        })

        return Decision(
            symbol=symbol, action=action, confidence=round(confidence, 3),
            reason="; ".join(reasons),
            stop_loss=stop_loss, take_profit=take_profit,
            risk_reward_ratio=round(rr, 2),
            strategy_name=self.name, timeframe=self.timeframe,
            tags=["aud", "mean_reversion", "bollinger"],
            debug={
                "rsi": round(rsi_val, 1),
                "adx": round(adx_val, 1),
                "bb_lower": round(lower, profile.digits),
                "bb_mid": round(mid, profile.digits),
                "bb_upper": round(upper, profile.digits),
                "atr": round(atr_val, profile.digits),
            },
        )
