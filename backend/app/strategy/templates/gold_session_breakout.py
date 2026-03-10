"""
Gold Session Breakout — จับ breakout ช่วง London/NY สำหรับ XAUUSDc.

ปรัชญา:
    - Gold เคลื่อนตัวแรงที่สุดช่วง London Open (14:00-16:00 TH)
      และ NY Open (19:00-22:00 TH)
    - จับ breakout ของ Asian Range (consolidation ก่อน London)
    - Fade exhaustion reversal ที่ปลาย NY session

กลไก:
    1. คำนวณ Asian Range (05:00-14:00 TH / 22:00-07:00 UTC)
    2. รอ breakout เหนือ/ใต้ Asian High/Low ด้วย momentum
    3. Confirm ด้วย ADX + Volume + Candle body
    4. SL ใต้ Asian Range opposite side
    5. TP = 1:2 RR หรือ Asian Range กว้าง

จุดแข็ง:
    - มี structure ชัดเจน (Asian Range)
    - SL ผูกกับ structure → ลด false stop
    - Gold มี habit เดิน breakout London เสมอ
"""

import pandas as pd
import app.analysis.indicators as ind

from app.strategy.base import BaseStrategy
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

logger = get_logger(__name__)

# ═════════════════════════════════════════════
# Parameters
# ═════════════════════════════════════════════

# Session hours (UTC)
ASIAN_START_HOUR = 22   # 22:00 UTC = 05:00 TH (prev day)
ASIAN_END_HOUR = 7      # 07:00 UTC = 14:00 TH
LONDON_START = 7        # 07:00 UTC
LONDON_END = 16         # 16:00 UTC
NY_START = 12           # 12:00 UTC
NY_END = 21             # 21:00 UTC

# Indicators
ADX_PERIOD = 14
ADX_MIN = 20           # breakout ต้องมี momentum
ATR_PERIOD = 14
RSI_PERIOD = 14
EMA_TREND = 50         # trend filter

# Risk
SL_BUFFER_ATR = 0.5    # buffer เพิ่มนอก Asian Range
RR_TARGET = 2.0
MIN_RANGE_ATR = 0.8    # Asian Range ต้องกว้างอย่างน้อย 0.8x ATR (ไม่แคบเกินไป)
MAX_RANGE_ATR = 4.0    # ไม่กว้างเกินไป (กว้าง = SL ใหญ่เกินไป)

# Breakout confirmation
BREAKOUT_CLOSE_PIPS = 2.0    # ราคา close ต้องเลย range อย่างน้อย $2 (Gold)
MIN_BODY_RATIO = 0.5         # candle body ≥ 50% ของ range (strong close)


class GoldSessionBreakout(BaseStrategy):
    """
    Gold Session Breakout — จับ breakout ของ Asian Range ในช่วง London/NY.

    ใช้ M15 chart. SL ผูกกับ structure (Asian High/Low).
    """

    name = "gold_session_breakout"
    timeframe = "M15"
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
        symbol = profile.symbol if profile else "XAUUSDc"

        if candles is None or len(candles) < 100:
            return self.create_hold(symbol=symbol, reason="Data insufficient")

        # ─── Ensure time column ───
        if "time" not in candles.columns:
            return self.create_hold(symbol=symbol, reason="No time column")

        # ─── Current bar info ───
        close = candles["close"]
        high = candles["high"]
        low = candles["low"]
        current_close = float(close.iloc[-1])
        current_open = float(candles["open"].iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])

        # ─── Time ───
        current_time = candles["time"].iloc[-1]
        if hasattr(current_time, 'hour'):
            current_hour = current_time.hour
        else:
            try:
                current_hour = pd.Timestamp(current_time).hour
            except Exception:
                return self.create_hold(symbol=symbol, reason="Cannot parse time")

        # ─── Session filter: only trade during London/NY ───
        in_london = LONDON_START <= current_hour < LONDON_END
        in_ny = NY_START <= current_hour < NY_END
        if not (in_london or in_ny):
            return self.create_hold(
                symbol=symbol,
                reason=f"Outside session (hour={current_hour}, need London/NY)",
            )

        # ─── Calculate Asian Range ───
        # Look back to find bars from Asian session (22:00-07:00 UTC)
        asian_bars = []
        for i in range(len(candles) - 1, max(0, len(candles) - 100), -1):
            bar_time = candles["time"].iloc[i]
            if hasattr(bar_time, 'hour'):
                bar_hour = bar_time.hour
            else:
                try:
                    bar_hour = pd.Timestamp(bar_time).hour
                except Exception:
                    continue

            # Asian session: 22:00-04:59 (prev day) + 05:00-06:59 (same day)
            in_asian = (bar_hour >= ASIAN_START_HOUR or bar_hour < ASIAN_END_HOUR)
            if in_asian:
                asian_bars.append(i)
            elif len(asian_bars) > 0:
                # Past the Asian range, stop collecting
                break

        if len(asian_bars) < 4:
            return self.create_hold(
                symbol=symbol,
                reason=f"Asian Range too small ({len(asian_bars)} bars)"
            )

        # Asian Range = High/Low of Asian session bars
        asian_indices = asian_bars
        asian_high = float(candles["high"].iloc[asian_indices].max())
        asian_low = float(candles["low"].iloc[asian_indices].min())
        asian_range = asian_high - asian_low

        # ─── ATR for range validation ───
        atr = ta.atr(high, low, close, length=ATR_PERIOD)
        atr_val = float(atr.iloc[-1]) if atr is not None and not pd.isna(atr.iloc[-1]) else None
        if atr_val is None:
            return self.create_hold(symbol=symbol, reason="ATR NaN")

        # ─── Validate Asian Range ───
        if asian_range < atr_val * MIN_RANGE_ATR:
            return self.create_hold(
                symbol=symbol,
                reason=f"Asian Range too narrow ({asian_range:.2f} < {atr_val * MIN_RANGE_ATR:.2f})"
            )
        if asian_range > atr_val * MAX_RANGE_ATR:
            return self.create_hold(
                symbol=symbol,
                reason=f"Asian Range too wide ({asian_range:.2f} > {atr_val * MAX_RANGE_ATR:.2f})"
            )

        # ─── Indicators ───
        adx_df = ta.adx(high, low, close, length=ADX_PERIOD)
        rsi = ta.rsi(close, length=RSI_PERIOD)
        ema50 = ta.ema(close, length=EMA_TREND)

        adx_val = float(adx_df[f"ADX_{ADX_PERIOD}"].iloc[-1]) if adx_df is not None and f"ADX_{ADX_PERIOD}" in adx_df.columns else 0
        rsi_val = float(rsi.iloc[-1]) if rsi is not None else 50
        ema50_val = float(ema50.iloc[-1]) if ema50 is not None else current_close

        # Candle body strength
        body = abs(current_close - current_open)
        total_range = current_high - current_low
        body_ratio = body / total_range if total_range > 0 else 0
        is_strong_candle = body_ratio >= MIN_BODY_RATIO

        # ═════════════════════════════════════════
        # BREAKOUT DETECTION
        # ═════════════════════════════════════════

        # Bullish breakout: close above Asian High with conviction
        bullish_breakout = (
            current_close > asian_high + BREAKOUT_CLOSE_PIPS and
            current_close > current_open and              # bullish candle
            is_strong_candle and                           # strong body
            current_close > ema50_val and                 # above EMA trend
            adx_val > ADX_MIN and                          # momentum exists
            rsi_val < 75                                   # not overbought
        )

        # Bearish breakout: close below Asian Low with conviction
        bearish_breakout = (
            current_close < asian_low - BREAKOUT_CLOSE_PIPS and
            current_close < current_open and
            is_strong_candle and
            current_close < ema50_val and
            adx_val > ADX_MIN and
            rsi_val > 25
        )

        if bullish_breakout:
            sl_price = asian_low - (atr_val * SL_BUFFER_ATR)
            sl_dist = current_close - sl_price
            tp_price = current_close + (sl_dist * RR_TARGET)

            return Decision(
                symbol=symbol,
                action=Action.BUY,
                confidence=min(0.65 + (adx_val - ADX_MIN) / 100, 0.90),
                reason=(
                    f"Asian Breakout ↑ close={current_close:.2f} > "
                    f"Asian High={asian_high:.2f}; "
                    f"ADX={adx_val:.0f}; RSI={rsi_val:.0f}; "
                    f"Range={asian_range:.2f}"
                ),
                stop_loss=sl_price,
                take_profit=tp_price,
                risk_reward_ratio=RR_TARGET,
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["gold_session", "asian_breakout", "bullish"],
                debug={
                    "asian_high": asian_high,
                    "asian_low": asian_low,
                    "asian_range": round(asian_range, 2),
                    "adx": round(adx_val, 1),
                    "rsi": round(rsi_val, 1),
                    "body_ratio": round(body_ratio, 2),
                    "session": "london" if in_london else "ny",
                },
            )

        elif bearish_breakout:
            sl_price = asian_high + (atr_val * SL_BUFFER_ATR)
            sl_dist = sl_price - current_close
            tp_price = current_close - (sl_dist * RR_TARGET)

            return Decision(
                symbol=symbol,
                action=Action.SELL,
                confidence=min(0.65 + (adx_val - ADX_MIN) / 100, 0.90),
                reason=(
                    f"Asian Breakout ↓ close={current_close:.2f} < "
                    f"Asian Low={asian_low:.2f}; "
                    f"ADX={adx_val:.0f}; RSI={rsi_val:.0f}; "
                    f"Range={asian_range:.2f}"
                ),
                stop_loss=sl_price,
                take_profit=tp_price,
                risk_reward_ratio=RR_TARGET,
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["gold_session", "asian_breakout", "bearish"],
                debug={
                    "asian_high": asian_high,
                    "asian_low": asian_low,
                    "asian_range": round(asian_range, 2),
                    "adx": round(adx_val, 1),
                    "rsi": round(rsi_val, 1),
                    "body_ratio": round(body_ratio, 2),
                    "session": "london" if in_london else "ny",
                },
            )

        return self.create_hold(
            symbol=symbol,
            reason=(
                f"No breakout (close={current_close:.2f}, "
                f"Asian H/L={asian_high:.2f}/{asian_low:.2f}, "
                f"ADX={adx_val:.0f})"
            ),
        )
