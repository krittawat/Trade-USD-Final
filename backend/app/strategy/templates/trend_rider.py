"""
Trend Rider Strategy — ขี่เทรนด์ยาว.

Logic:
    1. EMA 50/200 → Trend direction (Golden Cross / Death Cross)
    2. ADX > 25 → Trend strength confirmation
    3. Pullback to EMA 50 → Entry point
    4. ATR-based SL/TP (wide SL, 5R target)
    5. Trailing stop concept (ออกแบบให้ปิดโดย BE logic)

ลักษณะ:
    - ไทม์เฟรม: H1
    - เป้าหมาย: RR 1:3 - 1:5
    - เทรดน้อย ถือนาน
    - เหมาะกับ trending market (ADX > 25)
"""

import pandas as pd
import pandas_ta as ta
import app.analysis.indicators as ind

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)

# --- Parameters ---
EMA_FAST = 50        # EMA เร็ว (intermediate trend)
EMA_SLOW = 200       # EMA ช้า (major trend)
ADX_PERIOD = 14      # ADX period
ADX_THRESHOLD = 25   # ADX > 25 = trending
RSI_PERIOD = 14      # RSI period
ATR_PERIOD = 14      # ATR period
ATR_SL_MULT = 2.5    # SL กว้าง (default — Gold/BTC)
RR_TARGET = 2.5      # RR ขั้นต่ำ (lowered from 3.0 for higher WR)
PULLBACK_ATR_MULT = 1.5  # ราคาต้องอยู่ภายใน 1.5 ATR จาก EMA 50 (widened from 1.0)
MIN_CONFIDENCE = 0.55

# --- Per-Asset-Class SL Adjustment ---
FOREX_PREFIXES = ("EUR", "GBP", "USD", "AUD", "NZD", "CAD", "CHF", "JPY")

def _trend_sl_mult(symbol: str) -> float:
    """Forex H1 needs wider SL for deep pullbacks — 3.0 vs default 2.5."""
    s = symbol.upper()
    if any(s.startswith(p) for p in FOREX_PREFIXES):
        return 3.0   # Forex H1: wider for deep pullbacks
    return ATR_SL_MULT  # Gold/BTC default


class TrendRiderStrategy(BaseStrategy):
    """
    Trend Rider Strategy — ขี่เทรนด์ยาว.

    เข้าที่ pullback ในเทรนด์ที่แข็งแรง.
    ออกด้วย trailing stop / BE auto.
    """

    name = "trend_rider"
    timeframe = "H1"
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
        """
        วิเคราะห์สัญญาณ trend riding.

        ขั้นตอน:
            1. ตรวจ EMA 50/200 alignment (trend direction)
            2. ตรวจ ADX > 25 (trend strength)
            3. ตรวจ pullback to EMA 50 (entry zone)
            4. RSI ไม่สุดโต่ง
            5. คำนวณ confidence + ATR-based SL/TP
        """
        symbol = profile.symbol

        # --- อ่านค่า Overrides จาก kwargs (AI Brain) ---
        ema_fast_len = kwargs.get("ema_fast", EMA_FAST)
        ema_slow_len = kwargs.get("ema_slow", EMA_SLOW)
        adx_len = kwargs.get("adx_period", ADX_PERIOD)
        atr_len = kwargs.get("atr_period", ATR_PERIOD)
        rsi_len = kwargs.get("rsi_period", RSI_PERIOD)
        min_conf = kwargs.get("confidence_min", MIN_CONFIDENCE)

        # --- ตรวจข้อมูล ---
        if candles is None or len(candles) < ema_slow_len + 10:
            return self.create_hold(
                symbol=symbol,
                reason=f"ข้อมูลไม่เพียงพอ (ต้อง {ema_slow_len + 10} bars, มี {len(candles) if candles is not None else 0})",
            )

        # --- Indicators ---
        ema50 = ta.ema(candles["close"], length=ema_fast_len)
        ema200 = ta.ema(candles["close"], length=ema_slow_len)
        adx_df = ta.adx(candles["high"], candles["low"], candles["close"], length=adx_len)
        rsi = ta.rsi(candles["close"], length=rsi_len)
        atr = ta.atr(candles["high"], candles["low"], candles["close"], length=atr_len)

        current_close = candles["close"].iloc[-1]
        ema50_val = ema50.iloc[-1] if ema50 is not None else None
        ema200_val = ema200.iloc[-1] if ema200 is not None else None
        rsi_val = rsi.iloc[-1] if rsi is not None else 50.0
        atr_val = atr.iloc[-1] if atr is not None else None

        # ADX value
        adx_val = None
        if adx_df is not None and f"ADX_{ADX_PERIOD}" in adx_df.columns:
            adx_val = adx_df[f"ADX_{ADX_PERIOD}"].iloc[-1]

        # +DI / -DI
        plus_di = None
        minus_di = None
        if adx_df is not None:
            if f"DMP_{ADX_PERIOD}" in adx_df.columns:
                plus_di = adx_df[f"DMP_{ADX_PERIOD}"].iloc[-1]
            if f"DMN_{ADX_PERIOD}" in adx_df.columns:
                minus_di = adx_df[f"DMN_{ADX_PERIOD}"].iloc[-1]

        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in [ema50_val, ema200_val, atr_val]):
            return self.create_hold(symbol=symbol, reason="Indicators NaN — ต้องการข้อมูลเพิ่ม")

        # --- Trend Direction ---
        uptrend = ema50_val > ema200_val   # Golden Cross zone
        downtrend = ema50_val < ema200_val  # Death Cross zone

        # --- ADX Strength ---
        strong_trend = adx_val is not None and adx_val > ADX_THRESHOLD

        # --- Pullback Detection ---
        # ราคาต้องอยู่ใกล้ EMA 50 (ภายใน 1 ATR)
        distance_to_ema50 = abs(current_close - ema50_val)
        is_pullback = distance_to_ema50 <= (atr_val * PULLBACK_ATR_MULT)

        # --- ATR Volatility Gate ---
        # Skip trades in low-vol environments (ATR < 0.5× 3-period avg)
        atr_avg = atr.iloc[-ATR_PERIOD * 3:].mean() if atr is not None else atr_val
        atr_ratio = atr_val / atr_avg if atr_avg > 0 else 1.0
        low_vol = atr_ratio < 0.5

        if low_vol:
            return self.create_hold(
                symbol=symbol,
                reason=f"ATR ratio {atr_ratio:.2f} < 0.5 (low volatility — skip)",
            )

        # --- RSI Previous value for bounce detection ---
        rsi_prev = rsi.iloc[-2] if rsi is not None and len(rsi) >= 2 else None

        # --- Scoring ---
        confidence = 0.0
        action = Action.HOLD
        reasons = []

        # --- BUY Setup (Uptrend + Pullback) ---
        if uptrend:
            reasons.append("EMA 50 > 200 (uptrend)")
            confidence += 0.20

            if strong_trend:
                confidence += 0.20
                reasons.append(f"ADX {adx_val:.1f} > {ADX_THRESHOLD} (เทรนด์แรง)")

                if plus_di and minus_di and plus_di > minus_di:
                    confidence += 0.05
                    reasons.append("+DI > -DI (bullish momentum)")

            if is_pullback:
                confidence += 0.20
                reasons.append(f"Pullback ถึง EMA 50 (ห่าง {distance_to_ema50:.{profile.digits}f})")

                # ราคาเด้งขึ้นจาก EMA
                if current_close > ema50_val:
                    confidence += 0.10
                    reasons.append("ราคาเด้งขึ้นจาก EMA 50 (bullish bounce)")

                # RSI bounce confirmation: RSI crossing above 40
                if rsi_prev is not None and rsi_val is not None:
                    if rsi_prev < 40 and rsi_val >= 40:
                        confidence += 0.10
                        reasons.append(f"RSI bounce {rsi_prev:.1f}→{rsi_val:.1f} (cross 40)")

            # RSI ไม่ overbought จัด
            if rsi_val is not None and 30 < rsi_val < 65:
                confidence += 0.10
                reasons.append(f"RSI {rsi_val:.1f} (ไม่ overbought)")

            if confidence >= MIN_CONFIDENCE:
                action = Action.BUY

        # --- SELL Setup (Downtrend + Pullback) ---
        elif downtrend:
            reasons.append("EMA 50 < 200 (downtrend)")
            confidence += 0.20

            if strong_trend:
                confidence += 0.20
                reasons.append(f"ADX {adx_val:.1f} > {ADX_THRESHOLD} (เทรนด์แรง)")

                if plus_di and minus_di and minus_di > plus_di:
                    confidence += 0.05
                    reasons.append("-DI > +DI (bearish momentum)")

            if is_pullback:
                confidence += 0.20
                reasons.append(f"Pullback ถึง EMA 50 (ห่าง {distance_to_ema50:.{profile.digits}f})")

                if current_close < ema50_val:
                    confidence += 0.10
                    reasons.append("ราคาเด้งลงจาก EMA 50 (bearish rejection)")

                # RSI bounce confirmation: RSI crossing below 60
                if rsi_prev is not None and rsi_val is not None:
                    if rsi_prev > 60 and rsi_val <= 60:
                        confidence += 0.10
                        reasons.append(f"RSI rejection {rsi_prev:.1f}→{rsi_val:.1f} (cross 60)")

            if rsi_val is not None and 35 < rsi_val < 70:
                confidence += 0.10
                reasons.append(f"RSI {rsi_val:.1f} (ไม่ oversold)")

            if confidence >= min_conf:
                action = Action.SELL

        # --- ไม่ผ่าน threshold ---
        confidence = max(0.0, min(1.0, confidence))

        if action == Action.HOLD or confidence < min_conf:
            return self.create_hold(
                symbol=symbol,
                reason=f"Confidence {confidence:.2f} < {min_conf} | " + "; ".join(reasons) if reasons else "ไม่ใช่ trending market",
            )

        # --- Regime bonus ---
        if regime in self.suitable_regimes:
            confidence = min(1.0, confidence + 0.05)

        # --- ATR-based SL/TP ---
        default_sl_mult = _trend_sl_mult(symbol)
        sl_mult = kwargs.get("sl_atr_mult", default_sl_mult)
        rr_target = kwargs.get("rr_target", RR_TARGET)

        sl_distance = atr_val * sl_mult
        tp_distance = sl_distance * rr_target

        if action == Action.BUY:
            # SL ใต้ EMA 50 (swing support)
            stop_loss = round(min(ema50_val - atr_val * 0.5, current_close - sl_distance), profile.digits)
            take_profit = round(current_close + tp_distance, profile.digits)
        else:
            stop_loss = round(max(ema50_val + atr_val * 0.5, current_close + sl_distance), profile.digits)
            take_profit = round(current_close - tp_distance, profile.digits)

        actual_sl_dist = abs(current_close - stop_loss)
        rr = (tp_distance / actual_sl_dist) if actual_sl_dist > 0 else 0
        reason_text = "; ".join(reasons)

        logger.debug("trend_rider_signal", extra={
            "symbol": symbol, "action": action.value,
            "confidence": round(confidence, 3),
            "adx": round(adx_val, 1) if adx_val else None,
            "rr": round(rr, 1), "atr": round(atr_val, profile.digits),
        })

        return Decision(
            symbol=symbol, action=action, confidence=round(confidence, 3),
            reason=reason_text, stop_loss=stop_loss, take_profit=take_profit,
            risk_reward_ratio=round(rr, 2), strategy_name=self.name,
            timeframe=self.timeframe, tags=["trend_rider", "ema_cross", "pullback"],
            debug={
                "ema50": round(ema50_val, profile.digits),
                "ema200": round(ema200_val, profile.digits),
                "adx": round(adx_val, 1) if adx_val else None,
                "rsi": round(rsi_val, 1),
                "atr": round(atr_val, profile.digits),
                "distance_to_ema50": round(distance_to_ema50, profile.digits),
                "is_pullback": is_pullback,
            },
        )
