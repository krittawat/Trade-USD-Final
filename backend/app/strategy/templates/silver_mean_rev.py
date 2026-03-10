"""
Silver Mean-Reversion Strategy (XAG-Specific)
==============================================
ออกแบบเฉพาะสำหรับ Silver (XAG) ซึ่งมี whipsaw สูงและ range-bound บ่อย
ใช้ Bollinger Bands + RSI mean-reversion แทน SuperTrend trend-following

แนวคิด: Silver มักสะท้อนกลับค่าเฉลี่ย → ซื้อเมื่อราคาถูก (ใต้ BB Lower) ขายเมื่อราคาแพง (เหนือ BB Upper)

Indicators:
  - Bollinger Bands (20, 2.0) → Overbought/Oversold zones
  - RSI (14) → ยืนยันทิศทาง oversold/overbought
  - EMA 50 → กรอง trend หลัก (ไม่สวนเทรนด์แรง)
  - ATR (14) → Dynamic SL/TP sizing
  - ADX (14) → Regime filter: เทรดเมื่อ ADX < 30 (ranging market)

Target: Compound Growth 300 THB/day++ เมื่อพอร์ตโตเพียงพอ
"""

import pandas as pd
import app.analysis.indicators as ind
import numpy as np
import logging
from typing import Optional, Dict, Any
from .base_strategy import BaseStrategy, StrategyDecision

logger = logging.getLogger("SilverMeanRev")


class SilverMeanRevStrategy(BaseStrategy):
    """
    Silver Mean-Reversion Strategy — ออกแบบสำหรับ XAG โดยเฉพาะ
    
    BUY:  ราคาปิด < BB Lower + RSI < 35 + ADX < 30
    SELL: ราคาปิด > BB Upper + RSI > 65 + ADX < 30
    TP = BB Middle (SMA20) — mean reversion target
    """

    # ═══════════════════════════════════════
    # Tunable Parameters (Silver-Optimized)
    # ═══════════════════════════════════════

    # Bollinger Bands
    BB_LEN = 20
    BB_STD = 2.0

    # RSI
    RSI_PERIOD = 14
    RSI_BUY = 35       # Oversold threshold
    RSI_SELL = 65       # Overbought threshold
    RSI_EXTREME_BUY = 25   # Extra confidence boost
    RSI_EXTREME_SELL = 75  # Extra confidence boost

    # Trend Filter
    EMA_PERIOD = 50
    EMA_DISTANCE_MAX = 3.0  # Max ATR distance from EMA50 (ไม่เทรดถ้าราคาไกล EMA มาก)

    # Regime Filter
    ADX_PERIOD = 14
    ADX_MAX = 30        # เทรดเมื่อ ADX < 30 (ranging ดี)
    ADX_STRONG_TREND = 35  # ADX > 35 = trend แรง → ห้ามเทรด

    # ATR / Sizing
    ATR_PERIOD = 14
    SL_ATR_MULT = 1.2   # SL = 1.2 ATR below/above entry
    TP_USE_BB_MID = True # True = TP ที่ BB Middle, False = TP ที่ ATR mult
    TP_ATR_MULT = 1.5    # Fallback TP ถ้าไม่ใช้ BB Middle
    MIN_SL_DISTANCE = 0.05  # Silver scale (Gold ใช้ 3.0)

    # Confirmation
    MIN_CONFIDENCE = 55     # Minimum confidence to trade
    VOLUME_SPIKE_MULT = 1.3 # Volume > 1.3x avg = spike confirmation
    LOOKBACK_TOUCHES = 3    # Check BB touch in last N bars

    # Cooldown
    MIN_BARS_BETWEEN_TRADES = 3  # Wait N bars after last signal

    def __init__(self):
        self.name = "SILVER_MEAN_REV"
        self._last_signal_bar = -999

    def update_parameters(self, params: dict):
        """Update parameters from dict (used by training script)."""
        for key, value in params.items():
            if hasattr(self, key) and not key.startswith("_"):
                setattr(self, key, value)

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "style": "Mean-Reversion (BB + RSI)",
            "bb_len": self.BB_LEN,
            "bb_std": self.BB_STD,
            "rsi_period": self.RSI_PERIOD,
            "rsi_buy": self.RSI_BUY,
            "rsi_sell": self.RSI_SELL,
            "adx_max": self.ADX_MAX,
            "sl_atr_mult": self.SL_ATR_MULT,
            "min_confidence": self.MIN_CONFIDENCE,
        }

    def analyze(self, df: pd.DataFrame, symbol: str = "XAGUSDc", **kwargs) -> StrategyDecision:
        """
        Mean-Reversion Analysis สำหรับ Silver.
        
        Signal Logic:
          BUY:  close < BB_Lower AND RSI < RSI_BUY AND ADX < ADX_MAX
          SELL: close > BB_Upper AND RSI > RSI_SELL AND ADX < ADX_MAX
          TP:   BB Middle (SMA20) — กลับค่าเฉลี่ย
        """
        # ─── Check minimum data ───
        min_bars = max(self.BB_LEN, self.EMA_PERIOD, self.ADX_PERIOD) + 10
        if len(df) < min_bars:
            return StrategyDecision(
                signal="NO_TRADE",
                reason=f"ข้อมูลไม่พอ: ต้องการ {min_bars} bars, มี {len(df)}",
                strategy_name=self.name,
            )

        # ─── Calculate Indicators ───
        df = self._ensure_indicators(df)
        last = df.iloc[-1]
        prev = df.iloc[-2]
        bar_idx = len(df) - 1

        close = last["close"]
        high = last["high"]
        low = last["low"]

        # Extract indicator values — pandas_ta bbands produces: BBL_{len}_{std}_{std}
        bb_suffix = f"{self.BB_LEN}_{self.BB_STD}_{self.BB_STD}"
        bb_upper = last.get(f"BBU_{bb_suffix}", None)
        bb_lower = last.get(f"BBL_{bb_suffix}", None)
        bb_mid = last.get(f"BBM_{bb_suffix}", None)
        rsi = last.get(f"RSI_{self.RSI_PERIOD}", None)
        adx = last.get(f"ADX_{self.ADX_PERIOD}", None)
        atr = last.get(f"ATRr_{self.ATR_PERIOD}", None)
        ema50 = last.get(f"EMA_{self.EMA_PERIOD}", None)
        tick_vol = last.get("tick_volume", last.get("volume", 0))

        # Validate indicators
        if any(v is None or (isinstance(v, float) and np.isnan(v))
               for v in [bb_upper, bb_lower, bb_mid, rsi, adx, atr, ema50]):
            return StrategyDecision(
                signal="NO_TRADE",
                reason="Indicators ยังไม่พร้อม (NaN)",
                strategy_name=self.name,
            )

        if atr <= 0:
            return StrategyDecision(
                signal="NO_TRADE",
                reason="ATR = 0",
                strategy_name=self.name,
            )

        # ─── Cooldown Check ───
        if (bar_idx - self._last_signal_bar) < self.MIN_BARS_BETWEEN_TRADES:
            return StrategyDecision(
                signal="NO_TRADE",
                reason=f"Cooldown: รอ {self.MIN_BARS_BETWEEN_TRADES} bars",
                strategy_name=self.name,
            )

        # ═══════════════════════════════════════
        # REGIME FILTER — ADX
        # ═══════════════════════════════════════
        if adx > self.ADX_STRONG_TREND:
            return StrategyDecision(
                signal="NO_TRADE",
                reason=f"Trend แรงเกิน (ADX={adx:.1f} > {self.ADX_STRONG_TREND}) → ห้าม mean-revert",
                strategy_name=self.name,
            )

        # ═══════════════════════════════════════
        # EMA DISTANCE CHECK — ไม่เทรดถ้าราคาไกล EMA มาก
        # ═══════════════════════════════════════
        ema_distance = abs(close - ema50) / atr
        if ema_distance > self.EMA_DISTANCE_MAX:
            return StrategyDecision(
                signal="NO_TRADE",
                reason=f"ราคาไกล EMA50 เกิน ({ema_distance:.1f} ATR > {self.EMA_DISTANCE_MAX})",
                strategy_name=self.name,
            )

        # ═══════════════════════════════════════
        # SIGNAL DETECTION
        # ═══════════════════════════════════════
        signal = "NO_TRADE"
        reasons = []
        confidence = 0.0
        entry_price = close
        sl = None
        tp = None

        # --- BUY SIGNAL: Price below BB Lower + RSI oversold ---
        is_below_bb_lower = close < bb_lower
        is_rsi_oversold = rsi < self.RSI_BUY
        is_ranging = adx < self.ADX_MAX

        # --- SELL SIGNAL: Price above BB Upper + RSI overbought ---
        is_above_bb_upper = close > bb_upper
        is_rsi_overbought = rsi > self.RSI_SELL

        if is_below_bb_lower and is_rsi_oversold and is_ranging:
            signal = "BUY"
            reasons.append(f"ราคาใต้ BB Lower ({close:.3f} < {bb_lower:.3f})")
            reasons.append(f"RSI oversold ({rsi:.1f} < {self.RSI_BUY})")
            reasons.append(f"ADX ranging ({adx:.1f} < {self.ADX_MAX})")
            confidence += 40.0  # Base confidence

            # SL = Below BB Lower by ATR mult
            sl = close - (atr * self.SL_ATR_MULT)

            # TP = BB Middle (mean reversion target)
            if self.TP_USE_BB_MID:
                tp = bb_mid
            else:
                tp = close + (atr * self.TP_ATR_MULT)

        elif is_above_bb_upper and is_rsi_overbought and is_ranging:
            signal = "SELL"
            reasons.append(f"ราคาเหนือ BB Upper ({close:.3f} > {bb_upper:.3f})")
            reasons.append(f"RSI overbought ({rsi:.1f} > {self.RSI_SELL})")
            reasons.append(f"ADX ranging ({adx:.1f} < {self.ADX_MAX})")
            confidence += 40.0

            # SL = Above BB Upper by ATR mult
            sl = close + (atr * self.SL_ATR_MULT)

            # TP = BB Middle (mean reversion target)
            if self.TP_USE_BB_MID:
                tp = bb_mid
            else:
                tp = close - (atr * self.TP_ATR_MULT)

        else:
            # No signal
            no_reason = []
            if not (is_below_bb_lower or is_above_bb_upper):
                no_reason.append("ราคาอยู่ใน BB band")
            if not (is_rsi_oversold or is_rsi_overbought):
                no_reason.append(f"RSI={rsi:.1f} ไม่ extreme")
            if not is_ranging:
                no_reason.append(f"ADX={adx:.1f} trending")
            return StrategyDecision(
                signal="NO_TRADE",
                reason=" | ".join(no_reason) if no_reason else "ไม่มี setup",
                strategy_name=self.name,
            )

        # ═══════════════════════════════════════
        # CONFIDENCE SCORING
        # ═══════════════════════════════════════

        # 1. RSI Extreme Boost
        if signal == "BUY" and rsi < self.RSI_EXTREME_BUY:
            confidence += 15.0
            reasons.append(f"RSI extreme oversold ({rsi:.1f})")
        elif signal == "SELL" and rsi > self.RSI_EXTREME_SELL:
            confidence += 15.0
            reasons.append(f"RSI extreme overbought ({rsi:.1f})")

        # 2. BB %B Extreme
        bb_width = bb_upper - bb_lower
        if bb_width > 0:
            bb_pct_b = (close - bb_lower) / bb_width
            if signal == "BUY" and bb_pct_b < -0.05:
                confidence += 10.0
                reasons.append(f"BB%B very low ({bb_pct_b:.2f})")
            elif signal == "SELL" and bb_pct_b > 1.05:
                confidence += 10.0
                reasons.append(f"BB%B very high ({bb_pct_b:.2f})")

        # 3. Volume Spike Confirmation
        vol_avg = df["tick_volume"].iloc[-20:].mean() if "tick_volume" in df.columns else df["volume"].iloc[-20:].mean()
        if vol_avg > 0 and tick_vol > vol_avg * self.VOLUME_SPIKE_MULT:
            confidence += 10.0
            reasons.append(f"Volume spike ({tick_vol:.0f} > {vol_avg:.0f})")

        # 4. ADX Very Low = Strong Ranging
        if adx < 20:
            confidence += 10.0
            reasons.append(f"Strong ranging (ADX={adx:.1f})")

        # 5. Multiple BB Touch (in last N bars)
        recent = df.iloc[-self.LOOKBACK_TOUCHES:]
        if signal == "BUY":
            bb_lower_col = f"BBL_{bb_suffix}"
            if bb_lower_col in recent.columns:
                touches = (recent["low"] <= recent[bb_lower_col]).sum()
            else:
                touches = 0
        else:
            bb_upper_col = f"BBU_{bb_suffix}"
            if bb_upper_col in recent.columns:
                touches = (recent["high"] >= recent[bb_upper_col]).sum()
            else:
                touches = 0
        if touches >= 2:
            confidence += 10.0
            reasons.append(f"Multi-touch BB ({touches}x ใน {self.LOOKBACK_TOUCHES} bars)")

        # 6. EMA Proximity Bonus (ราคาใกล้ EMA = reversion มีโอกาสสูง)
        if ema_distance < 1.5:
            confidence += 5.0
            reasons.append(f"ใกล้ EMA50 ({ema_distance:.1f} ATR)")

        # ═══════════════════════════════════════
        # SL VALIDATION
        # ═══════════════════════════════════════
        if sl is not None:
            sl_distance = abs(entry_price - sl)
            if sl_distance < self.MIN_SL_DISTANCE:
                sl = entry_price - self.MIN_SL_DISTANCE if signal == "BUY" else entry_price + self.MIN_SL_DISTANCE
                sl_distance = self.MIN_SL_DISTANCE
                reasons.append(f"SL adjusted to min ({self.MIN_SL_DISTANCE})")

        # ═══════════════════════════════════════
        # CONFIDENCE THRESHOLD CHECK
        # ═══════════════════════════════════════
        if confidence < self.MIN_CONFIDENCE:
            return StrategyDecision(
                signal="NO_TRADE",
                reason=f"Confidence ต่ำ ({confidence:.0f} < {self.MIN_CONFIDENCE}) | {' | '.join(reasons)}",
                strategy_name=self.name,
            )

        # ═══════════════════════════════════════
        # COMPUTE RR RATIO
        # ═══════════════════════════════════════
        rr = 0.0
        if sl is not None and tp is not None:
            sl_dist = abs(entry_price - sl)
            tp_dist = abs(tp - entry_price)
            rr = tp_dist / sl_dist if sl_dist > 0 else 0

        # Mark signal bar for cooldown
        self._last_signal_bar = bar_idx

        reason_str = " | ".join(reasons)
        logger.info(f"[{self.name}] {signal} @ {entry_price:.3f} SL={sl:.3f} TP={tp:.3f} Conf={confidence:.0f} RR={rr:.2f} | {reason_str}")

        return StrategyDecision(
            signal=signal,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            reason=reason_str,
            confidence=confidence,
            risk_pct=1.0,
            strategy_name=self.name,
            risk_reward_ratio=rr,
        )

    def _ensure_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all required indicators."""

        # Bollinger Bands
        bb_col = f"BBU_{self.BB_LEN}_{self.BB_STD}_{self.BB_STD}"
        if bb_col not in df.columns:
            bb = ta.bbands(df["close"], length=self.BB_LEN, std=self.BB_STD)
            if bb is not None:
                for col in bb.columns:
                    df[col] = bb[col]

        # RSI
        rsi_col = f"RSI_{self.RSI_PERIOD}"
        if rsi_col not in df.columns:
            df[rsi_col] = ta.rsi(df["close"], length=self.RSI_PERIOD)

        # EMA
        ema_col = f"EMA_{self.EMA_PERIOD}"
        if ema_col not in df.columns:
            df[ema_col] = ta.ema(df["close"], length=self.EMA_PERIOD)

        # ATR
        atr_col = f"ATRr_{self.ATR_PERIOD}"
        if atr_col not in df.columns:
            df[atr_col] = ta.atr(df["high"], df["low"], df["close"], length=self.ATR_PERIOD)

        # ADX
        adx_col = f"ADX_{self.ADX_PERIOD}"
        if adx_col not in df.columns:
            adx_df = ta.adx(df["high"], df["low"], df["close"], length=self.ADX_PERIOD)
            if adx_df is not None:
                df[adx_col] = adx_df.iloc[:, 0]  # ADX value

        return df


# Global Instance
silver_mean_rev_strategy = SilverMeanRevStrategy()
