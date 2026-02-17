"""
Scalping Strategy — เทรดสั้น กำไรเร็ว.

Logic:
    1. EMA 8/21 crossover → ทิศทาง
    2. RSI filter → ไม่เข้าที่ overbought/oversold สุดโต่ง
    3. VWAP → ใช้เป็น reference ทิศทาง (ราคา > VWAP = bullish bias)
    4. Volume confirmation → ต้องมี volume สูงกว่า average

ลักษณะ:
    - ไทม์เฟรม: M5
    - เป้าหมาย: กำไรเร็ว, RR 1:1.5
    - เซสชัน: London, NY, Overlap
    - ATR-based SL/TP
"""

import pandas as pd
import pandas_ta as ta

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)

# --- Parameters ---
EMA_FAST = 8       # EMA เร็ว
EMA_SLOW = 21      # EMA ช้า
RSI_PERIOD = 14    # RSI period
RSI_OB = 75        # RSI overbought — ห้ามซื้อ
RSI_OS = 25        # RSI oversold — ห้ามขาย
ATR_PERIOD = 14    # ATR สำหรับคำนวณ SL/TP
ATR_SL_MULT = 2.0  # SL = ATR × multiplier (was 1.5 — too tight, caused constant stop-outs)
RR_RATIO = 2.0     # Risk:Reward ratio (was 1.5 — now TP=4.0×ATR for profitable edge)
VOL_MA = 20        # Volume MA period
MIN_CONFIDENCE = 0.55  # ค่าต่ำสุดที่จะเข้าเทรด


class ScalpingStrategy(BaseStrategy):
    """
    Scalping Strategy — เทรดสั้น กำไรเร็ว.

    ใช้ EMA crossover + RSI filter + VWAP reference.
    SL/TP คำนวณจาก ATR (dynamic ตามความผันผวน).
    """

    name = "scalping"
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
    ) -> Decision:
        """
        วิเคราะห์สัญญาณ scalping.

        ขั้นตอน:
            1. คำนวณ indicators (EMA, RSI, ATR, Volume MA)
            2. ตรวจ EMA crossover
            3. ตรวจ RSI filter (ไม่ extreme)
            4. ตรวจ VWAP bias
            5. คำนวณ confidence score
            6. ถ้า confidence เพียงพอ → BUY/SELL + ATR-based SL/TP
        """
        symbol = profile.symbol

        # --- ตรวจข้อมูลเพียงพอ ---
        if candles is None or len(candles) < max(EMA_SLOW, ATR_PERIOD, VOL_MA) + 5:
            return self.create_hold(
                symbol=symbol,
                reason=f"ข้อมูล candle ไม่เพียงพอ (ต้อง ≥ {EMA_SLOW + 5} bars, มี {len(candles) if candles is not None else 0})",
            )

        # --- คำนวณ Indicators ---
        ema_fast = ta.ema(candles["close"], length=EMA_FAST)
        ema_slow = ta.ema(candles["close"], length=EMA_SLOW)
        rsi = ta.rsi(candles["close"], length=RSI_PERIOD)
        atr = ta.atr(candles["high"], candles["low"], candles["close"], length=ATR_PERIOD)

        # Volume MA
        vol_ma = candles["volume"].rolling(window=VOL_MA).mean() if "volume" in candles.columns else None

        # ดึงค่าปัจจุบัน
        current_close = candles["close"].iloc[-1]
        ema_f = ema_fast.iloc[-1] if ema_fast is not None else None
        ema_s = ema_slow.iloc[-1] if ema_slow is not None else None
        rsi_val = rsi.iloc[-1] if rsi is not None else 50.0
        atr_val = atr.iloc[-1] if atr is not None else None

        # ค่าก่อนหน้า (สำหรับ crossover detection)
        ema_f_prev = ema_fast.iloc[-2] if ema_fast is not None and len(ema_fast) >= 2 else None
        ema_s_prev = ema_slow.iloc[-2] if ema_slow is not None and len(ema_slow) >= 2 else None

        # --- ตรวจ NaN ---
        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in [ema_f, ema_s, rsi_val, atr_val]):
            return self.create_hold(
                symbol=symbol,
                reason="Indicator values เป็น NaN — ต้องการข้อมูลเพิ่ม",
            )

        # --- VWAP (optional — ถ้ามี DatetimeIndex) ---
        vwap_val = None
        try:
            if isinstance(candles.index, pd.DatetimeIndex):
                vwap = ta.vwap(candles["high"], candles["low"], candles["close"], candles["volume"])
                if vwap is not None:
                    vwap_val = vwap.iloc[-1]
        except Exception:
            pass  # VWAP ไม่ critical — ใช้เป็น bias เสริมเท่านั้น

        # --- สัญญาณ ---
        confidence = 0.0
        action = Action.HOLD
        reasons = []

        # 1. EMA Crossover Detection
        bullish_cross = (ema_f_prev is not None and ema_s_prev is not None and
                        ema_f_prev <= ema_s_prev and ema_f > ema_s)
        bearish_cross = (ema_f_prev is not None and ema_s_prev is not None and
                        ema_f_prev >= ema_s_prev and ema_f < ema_s)

        # 2. EMA Position (ราคาอยู่เหนือ/ใต้ EMA)
        price_above_ema = current_close > ema_f > ema_s
        price_below_ema = current_close < ema_f < ema_s

        # --- BUY Logic ---
        if bullish_cross or price_above_ema:
            action = Action.BUY
            confidence += 0.30 if bullish_cross else 0.15
            reasons.append("EMA 8 cross above 21" if bullish_cross else "ราคาเหนือ EMA 8 > 21")

            # RSI filter — ห้ามซื้อที่ overbought
            if rsi_val < RSI_OB:
                confidence += 0.20
                reasons.append(f"RSI {rsi_val:.1f} < {RSI_OB} (ไม่ overbought)")
            else:
                confidence -= 0.15
                reasons.append(f"RSI {rsi_val:.1f} ≥ {RSI_OB} (overbought — ลด confidence)")

            # VWAP bias
            if vwap_val is not None and current_close > vwap_val:
                confidence += 0.15
                reasons.append("ราคา > VWAP (bullish bias)")

            # Volume confirmation
            if vol_ma is not None and not pd.isna(vol_ma.iloc[-1]):
                current_vol = candles["volume"].iloc[-1]
                if current_vol > vol_ma.iloc[-1]:
                    confidence += 0.10
                    reasons.append("Volume > MA (confirmed)")

        # --- SELL Logic ---
        elif bearish_cross or price_below_ema:
            action = Action.SELL
            confidence += 0.30 if bearish_cross else 0.15
            reasons.append("EMA 8 cross below 21" if bearish_cross else "ราคาใต้ EMA 8 < 21")

            # RSI filter — ห้ามขายที่ oversold
            if rsi_val > RSI_OS:
                confidence += 0.20
                reasons.append(f"RSI {rsi_val:.1f} > {RSI_OS} (ไม่ oversold)")
            else:
                confidence -= 0.15
                reasons.append(f"RSI {rsi_val:.1f} ≤ {RSI_OS} (oversold — ลด confidence)")

            # VWAP bias
            if vwap_val is not None and current_close < vwap_val:
                confidence += 0.15
                reasons.append("ราคา < VWAP (bearish bias)")

            # Volume confirmation
            if vol_ma is not None and not pd.isna(vol_ma.iloc[-1]):
                current_vol = candles["volume"].iloc[-1]
                if current_vol > vol_ma.iloc[-1]:
                    confidence += 0.10
                    reasons.append("Volume > MA (confirmed)")

        # --- Regime bonus ---
        if regime in self.suitable_regimes:
            confidence += 0.10
            reasons.append(f"Regime: {regime.value} (เหมาะสม)")

        # --- Confidence ต่ำเกินไป → HOLD ---
        confidence = max(0.0, min(1.0, confidence))

        if confidence < MIN_CONFIDENCE or action == Action.HOLD:
            return self.create_hold(
                symbol=symbol,
                reason=f"Confidence {confidence:.2f} < {MIN_CONFIDENCE} | " + "; ".join(reasons) if reasons else "ไม่มีสัญญาณ",
            )

        # --- คำนวณ SL/TP จาก ATR ---
        sl_distance = atr_val * ATR_SL_MULT
        tp_distance = sl_distance * RR_RATIO

        if action == Action.BUY:
            stop_loss = round(current_close - sl_distance, profile.digits)
            take_profit = round(current_close + tp_distance, profile.digits)
        else:  # SELL
            stop_loss = round(current_close + sl_distance, profile.digits)
            take_profit = round(current_close - tp_distance, profile.digits)

        rr = tp_distance / sl_distance if sl_distance > 0 else 0

        reason_text = "; ".join(reasons)

        logger.info("scalping_signal", extra={
            "symbol": symbol,
            "action": action.value,
            "confidence": round(confidence, 3),
            "sl": stop_loss,
            "tp": take_profit,
            "atr": round(atr_val, profile.digits),
            "rsi": round(rsi_val, 1),
            "reason": reason_text,
        })

        return Decision(
            symbol=symbol,
            action=action,
            confidence=round(confidence, 3),
            reason=reason_text,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=round(rr, 2),
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["scalping", "ema_crossover"],
            debug={
                "ema_fast": round(ema_f, profile.digits),
                "ema_slow": round(ema_s, profile.digits),
                "rsi": round(rsi_val, 1),
                "atr": round(atr_val, profile.digits),
                "vwap": round(vwap_val, profile.digits) if vwap_val else None,
                "sl_distance": round(sl_distance, profile.digits),
            },
        )
