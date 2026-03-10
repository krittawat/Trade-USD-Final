"""
GBP Session Breakout Strategy — Asia Range Breakout for GBPUSDc.

Logic:
    1. Calculate Asia session range (00:00–07:00 UTC high/low)
    2. Wait for London open (07:00–16:00 UTC)
    3. Enter on price breaking above/below Asia range
    4. ADX > 18 confirms real breakout (not false spike)
    5. SL = opposite side of Asia range + ATR buffer
    6. TP = 1.5× Asia range distance (conservative for high WR)

Target: WR > 50%, profitable on 100-day M5 backtest
Pair: GBPUSDc (high volatility, respects session levels)
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
ATR_PERIOD = 14
ADX_PERIOD = 14
ADX_MIN = 18            # Minimum ADX for confirmed breakout

# Asia session hours (UTC)
ASIA_START_HOUR = 0
ASIA_END_HOUR = 7       # 00:00–07:00 UTC

# Trading session (London/NY)
LONDON_START = 7
NY_END = 21

# SL/TP
SL_ATR_BUFFER = 0.5     # Extra buffer beyond Asia range for SL
TP_RANGE_MULT = 1.5     # TP = 1.5× Asia range distance
MIN_RANGE_ATR = 0.3     # Min Asia range = 0.3× ATR (filter tiny ranges)
MAX_RANGE_ATR = 3.0     # Max Asia range = 3× ATR (filter crazy ranges)

MIN_CONFIDENCE = 0.55


class GbpSessionBreakoutStrategy(BaseStrategy):
    """
    GBP Session Breakout — เทรด breakout กรอบ Asia ใน London/NY session.

    เหมาะกับ GBPUSD ที่มี volatility สูงและเคารพ session levels.
    """

    name = "gbp_session_breakout"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
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
        if candles is None or len(candles) < 200:
            return self.create_hold(symbol=symbol, reason="Insufficient data")

        # ─── Indicators ───
        atr = ta.atr(candles["high"], candles["low"], candles["close"], length=ATR_PERIOD)
        adx_df = ta.adx(candles["high"], candles["low"], candles["close"], length=ADX_PERIOD)

        atr_val = atr.iloc[-1] if atr is not None else None
        adx_val = None
        plus_di = None
        minus_di = None
        if adx_df is not None:
            adx_col = f"ADX_{ADX_PERIOD}"
            dmp_col = f"DMP_{ADX_PERIOD}"
            dmn_col = f"DMN_{ADX_PERIOD}"
            if adx_col in adx_df.columns:
                adx_val = adx_df[adx_col].iloc[-1]
            if dmp_col in adx_df.columns:
                plus_di = adx_df[dmp_col].iloc[-1]
            if dmn_col in adx_df.columns:
                minus_di = adx_df[dmn_col].iloc[-1]

        if atr_val is None or pd.isna(atr_val) or atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR invalid")

        # ─── Get current hour ───
        last_time = candles.iloc[-1].get("time")
        if last_time is None:
            return self.create_hold(symbol=symbol, reason="No time column")
        if isinstance(last_time, (pd.Timestamp, datetime)):
            current_hour = last_time.hour
        else:
            return self.create_hold(symbol=symbol, reason="Time format unknown")

        # ─── Session filter: only trade in London/NY ───
        if current_hour < LONDON_START or current_hour >= NY_END:
            return self.create_hold(symbol=symbol, reason=f"Outside trading hours (hour={current_hour})")

        # ─── Calculate Asia Range ───
        # Find candles from Asia session (last occurrence of 00:00–07:00 UTC)
        asia_candles = candles[
            candles["time"].apply(
                lambda t: ASIA_START_HOUR <= t.hour < ASIA_END_HOUR
                if isinstance(t, (pd.Timestamp, datetime)) else False
            )
        ]

        if len(asia_candles) < 10:
            return self.create_hold(symbol=symbol, reason="Insufficient Asia candles")

        # Use the most recent Asia session (last 84 M5 candles = 7 hours)
        asia_recent = asia_candles.tail(84)
        asia_high = asia_recent["high"].max()
        asia_low = asia_recent["low"].min()
        asia_range = asia_high - asia_low

        # ─── Range validation ───
        if asia_range < atr_val * MIN_RANGE_ATR:
            return self.create_hold(
                symbol=symbol,
                reason=f"Asia range too small ({asia_range:.5f} < {atr_val * MIN_RANGE_ATR:.5f})",
            )
        if asia_range > atr_val * MAX_RANGE_ATR:
            return self.create_hold(
                symbol=symbol,
                reason=f"Asia range too wide ({asia_range:.5f}) — likely news",
            )

        # ─── Breakout detection ───
        close = candles["close"].iloc[-1]
        prev_close = candles["close"].iloc[-2]

        action = Action.HOLD
        confidence = 0.0
        reasons = []

        # BUY: Close breaks above Asia High (and previous close was inside range)
        if close > asia_high and prev_close <= asia_high:
            action = Action.BUY
            confidence = 0.50
            reasons.append(f"Break above Asia High ({asia_high:.5f})")

        # SELL: Close breaks below Asia Low
        elif close < asia_low and prev_close >= asia_low:
            action = Action.SELL
            confidence = 0.50
            reasons.append(f"Break below Asia Low ({asia_low:.5f})")

        if action == Action.HOLD:
            return self.create_hold(symbol=symbol, reason="No breakout from Asia range")

        # ─── Confluence: ADX confirms momentum ───
        if adx_val is not None and not pd.isna(adx_val):
            if adx_val > ADX_MIN:
                confidence += 0.15
                reasons.append(f"ADX {adx_val:.1f} confirms momentum")
            else:
                confidence -= 0.10
                reasons.append(f"ADX {adx_val:.1f} weak (possible false breakout)")

        # ─── Confluence: DI direction match ───
        if plus_di is not None and minus_di is not None:
            if not pd.isna(plus_di) and not pd.isna(minus_di):
                if action == Action.BUY and plus_di > minus_di:
                    confidence += 0.10
                    reasons.append(f"+DI({plus_di:.1f})>-DI({minus_di:.1f})")
                elif action == Action.SELL and minus_di > plus_di:
                    confidence += 0.10
                    reasons.append(f"-DI({minus_di:.1f})>+DI({plus_di:.1f})")
                else:
                    confidence -= 0.05
                    reasons.append("DI direction mismatch")

        # ─── Confluence: London session bonus ───
        if 8 <= current_hour <= 15:
            confidence += 0.05
            reasons.append("London active session")

        # ─── Confidence check ───
        confidence = max(0.0, min(1.0, confidence))
        if confidence < MIN_CONFIDENCE:
            return self.create_hold(
                symbol=symbol,
                reason=f"Confidence {confidence:.2f} < {MIN_CONFIDENCE} | {'; '.join(reasons)}",
            )

        # ─── SL/TP Calculation ───
        sl_buffer = atr_val * SL_ATR_BUFFER
        tp_distance = asia_range * TP_RANGE_MULT

        if action == Action.BUY:
            stop_loss = round(asia_low - sl_buffer, profile.digits)
            take_profit = round(close + tp_distance, profile.digits)
        else:
            stop_loss = round(asia_high + sl_buffer, profile.digits)
            take_profit = round(close - tp_distance, profile.digits)

        actual_sl_dist = abs(close - stop_loss)
        rr = tp_distance / actual_sl_dist if actual_sl_dist > 0 else 0

        logger.debug("gbp_breakout_signal", extra={
            "symbol": symbol, "action": action.value,
            "confidence": round(confidence, 3),
            "asia_high": round(asia_high, profile.digits),
            "asia_low": round(asia_low, profile.digits),
            "asia_range": round(asia_range, profile.digits),
            "rr": round(rr, 2),
        })

        return Decision(
            symbol=symbol, action=action, confidence=round(confidence, 3),
            reason="; ".join(reasons),
            stop_loss=stop_loss, take_profit=take_profit,
            risk_reward_ratio=round(rr, 2),
            strategy_name=self.name, timeframe=self.timeframe,
            tags=["gbp", "session_breakout", "asia_range"],
            debug={
                "asia_high": round(asia_high, profile.digits),
                "asia_low": round(asia_low, profile.digits),
                "asia_range": round(asia_range, profile.digits),
                "adx": round(adx_val, 1) if adx_val and not pd.isna(adx_val) else None,
                "atr": round(atr_val, profile.digits),
            },
        )
