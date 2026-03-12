"""
Gold Precision Strategy — สูตรยิงแม่น Win Rate สูง สำหรับ XAUUSDc.

ปรัชญา:
    - ยิงน้อย ยิงแม่น (10-20 เทรด/สัปดาห์)
    - ไม่ trade ตลาด chop/sideways → ตัดเทรดเสียออก
    - ต้อง confluence ≥ 5/7 layers ถึงเข้า
    - Session filter: เทรดแค่ London + NY (Gold moves)
    - Multi-timeframe: M15 entry aligned กับ H1 trend
    - ATR-based dynamic SL กว้างพอ + structure-anchored

7 Confluence Layers:
    1. H1 Trend (EMA 50/200 aligned)
    2. ADX > 25 (ตลาดมี trend)
    3. EMA 9/21 alignment (M15)
    4. RSI divergence zone (ไม่ oversold/overbought)
    5. VWAP bias (ราคาอยู่ฝั่งเอื้อ)
    6. Candle pattern (engulfing, pin bar, momentum bar)
    7. Volume confirmation (> 1.2x avg)

Target:
    - Win Rate: 55-65%
    - Risk:Reward: 1:2
    - Profit Factor: > 1.5
"""

import pandas as pd
import pandas_ta as ta
import app.analysis.indicators as ind

from app.strategy.base import BaseStrategy
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

logger = get_logger(__name__)

# ═════════════════════════════════════════════
# Parameters
# ═════════════════════════════════════════════

# Session filter — Gold moves ช่วง London/NY เท่านั้น
LONDON_START = 7       # 07:00 UTC = 14:00 TH
LONDON_END = 16        # 16:00 UTC = 23:00 TH
NY_START = 12          # 12:00 UTC = 19:00 TH
NY_END = 21            # 21:00 UTC = 04:00 TH

# Trend filters
EMA_FAST = 9
EMA_MID = 21
EMA_SLOW = 50
EMA_TREND = 200      # H1 trend via 200-bar EMA on M15 (~67 hours lookback)

# Momentum
ADX_PERIOD = 14
ADX_MIN = 25           # ต้อง trending จริง (เพิ่มจาก 23)
RSI_PERIOD = 14
RSI_OB = 72            # Overbought → ห้าม BUY
RSI_OS = 28            # Oversold → ห้าม SELL

# Volume
VOL_MA = 20
VOL_MIN_RATIO = 1.2    # volume ต้อง > 1.2x avg

# Risk
ATR_PERIOD = 14
SL_ATR_MULT = 2.5      # SL = 2.5x ATR (Gold M15 ต้องกว้างพอ)
RR_TARGET = 2.0         # TP = SL × 2.0
MIN_CONFLUENCE = 6      # ต้อง ≥ 6/7 layers pass
SWING_LOOKBACK = 10     # ดู swing H/L ย้อนหลัง 10 bars (2.5 ชม. M15)

# Candle pattern
MIN_BODY_RATIO = 0.4    # body ≥ 40% ของ range (ไม่ใช่ doji)
PIN_BAR_RATIO = 2.0     # wick ≥ 2x body


class GoldPrecisionStrategy(BaseStrategy):
    """
    Gold Precision — High Win Rate strategy for XAUUSDc.

    ยิงน้อย ยิงแม่น ด้วย 7-layer confluence.
    ออกแบบสำหรับ M15 chart + H1 trend confirmation.
    """

    name = "gold_precision"
    timeframe = "M15"
    suitable_regimes = [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN]

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "XAUUSDc"

        # ─── Data check ───
        if candles is None or len(candles) < EMA_TREND + 10:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {EMA_TREND + 10})",
            )

        # ─── Session filter: London/NY only ───
        if "time" in candles.columns:
            current_time = candles["time"].iloc[-1]
            if hasattr(current_time, 'hour'):
                hour = current_time.hour
            else:
                try:
                    hour = pd.Timestamp(current_time).hour
                except Exception:
                    hour = 12  # assume OK
            in_london = LONDON_START <= hour < LONDON_END
            in_ny = NY_START <= hour < NY_END
            if not (in_london or in_ny):
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Session filter: hour={hour} outside London/NY",
                )

        # ─── Compute all indicators ───
        close = candles["close"]
        high = candles["high"]
        low = candles["low"]
        current_close = float(close.iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])
        current_open = float(candles["open"].iloc[-1])
        prev_close = float(close.iloc[-2])
        prev_open = float(candles["open"].iloc[-2])
        prev_high = float(high.iloc[-2])
        prev_low = float(low.iloc[-2])

        ema_fast = ta.ema(close, length=EMA_FAST)
        ema_mid = ta.ema(close, length=EMA_MID)
        ema_slow = ta.ema(close, length=EMA_SLOW)
        ema_trend = ta.ema(close, length=EMA_TREND)

        adx_df = ta.adx(high, low, close, length=ADX_PERIOD)
        rsi = ta.rsi(close, length=RSI_PERIOD)
        atr = ta.atr(high, low, close, length=ATR_PERIOD)

        # Volume — use tick_volume if available
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        if vol_col in candles.columns:
            vol = candles[vol_col].astype(float)
            vol_ma = vol.rolling(window=VOL_MA).mean()
        else:
            vol = None
            vol_ma = None

        # ─── Get current values ───
        ema_f = float(ema_fast.iloc[-1]) if ema_fast is not None else None
        ema_m = float(ema_mid.iloc[-1]) if ema_mid is not None else None
        ema_s = float(ema_slow.iloc[-1]) if ema_slow is not None else None
        ema_t = float(ema_trend.iloc[-1]) if ema_trend is not None else None

        adx_val = float(adx_df[f"ADX_{ADX_PERIOD}"].iloc[-1]) if adx_df is not None and f"ADX_{ADX_PERIOD}" in adx_df.columns else None
        rsi_val = float(rsi.iloc[-1]) if rsi is not None else 50.0
        atr_val = float(atr.iloc[-1]) if atr is not None else None

        vol_current = float(vol.iloc[-1]) if vol is not None else None
        vol_avg = float(vol_ma.iloc[-1]) if vol_ma is not None else None

        # ─── NaN check ───
        critical = [ema_f, ema_m, ema_s, ema_t, adx_val, rsi_val, atr_val]
        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in critical):
            return self.create_hold(symbol=symbol, reason="Indicators NaN — need more data")

        # ─── Chop regime block ───
        if regime in (RegimeType.RANGING, RegimeType.LOW_VOLATILITY):
            return self.create_hold(
                symbol=symbol,
                reason=f"Regime {regime.value} — chop/sideways, skip",
            )

        # ═════════════════════════════════════════
        # 7-LAYER CONFLUENCE CHECK
        # ═════════════════════════════════════════

        buy_layers = 0
        sell_layers = 0
        buy_reasons = []
        sell_reasons = []

        # ── Layer 1: H1 Trend (EMA 50/200 alignment on M15 ≈ H1 via EMA 200) ──
        trend_up = ema_s > ema_t  # EMA 50 > EMA 200 = uptrend
        trend_down = ema_s < ema_t

        if trend_up:
            buy_layers += 1
            buy_reasons.append("L1:H1↑ EMA50>200")
        if trend_down:
            sell_layers += 1
            sell_reasons.append("L1:H1↓ EMA50<200")

        # ── Layer 2: ADX > threshold (market trending) ──
        if adx_val > ADX_MIN:
            buy_layers += 1
            sell_layers += 1
            buy_reasons.append(f"L2:ADX {adx_val:.0f}>{ADX_MIN}")
            sell_reasons.append(f"L2:ADX {adx_val:.0f}>{ADX_MIN}")

        # ── Layer 3: EMA 9/21 alignment (short-term direction) ──
        if ema_f > ema_m > ema_s:
            buy_layers += 1
            buy_reasons.append("L3:EMA 9>21>50")
        if ema_f < ema_m < ema_s:
            sell_layers += 1
            sell_reasons.append("L3:EMA 9<21<50")

        # ── Layer 4: RSI divergence zone ──
        if 35 < rsi_val < RSI_OB:
            buy_layers += 1
            buy_reasons.append(f"L4:RSI {rsi_val:.0f} in zone")
        if RSI_OS < rsi_val < 65:
            sell_layers += 1
            sell_reasons.append(f"L4:RSI {rsi_val:.0f} in zone")

        # ── Layer 5: VWAP bias ──
        # Approximate VWAP using rolling VWAP = sum(close*vol) / sum(vol) over 20 bars
        vwap_val = None
        if vol is not None and len(vol) >= 20:
            try:
                cv = (close * vol).rolling(20).sum()
                sv = vol.rolling(20).sum()
                vwap_series = cv / sv
                vwap_val = float(vwap_series.iloc[-1])
            except Exception:
                pass

        if vwap_val is not None and not pd.isna(vwap_val):
            if current_close > vwap_val:
                buy_layers += 1
                buy_reasons.append("L5:Price>VWAP")
            if current_close < vwap_val:
                sell_layers += 1
                sell_reasons.append("L5:Price<VWAP")

        # ── Layer 6: Candle pattern (momentum bar, engulfing, pin bar) ──
        body = abs(current_close - current_open)
        total_range = current_high - current_low
        body_ratio = body / total_range if total_range > 0 else 0

        # Bullish patterns
        bullish_engulfing = (
            current_close > current_open and  # bullish candle
            prev_close < prev_open and          # previous bearish
            current_close > prev_open and       # body engulfs
            current_open < prev_close
        )
        bullish_pin = (
            body_ratio > 0.2 and
            (current_open - current_low) > body * PIN_BAR_RATIO and  # long lower wick
            current_close > current_open  # closed bullish
        )
        bullish_momentum = (
            current_close > current_open and
            body_ratio > MIN_BODY_RATIO and
            current_close > prev_high  # close above prev high = strong
        )

        if bullish_engulfing or bullish_pin or bullish_momentum:
            buy_layers += 1
            pattern = "engulf" if bullish_engulfing else ("pin" if bullish_pin else "momentum")
            buy_reasons.append(f"L6:{pattern} candle")

        # Bearish patterns
        bearish_engulfing = (
            current_close < current_open and
            prev_close > prev_open and
            current_close < prev_open and
            current_open > prev_close
        )
        bearish_pin = (
            body_ratio > 0.2 and
            (current_high - current_open) > body * PIN_BAR_RATIO and
            current_close < current_open
        )
        bearish_momentum = (
            current_close < current_open and
            body_ratio > MIN_BODY_RATIO and
            current_close < prev_low
        )

        if bearish_engulfing or bearish_pin or bearish_momentum:
            sell_layers += 1
            pattern = "engulf" if bearish_engulfing else ("pin" if bearish_pin else "momentum")
            sell_reasons.append(f"L6:{pattern} candle")

        # ── Layer 7: Volume confirmation ──
        if vol_current is not None and vol_avg is not None and vol_avg > 0:
            vol_ratio = vol_current / vol_avg
            if vol_ratio >= VOL_MIN_RATIO:
                buy_layers += 1
                sell_layers += 1
                buy_reasons.append(f"L7:Vol {vol_ratio:.1f}x")
                sell_reasons.append(f"L7:Vol {vol_ratio:.1f}x")

        # ═════════════════════════════════════════
        # DECISION
        # ═════════════════════════════════════════

        action = Action.HOLD
        confidence = 0.0
        reasons = []
        sl_price = None
        tp_price = None

        # ── BUY signal ──
        if buy_layers >= MIN_CONFLUENCE and trend_up:
            action = Action.BUY
            confidence = min(buy_layers / 7.0, 1.0)

            # SL: below recent swing low or ATR-based, whichever is wider
            swing_low = float(candles["low"].iloc[-SWING_LOOKBACK:].min())
            sl_atr = current_close - (atr_val * SL_ATR_MULT)
            sl_price = min(swing_low - (atr_val * 0.3), sl_atr)  # wider = safer
            sl_dist = current_close - sl_price
            tp_price = current_close + (sl_dist * RR_TARGET)

            reasons = buy_reasons + [
                f"SL:{sl_price:.2f} ({sl_dist:.2f}dist)",
                f"TP:{tp_price:.2f} (RR={RR_TARGET})"
            ]

        # ── SELL signal ──
        elif sell_layers >= MIN_CONFLUENCE and trend_down:
            action = Action.SELL
            confidence = min(sell_layers / 7.0, 1.0)

            swing_high = float(candles["high"].iloc[-SWING_LOOKBACK:].max())
            sl_atr = current_close + (atr_val * SL_ATR_MULT)
            sl_price = max(swing_high + (atr_val * 0.3), sl_atr)
            sl_dist = sl_price - current_close
            tp_price = current_close - (sl_dist * RR_TARGET)

            reasons = sell_reasons + [
                f"SL:{sl_price:.2f} ({sl_dist:.2f}dist)",
                f"TP:{tp_price:.2f} (RR={RR_TARGET})"
            ]

        # ── HOLD ──
        else:
            max_layers = max(buy_layers, sell_layers)
            side = "BUY" if buy_layers > sell_layers else "SELL"
            return self.create_hold(
                symbol=symbol,
                reason=f"Confluence {max_layers}/7 ({side}) < {MIN_CONFLUENCE} required",
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
            tags=["gold_precision", f"confluence_{buy_layers if action == Action.BUY else sell_layers}"],
            debug={
                "buy_layers": buy_layers,
                "sell_layers": sell_layers,
                "adx": round(adx_val, 1),
                "rsi": round(rsi_val, 1),
                "atr": round(atr_val, 2),
                "ema_alignment": "bull" if ema_f > ema_m > ema_s else ("bear" if ema_f < ema_m < ema_s else "mixed"),
                "regime": regime.value if hasattr(regime, 'value') else str(regime),
            },
        )
