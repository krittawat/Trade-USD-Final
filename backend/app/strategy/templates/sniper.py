"""
Sniper Strategy — เข้าแม่น ออก RR สูง.

Logic:
    1. Market Structure — ตรวจ Higher Highs/Higher Lows (uptrend) หรือ Lower Highs/Lower Lows (downtrend)
    2. Order Block Detection — หาโซนที่ราคาเด้งแรง (potential demand/supply zone)
    3. Fair Value Gap (FVG) — หาช่องว่างราคาที่ยังไม่ถูก fill
    4. Confluence Scoring — ยิ่งมี confluence มาก confidence ยิ่งสูง
    5. EMA 200 filter — ใช้เป็น trend direction

ลักษณะ:
    - ไทม์เฟรม: M15
    - เป้าหมาย: RR 1:3+
    - เทรดน้อย accuracy สูง
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
EMA_TREND = 200      # EMA สำหรับ trend direction
RSI_PERIOD = 14      # RSI period
ATR_PERIOD = 14      # ATR period
ATR_SL_MULT = 2.0    # SL = ATR × mult (default — Gold/BTC)
RR_TARGET = 2.5      # เป้า RR (balanced: lower than 3.0 for higher WR, but not too low)

# --- Per-Asset-Class SL Adjustment ---
FOREX_PREFIXES = ("EUR", "GBP", "USD", "AUD", "NZD", "CAD", "CHF", "JPY")

def _sniper_sl_mult(symbol: str) -> float:
    """Forex M15 needs wider SL to survive noise — 3.0 vs default 2.0."""
    s = symbol.upper()
    if any(s.startswith(p) for p in FOREX_PREFIXES):
        return 3.0   # Forex M15: wider SL
    return ATR_SL_MULT  # Gold/BTC default
SWING_LOOKBACK = 10  # lookback bars สำหรับ swing high/low
FVG_MIN_GAP = 0.5    # ขนาด FVG ขั้นต่ำ (หน่วย ATR)
MIN_CONFIDENCE = 0.58 # confidence ขั้นต่ำ (slightly lower for more entries with added confluence)


class SniperStrategy(BaseStrategy):
    """
    Sniper Strategy — เข้าแม่น ออก RR สูง.

    ใช้ Market Structure + Order Block + FVG + EMA 200.
    เน้นคุณภาพ entry > จำนวนเทรด.
    """

    name = "sniper"
    timeframe = "M15"
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
        วิเคราะห์สัญญาณ sniper — หา high-precision entry.

        ต้องมี confluence ≥ 2 อย่างถึงจะเข้า:
            - Market structure aligned
            - Price near order block / FVG
            - EMA 200 confirms direction
            - RSI not extreme
        """
        symbol = profile.symbol

        # --- อ่านค่า Overrides จาก kwargs (AI Brain) ---
        ema_trend_len = kwargs.get("ema_fast", EMA_TREND)  # Aliasing ema_fast to ema_trend for sniper
        rsi_len = kwargs.get("rsi_period", RSI_PERIOD)
        atr_len = kwargs.get("atr_period", ATR_PERIOD)
        min_conf = kwargs.get("confidence_min", MIN_CONFIDENCE)

        # --- ตรวจข้อมูล ---
        if candles is None or len(candles) < ema_trend_len + 5:
            return self.create_hold(
                symbol=symbol,
                reason=f"ข้อมูลไม่เพียงพอ (ต้อง {ema_trend_len + 5} bars, มี {len(candles) if candles is not None else 0})",
            )

        # --- Indicators ---
        ema200 = ta.ema(candles["close"], length=ema_trend_len)
        rsi = ta.rsi(candles["close"], length=rsi_len)
        atr = ta.atr(candles["high"], candles["low"], candles["close"], length=atr_len)

        current_close = candles["close"].iloc[-1]
        current_high = candles["high"].iloc[-1]
        current_low = candles["low"].iloc[-1]
        ema200_val = ema200.iloc[-1] if ema200 is not None else None
        rsi_val = rsi.iloc[-1] if rsi is not None else 50.0
        atr_val = atr.iloc[-1] if atr is not None else None

        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in [ema200_val, atr_val]):
            return self.create_hold(symbol=symbol, reason="Indicators NaN — ต้องการข้อมูลเพิ่ม")

        # --- 1. Market Structure (Swing Highs/Lows) ---
        highs = candles["high"].iloc[-SWING_LOOKBACK * 3:]
        lows = candles["low"].iloc[-SWING_LOOKBACK * 3:]

        recent_swing_highs = self._find_swing_highs(highs, lookback=SWING_LOOKBACK)
        recent_swing_lows = self._find_swing_lows(lows, lookback=SWING_LOOKBACK)

        structure_bullish = self._is_higher_highs_lows(recent_swing_highs, recent_swing_lows)
        structure_bearish = self._is_lower_highs_lows(recent_swing_highs, recent_swing_lows)

        # --- 2. Fair Value Gap (FVG) ---
        fvg_bullish, fvg_bearish = self._detect_fvg(candles, atr_val)

        # --- 3. Order Block (simplified: look for rejection candles) ---
        ob_bullish = self._detect_bullish_ob(candles)
        ob_bearish = self._detect_bearish_ob(candles)

        # --- Confluence scoring ---
        confidence = 0.0
        action = Action.HOLD
        reasons = []

        # BUY setup
        buy_score = 0.0
        if current_close > ema200_val:
            buy_score += 0.20
            reasons.append("ราคา > EMA 200 (bullish trend)")
        if structure_bullish:
            buy_score += 0.25
            reasons.append("Higher Highs + Higher Lows (bullish structure)")
        if fvg_bullish:
            buy_score += 0.15
            reasons.append("Bullish FVG detected")
        if ob_bullish:
            buy_score += 0.15
            reasons.append("Bullish Order Block (demand zone)")
        if 30 < rsi_val < 60:
            buy_score += 0.10
            reasons.append(f"RSI {rsi_val:.1f} ในโซน pullback")

        # SELL setup
        sell_score = 0.0
        if current_close < ema200_val:
            sell_score += 0.20
        if structure_bearish:
            sell_score += 0.25
        if fvg_bearish:
            sell_score += 0.15
        if ob_bearish:
            sell_score += 0.15
        if 40 < rsi_val < 70:
            sell_score += 0.10

        # Volume spike detection (+0.10 per side)
        if "volume" in candles.columns:
            vol_ma = candles["volume"].rolling(window=20).mean()
            if vol_ma is not None and not pd.isna(vol_ma.iloc[-1]) and vol_ma.iloc[-1] > 0:
                vol_ratio = candles["volume"].iloc[-1] / vol_ma.iloc[-1]
                if vol_ratio > 1.5:
                    buy_score += 0.10
                    sell_score += 0.10

        # Engulfing candle confirmation (+0.10 per matching side)
        if len(candles) >= 2:
            prev_c = candles.iloc[-2]
            curr_c = candles.iloc[-1]
            # Bullish engulfing
            if (prev_c["close"] < prev_c["open"] and
                curr_c["close"] > curr_c["open"] and
                curr_c["close"] > prev_c["open"] and
                curr_c["open"] <= prev_c["close"]):
                buy_score += 0.10
                reasons.append("Bullish engulfing")
            # Bearish engulfing
            if (prev_c["close"] > prev_c["open"] and
                curr_c["close"] < curr_c["open"] and
                curr_c["close"] < prev_c["open"] and
                curr_c["open"] >= prev_c["close"]):
                sell_score += 0.10

        # --- ตัดสินใจ ---
        if buy_score > sell_score and buy_score >= min_conf:
            action = Action.BUY
            confidence = min(1.0, buy_score)
        elif sell_score > buy_score and sell_score >= min_conf:
            action = Action.SELL
            confidence = min(1.0, sell_score)
            reasons = [
                f"ราคา < EMA 200" if current_close < ema200_val else "",
                "Lower Highs + Lower Lows" if structure_bearish else "",
                "Bearish FVG" if fvg_bearish else "",
                "Bearish OB" if ob_bearish else "",
                f"RSI {rsi_val:.1f}" if 40 < rsi_val < 70 else "",
            ]
            reasons = [r for r in reasons if r]
        else:
            return self.create_hold(
                symbol=symbol,
                reason=f"Confluence ไม่เพียงพอ (buy={buy_score:.2f}, sell={sell_score:.2f}, min={min_conf})",
            )

        # --- Regime bonus ---
        if regime in self.suitable_regimes:
            confidence = min(1.0, confidence + 0.05)

        # --- ATR-based SL/TP (wide SL, 3R TP) ---
        default_sl_mult = _sniper_sl_mult(symbol)
        sl_mult = kwargs.get("sl_atr_mult", default_sl_mult)
        rr_target = kwargs.get("rr_target", RR_TARGET)

        sl_distance = atr_val * sl_mult
        tp_distance = sl_distance * rr_target

        if action == Action.BUY:
            # SL ใต้ swing low ล่าสุด (ถ้ามี) หรือ ATR-based
            swing_low = recent_swing_lows[-1] if recent_swing_lows else current_close - sl_distance
            stop_loss = round(min(swing_low - atr_val * 0.2, current_close - sl_distance), profile.digits)
            take_profit = round(current_close + tp_distance, profile.digits)
        else:
            swing_high = recent_swing_highs[-1] if recent_swing_highs else current_close + sl_distance
            stop_loss = round(max(swing_high + atr_val * 0.2, current_close + sl_distance), profile.digits)
            take_profit = round(current_close - tp_distance, profile.digits)

        rr = tp_distance / sl_distance if sl_distance > 0 else 0
        reason_text = "; ".join(reasons)

        logger.debug("sniper_signal", extra={
            "symbol": symbol, "action": action.value,
            "confidence": round(confidence, 3),
            "rr": round(rr, 1), "atr": round(atr_val, profile.digits),
        })

        return Decision(
            symbol=symbol, action=action, confidence=round(confidence, 3),
            reason=reason_text, stop_loss=stop_loss, take_profit=take_profit,
            risk_reward_ratio=round(rr, 2), strategy_name=self.name,
            timeframe=self.timeframe, tags=["sniper", "structure", "fvg"],
            debug={
                "ema200": round(ema200_val, profile.digits),
                "rsi": round(rsi_val, 1), "atr": round(atr_val, profile.digits),
                "structure_bullish": structure_bullish, "structure_bearish": structure_bearish,
                "fvg_bullish": fvg_bullish, "fvg_bearish": fvg_bearish,
            },
        )

    # ====================================================================
    # Helper methods
    # ====================================================================

    def _find_swing_highs(self, highs: pd.Series, lookback: int = 5) -> list[float]:
        """หา swing highs (จุดสูงสุดในช่วง lookback bars)."""
        swing_highs = []
        for i in range(lookback, len(highs) - lookback):
            if highs.iloc[i] == highs.iloc[i - lookback:i + lookback + 1].max():
                swing_highs.append(highs.iloc[i])
        return swing_highs[-3:]  # เก็บแค่ 3 ตัวล่าสุด

    def _find_swing_lows(self, lows: pd.Series, lookback: int = 5) -> list[float]:
        """หา swing lows (จุดต่ำสุดในช่วง lookback bars)."""
        swing_lows = []
        for i in range(lookback, len(lows) - lookback):
            if lows.iloc[i] == lows.iloc[i - lookback:i + lookback + 1].min():
                swing_lows.append(lows.iloc[i])
        return swing_lows[-3:]

    def _is_higher_highs_lows(self, highs: list, lows: list) -> bool:
        """ตรวจว่า swing highs/lows เพิ่มขึ้น (bullish structure)."""
        if len(highs) < 2 or len(lows) < 2:
            return False
        return highs[-1] > highs[-2] and lows[-1] > lows[-2]

    def _is_lower_highs_lows(self, highs: list, lows: list) -> bool:
        """ตรวจว่า swing highs/lows ลดลง (bearish structure)."""
        if len(highs) < 2 or len(lows) < 2:
            return False
        return highs[-1] < highs[-2] and lows[-1] < lows[-2]

    def _detect_fvg(self, candles: pd.DataFrame, atr: float) -> tuple[bool, bool]:
        """ตรวจ Fair Value Gap (FVG) ใน 3 bars ล่าสุด."""
        if len(candles) < 3:
            return False, False

        # Bullish FVG: bar[-3].high < bar[-1].low (ช่องว่างขึ้น)
        bullish = candles["high"].iloc[-3] < candles["low"].iloc[-1]
        gap_size_bull = candles["low"].iloc[-1] - candles["high"].iloc[-3]

        # Bearish FVG: bar[-3].low > bar[-1].high (ช่องว่างลง)
        bearish = candles["low"].iloc[-3] > candles["high"].iloc[-1]
        gap_size_bear = candles["low"].iloc[-3] - candles["high"].iloc[-1]

        min_gap = atr * FVG_MIN_GAP if atr > 0 else 0

        return (bullish and gap_size_bull > min_gap), (bearish and gap_size_bear > min_gap)

    def _detect_bullish_ob(self, candles: pd.DataFrame) -> bool:
        """ตรวจ Bullish Order Block (simplified: bearish candle ก่อน bullish move)."""
        if len(candles) < 5:
            return False
        # หาแท่ง bearish ที่ตามด้วย bullish move แรง
        for i in range(-5, -2):
            if (candles["close"].iloc[i] < candles["open"].iloc[i] and  # bearish candle
                candles["close"].iloc[i + 1] > candles["open"].iloc[i + 1] and  # bullish
                candles["close"].iloc[i + 1] > candles["high"].iloc[i]):  # break above
                return True
        return False

    def _detect_bearish_ob(self, candles: pd.DataFrame) -> bool:
        """ตรวจ Bearish Order Block (simplified: bullish candle ก่อน bearish move)."""
        if len(candles) < 5:
            return False
        for i in range(-5, -2):
            if (candles["close"].iloc[i] > candles["open"].iloc[i] and  # bullish candle
                candles["close"].iloc[i + 1] < candles["open"].iloc[i + 1] and  # bearish
                candles["close"].iloc[i + 1] < candles["low"].iloc[i]):  # break below
                return True
        return False
