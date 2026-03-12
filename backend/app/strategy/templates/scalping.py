"""
Scalping Strategy — เทรดสั้น กำไรเร็ว (V2 Enhanced).

Logic (7-Layer Confluence):
    1. EMA 8/21 crossover + momentum slope → ทิศทาง + ความแรง
    2. RSI filter → ไม่เข้าที่ overbought/oversold สุดโต่ง
    3. Stochastic RSI → entry timing precision
    4. VWAP → ใช้เป็น reference ทิศทาง (robust calculation)
    5. Volume confirmation → ต้องมี volume สูงกว่า average
    6. H1 Multi-Timeframe → trend alignment confirmation
    7. Regime + Session → context scoring

ลักษณะ:
    - ไทม์เฟรม: M5 (+ H1 MTF)
    - เป้าหมาย: กำไรเร็ว, RR 1:2.0-2.5
    - เซสชัน: London, NY, Overlap
    - ATR-based SL/TP

Changes V2 (11-Feb-2026):
    - Enhanced confidence scoring (more granular, reach 0.65 without crossover)
    - EMA momentum/slope gradient scoring
    - H1 MTF trend confirmation
    - Stochastic RSI for entry timing
    - Robust VWAP that works without DatetimeIndex
    - Rebalanced penalties (less harsh)
"""

import pandas as pd
import pandas_ta as ta
import app.analysis.indicators as ind
import numpy as np

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
VOL_MA = 20        # Volume MA period
MIN_CONFIDENCE = 0.65  # ค่าต่ำสุดที่จะเข้าเทรด

# Stochastic RSI parameters
STOCH_RSI_PERIOD = 14
STOCH_RSI_K = 3
STOCH_RSI_D = 3
STOCH_RSI_OB = 80  # overbought zone
STOCH_RSI_OS = 20  # oversold zone

# EMA Momentum parameters
EMA_SLOPE_LOOKBACK = 5  # bars to measure slope

# --- Per-Asset-Class SL/TP Settings ---
# Forex M5 ATR is tiny (~6 pips), need wider multiplier to avoid noise stop-outs
# Spread on Forex can be 12%+ of SL at 2×ATR — unacceptable
FOREX_PREFIXES = ("EUR", "GBP", "USD", "AUD", "NZD", "CAD", "CHF", "JPY")

def _get_sl_rr(symbol: str) -> tuple[float, float]:
    """Return (sl_atr_mult, rr_ratio) based on asset class."""
    s = symbol.upper()
    # Forex pairs: wider SL to survive M5 noise + spread cost
    if any(s.startswith(p) for p in FOREX_PREFIXES):
        return 3.5, 2.5   # SL=3.5×ATR (~12 pips), TP=8.75×ATR (~30 pips)
    # BTC/Crypto: wider SL for high vol
    if "BTC" in s or "ETH" in s:
        return 2.5, 2.0   # SL=2.5×ATR, TP=5×ATR
    # Gold/Silver/Default
    return 2.0, 2.0       # SL=2×ATR, TP=4×ATR


def _calc_robust_vwap(candles: pd.DataFrame) -> float | None:
    """
    Calculate VWAP robustly — works with or without DatetimeIndex.

    Uses rolling session VWAP (last 50 bars) as approximation
    when proper session-based VWAP isn't available.
    """
    try:
        # Try proper VWAP first (needs DatetimeIndex)
        if isinstance(candles.index, pd.DatetimeIndex):
            vwap = ta.vwap(candles["high"], candles["low"], candles["close"], candles["volume"])
            if vwap is not None and not pd.isna(vwap.iloc[-1]):
                return float(vwap.iloc[-1])

        # Fallback: rolling VWAP (typical price × volume / cumulative volume)
        if "volume" not in candles.columns:
            return None

        window = min(50, len(candles))
        recent = candles.tail(window)
        vol = recent["volume"]
        if vol.sum() <= 0:
            return None

        typical_price = (recent["high"] + recent["low"] + recent["close"]) / 3
        vwap_val = float((typical_price * vol).sum() / vol.sum())
        return vwap_val
    except Exception:
        return None


def _calc_ema_momentum(ema_series: pd.Series, atr_val: float, lookback: int = EMA_SLOPE_LOOKBACK) -> float:
    """
    Calculate EMA momentum as slope normalized by ATR.

    Returns:
        float: momentum score (positive = bullish, negative = bearish)
               abs value > 1.0 = strong momentum
               abs value > 2.0 = very strong momentum
    """
    if ema_series is None or len(ema_series) < lookback + 1 or atr_val <= 0:
        return 0.0
    slope = float(ema_series.iloc[-1] - ema_series.iloc[-lookback])
    return slope / atr_val


def _check_h1_trend(h1_candles: pd.DataFrame | None) -> dict:
    """
    Check H1 timeframe trend alignment.

    Returns:
        dict with keys:
            - direction: "UP", "DOWN", or "NEUTRAL"
            - adx: H1 ADX value
            - aligned_buy: True if H1 supports buy
            - aligned_sell: True if H1 supports sell
    """
    result = {"direction": "NEUTRAL", "adx": 0.0, "aligned_buy": False, "aligned_sell": False}

    if h1_candles is None or len(h1_candles) < 30:
        return result

    try:
        ema20 = ta.ema(h1_candles["close"], length=20)
        if ema20 is None or pd.isna(ema20.iloc[-1]):
            return result

        # H1 EMA slope (last 3 bars on H1 = ~3 hours)
        if len(ema20) >= 4:
            slope = float(ema20.iloc[-1] - ema20.iloc[-3])
            close = float(h1_candles["close"].iloc[-1])
            ema_val = float(ema20.iloc[-1])

            if slope > 0 and close > ema_val:
                result["direction"] = "UP"
                result["aligned_buy"] = True
            elif slope < 0 and close < ema_val:
                result["direction"] = "DOWN"
                result["aligned_sell"] = True

        # H1 ADX
        adx_df = ta.adx(h1_candles["high"], h1_candles["low"], h1_candles["close"], length=14)
        if adx_df is not None and "ADX_14" in adx_df.columns:
            adx_val = adx_df["ADX_14"].iloc[-1]
            if not pd.isna(adx_val):
                result["adx"] = float(adx_val)
    except Exception:
        pass

    return result


def _calc_stoch_rsi(candles: pd.DataFrame) -> dict:
    """
    Calculate Stochastic RSI for entry timing.

    Returns:
        dict with keys:
            - k: StochRSI %K value (0-100)
            - d: StochRSI %D value (0-100)
            - bullish_cross: %K crossed above %D in oversold zone
            - bearish_cross: %K crossed below %D in overbought zone
            - oversold: in oversold zone
            - overbought: in overbought zone
    """
    result = {
        "k": 50.0, "d": 50.0,
        "bullish_cross": False, "bearish_cross": False,
        "oversold": False, "overbought": False,
    }

    try:
        stoch_rsi = ta.stochrsi(
            candles["close"],
            length=STOCH_RSI_PERIOD,
            rsi_length=RSI_PERIOD,
            k=STOCH_RSI_K,
            d=STOCH_RSI_D,
        )
        if stoch_rsi is None:
            return result

        # Get K and D columns
        k_col = [c for c in stoch_rsi.columns if "K" in c.upper()]
        d_col = [c for c in stoch_rsi.columns if "D" in c.upper()]

        if not k_col or not d_col:
            return result

        k_curr = stoch_rsi[k_col[0]].iloc[-1]
        d_curr = stoch_rsi[d_col[0]].iloc[-1]
        k_prev = stoch_rsi[k_col[0]].iloc[-2] if len(stoch_rsi) >= 2 else k_curr
        d_prev = stoch_rsi[d_col[0]].iloc[-2] if len(stoch_rsi) >= 2 else d_curr

        if pd.isna(k_curr) or pd.isna(d_curr):
            return result

        result["k"] = float(k_curr) * 100  # ta returns 0-1, scale to 0-100
        result["d"] = float(d_curr) * 100

        k_val = result["k"]
        d_val = result["d"]
        k_p = float(k_prev) * 100
        d_p = float(d_prev) * 100

        result["oversold"] = k_val < STOCH_RSI_OS
        result["overbought"] = k_val > STOCH_RSI_OB

        # Bullish cross: K crosses above D in oversold zone
        result["bullish_cross"] = (k_p <= d_p and k_val > d_val and k_val < 40)
        # Bearish cross: K crosses below D in overbought zone
        result["bearish_cross"] = (k_p >= d_p and k_val < d_val and k_val > 60)

    except Exception:
        pass

    return result


class ScalpingStrategy(BaseStrategy):
    """
    Scalping Strategy V2 — เทรดสั้น กำไรเร็ว (Enhanced Confidence Scoring).

    7-Layer Confluence:
        1. EMA crossover + momentum slope
        2. RSI filter
        3. Stochastic RSI timing
        4. VWAP reference (robust)
        5. Volume confirmation
        6. H1 MTF trend alignment
        7. Regime + session context

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
        **kwargs,
    ) -> Decision:
        """
        วิเคราะห์สัญญาณ scalping (V2 Enhanced).

        7-Layer Confluence Scoring:
            1. EMA position/crossover + momentum slope
            2. RSI filter (not extreme)
            3. Stochastic RSI entry timing
            4. VWAP bias (robust calculation)
            5. Volume confirmation
            6. H1 Multi-Timeframe alignment
            7. Regime + session context
        """
        symbol = profile.symbol

        # --- อ่านค่า Overrides จาก kwargs (AI Brain) ---
        ema_fast_len = kwargs.get("ema_fast", EMA_FAST)
        ema_slow_len = kwargs.get("ema_slow", EMA_SLOW)
        rsi_len = kwargs.get("rsi_period", RSI_PERIOD)
        min_conf = kwargs.get("confidence_min", MIN_CONFIDENCE)

        # --- ตรวจข้อมูลเพียงพอ ---
        if candles is None or len(candles) < max(ema_slow_len, ATR_PERIOD, VOL_MA) + 5:
            return self.create_hold(
                symbol=symbol,
                reason=f"ข้อมูล candle ไม่เพียงพอ (ต้อง ≥ {ema_slow_len + 5} bars, มี {len(candles) if candles is not None else 0})",
            )

        # --- คำนวณ Indicators ---
        ema_fast = ta.ema(candles["close"], length=ema_fast_len)
        ema_slow = ta.ema(candles["close"], length=ema_slow_len)
        rsi = ta.rsi(candles["close"], length=rsi_len)
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
        if any(v is None or pd.isna(v) for v in [ema_f, ema_s, rsi_val, atr_val]):
            return self.create_hold(
                symbol=symbol,
                reason="Indicator values เป็น NaN — ต้องการข้อมูลเพิ่ม",
            )

        # --- Layer 4: VWAP (robust — works without DatetimeIndex) ---
        vwap_val = _calc_robust_vwap(candles)

        # --- Layer 3: Stochastic RSI ---
        stoch = _calc_stoch_rsi(candles)

        # --- Layer 1b: EMA Momentum/Slope ---
        ema_momentum = _calc_ema_momentum(ema_fast, atr_val)

        # --- Layer 6: H1 Multi-Timeframe ---
        h1_candles = kwargs.get("h1_candles", None)
        h1_trend = _check_h1_trend(h1_candles)

        # --- Candle Quality Filter (reject doji/spinning top) ---
        curr_candle = candles.iloc[-1]
        candle_body = abs(curr_candle["close"] - curr_candle["open"])
        candle_range = curr_candle["high"] - curr_candle["low"]
        is_weak_candle = candle_range > 0 and (candle_body / candle_range) < 0.30

        # --- Session awareness for Forex ---
        session_bonus = 0.0
        s = symbol.upper()
        is_forex = any(s.startswith(p) for p in FOREX_PREFIXES)
        if is_forex:
            try:
                candle_time = candles.iloc[-1].get("time")
                if candle_time is not None and hasattr(candle_time, "hour"):
                    hour = candle_time.hour
                    if 13 <= hour < 16:  # London/NY overlap
                        session_bonus = 0.10
                    elif 7 <= hour < 13 or 16 <= hour < 21:  # London or NY
                        session_bonus = 0.05
                    elif 0 <= hour < 7:  # Asian — low vol for FX
                        session_bonus = -0.05  # V2: reduced from -0.10
            except Exception:
                pass

        # ===============================================
        # === CONFIDENCE SCORING (V2 Enhanced) ===
        # ===============================================
        confidence = 0.0
        action = Action.HOLD
        reasons = []

        # --- Layer 1a: EMA Crossover Detection ---
        bullish_cross = (ema_f_prev is not None and ema_s_prev is not None and
                        ema_f_prev <= ema_s_prev and ema_f > ema_s)
        bearish_cross = (ema_f_prev is not None and ema_s_prev is not None and
                        ema_f_prev >= ema_s_prev and ema_f < ema_s)

        # EMA Position (ราคาอยู่เหนือ/ใต้ EMA)
        price_above_ema = current_close > ema_f > ema_s
        price_below_ema = current_close < ema_f < ema_s

        # --- BUY Logic ---
        if bullish_cross or price_above_ema:
            action = Action.BUY

            # Layer 1a: EMA signal (+0.20 to +0.30)
            if bullish_cross:
                confidence += 0.30
                reasons.append("EMA 8 cross above 21")
            else:
                confidence += 0.20  # V2: up from 0.15
                reasons.append("ราคาเหนือ EMA 8 > 21")

            # Layer 1b: EMA Momentum bonus (+0.0 to +0.10)
            if ema_momentum > 1.5:
                confidence += 0.10
                reasons.append(f"Strong bullish momentum ({ema_momentum:.1f}×ATR)")
            elif ema_momentum > 0.5:
                confidence += 0.05
                reasons.append(f"Moderate momentum ({ema_momentum:.1f}×ATR)")

            # Layer 2: RSI filter — ห้ามซื้อที่ overbought
            if rsi_val < RSI_OB:
                confidence += 0.20
                reasons.append(f"RSI {rsi_val:.1f} < {RSI_OB} (ไม่ overbought)")
            else:
                confidence -= 0.15
                reasons.append(f"RSI {rsi_val:.1f} ≥ {RSI_OB} (overbought — ลด confidence)")

            # Layer 3: Stochastic RSI timing (+0.0 to +0.10)
            if stoch["bullish_cross"]:
                confidence += 0.10
                reasons.append(f"StochRSI bullish cross (K={stoch['k']:.0f})")
            elif stoch["oversold"]:
                confidence += 0.05
                reasons.append(f"StochRSI oversold zone (K={stoch['k']:.0f})")

            # Layer 4: VWAP bias
            if vwap_val is not None and current_close > vwap_val:
                confidence += 0.10
                reasons.append("ราคา > VWAP (bullish bias)")

            # Layer 5: Volume confirmation
            if vol_ma is not None and not pd.isna(vol_ma.iloc[-1]):
                current_vol = candles["volume"].iloc[-1]
                if current_vol > vol_ma.iloc[-1]:
                    confidence += 0.10
                    reasons.append("Volume > MA (confirmed)")

            # Layer 6: H1 MTF confirmation (+0.0 to +0.15)
            if h1_trend["aligned_buy"]:
                confidence += 0.10
                reasons.append("H1 trend aligned ↑")
                if h1_trend["adx"] > 25:
                    confidence += 0.05
                    reasons.append(f"H1 ADX {h1_trend['adx']:.0f} (strong trend)")

        # --- SELL Logic ---
        elif bearish_cross or price_below_ema:
            action = Action.SELL

            # Layer 1a: EMA signal (+0.20 to +0.30)
            if bearish_cross:
                confidence += 0.30
                reasons.append("EMA 8 cross below 21")
            else:
                confidence += 0.20  # V2: up from 0.15
                reasons.append("ราคาใต้ EMA 8 < 21")

            # Layer 1b: EMA Momentum bonus (+0.0 to +0.10)
            if ema_momentum < -1.5:
                confidence += 0.10
                reasons.append(f"Strong bearish momentum ({ema_momentum:.1f}×ATR)")
            elif ema_momentum < -0.5:
                confidence += 0.05
                reasons.append(f"Moderate momentum ({ema_momentum:.1f}×ATR)")

            # Layer 2: RSI filter — ห้ามขายที่ oversold
            if rsi_val > RSI_OS:
                confidence += 0.20
                reasons.append(f"RSI {rsi_val:.1f} > {RSI_OS} (ไม่ oversold)")
            else:
                confidence -= 0.15
                reasons.append(f"RSI {rsi_val:.1f} ≤ {RSI_OS} (oversold — ลด confidence)")

            # Layer 3: Stochastic RSI timing (+0.0 to +0.10)
            if stoch["bearish_cross"]:
                confidence += 0.10
                reasons.append(f"StochRSI bearish cross (K={stoch['k']:.0f})")
            elif stoch["overbought"]:
                confidence += 0.05
                reasons.append(f"StochRSI overbought zone (K={stoch['k']:.0f})")

            # Layer 4: VWAP bias
            if vwap_val is not None and current_close < vwap_val:
                confidence += 0.10
                reasons.append("ราคา < VWAP (bearish bias)")

            # Layer 5: Volume confirmation
            if vol_ma is not None and not pd.isna(vol_ma.iloc[-1]):
                current_vol = candles["volume"].iloc[-1]
                if current_vol > vol_ma.iloc[-1]:
                    confidence += 0.10
                    reasons.append("Volume > MA (confirmed)")

            # Layer 6: H1 MTF confirmation (+0.0 to +0.15)
            if h1_trend["aligned_sell"]:
                confidence += 0.10
                reasons.append("H1 trend aligned ↓")
                if h1_trend["adx"] > 25:
                    confidence += 0.05
                    reasons.append(f"H1 ADX {h1_trend['adx']:.0f} (strong trend)")

        # --- Layer 7a: Regime bonus ---
        if regime in self.suitable_regimes:
            confidence += 0.10
            reasons.append(f"Regime: {regime.value} (เหมาะสม)")

        # --- Layer 7b: Session bonus/penalty ---
        if session_bonus != 0:
            confidence += session_bonus
            if session_bonus > 0:
                reasons.append(f"Session bonus +{session_bonus:.2f}")
            else:
                reasons.append(f"Low-vol session penalty {session_bonus:.2f}")

        # --- Candle quality gate (V2: reduced penalty) ---
        if is_weak_candle and action != Action.HOLD:
            confidence -= 0.08  # V2: reduced from -0.15
            reasons.append("Weak candle (doji/spinning) penalty -0.08")

        # --- Confidence ต่ำเกินไป → HOLD ---
        confidence = max(0.0, min(1.0, confidence))

        if confidence < min_conf or action == Action.HOLD:
            hold_reason = (f"Confidence {confidence:.2f} < {min_conf} | " + "; ".join(reasons)) if reasons else "ไม่มีสัญญาณ"
            return self.create_hold(
                symbol=symbol,
                reason=hold_reason,
            )

        # --- คำนวณ SL/TP จาก ATR (per-asset-class) ---
        default_sl_mult, default_rr = _get_sl_rr(symbol)
        sl_mult = kwargs.get("sl_atr_mult", default_sl_mult)
        rr_target = kwargs.get("rr_target", default_rr)

        sl_distance = atr_val * sl_mult
        tp_distance = sl_distance * rr_target

        if action == Action.BUY:
            stop_loss = round(current_close - sl_distance, profile.digits)
            take_profit = round(current_close + tp_distance, profile.digits)
        else:  # SELL
            stop_loss = round(current_close + sl_distance, profile.digits)
            take_profit = round(current_close - tp_distance, profile.digits)

        rr = tp_distance / sl_distance if sl_distance > 0 else 0

        reason_text = "; ".join(reasons)

        logger.debug("scalping_signal", extra={
            "symbol": symbol,
            "action": action.value,
            "confidence": round(confidence, 3),
            "sl": stop_loss,
            "tp": take_profit,
            "atr": round(atr_val, profile.digits),
            "rsi": round(rsi_val, 1),
            "ema_momentum": round(ema_momentum, 2),
            "h1_trend": h1_trend["direction"],
            "stoch_k": round(stoch["k"], 0),
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
            tags=["scalping", "ema_crossover", "v2_enhanced"],
            debug={
                "ema_fast": round(ema_f, profile.digits),
                "ema_slow": round(ema_s, profile.digits),
                "rsi": round(rsi_val, 1),
                "atr": round(atr_val, profile.digits),
                "vwap": round(vwap_val, profile.digits) if vwap_val else None,
                "sl_distance": round(sl_distance, profile.digits),
                "ema_momentum": round(ema_momentum, 2),
                "h1_trend": h1_trend["direction"],
                "stoch_k": round(stoch["k"], 0),
                "stoch_d": round(stoch["d"], 0),
            },
        )
