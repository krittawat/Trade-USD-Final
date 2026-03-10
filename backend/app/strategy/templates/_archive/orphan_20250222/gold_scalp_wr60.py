"""
Gold Scalp WR60 — High Win-Rate Gold Scalping Strategy
=======================================================
Fork of GoldScalpPro, tuned for >60% Win Rate on XAUUSD M5.

Key differences from GoldScalpPro:
  - Tighter TP across ALL regimes (1.0-2.0 ATR instead of 3.0-5.0)
  - Lower minimum score thresholds for more trades
  - Faster SuperTrend (7-period) for quicker trend detection
  - Simpler SL using ATR-based (no anti-hunt randomization in backtest)
  - Removed Gold Bias Correction (let both directions trade freely)
  - Removed session penalties (keep parameters stable)
"""
import pandas as pd
import app.analysis.indicators as ind
import numpy as np
import logging
from typing import Optional, Dict, Any
from .base_strategy import BaseStrategy, StrategyDecision

logger = logging.getLogger("GoldScalpWR60")


class GoldScalpWR60Strategy(BaseStrategy):
    """
    Win-Rate Optimized Gold Scalping Strategy.
    Target: WR ≥ 60%, Profit Factor > 1.2
    """

    # Indicator Periods
    SUPERTREND_LEN = 7        # Faster than default 10
    SUPERTREND_MUL = 2.0      # Tighter than default 3.0
    RSI_PERIOD = 14
    ADX_PERIOD = 14
    ATR_PERIOD = 14
    EMA_FAST = 21
    EMA_SLOW = 50

    # Risk — WINNING CONFIG: SL_2.5_TP_1.0 (WR=73%, PF=1.04, $264)
    SL_ATR_MULT = 2.5         # Wide SL protects from noise
    TP_ATR_MULT = 1.0         # Tight TP → high hit rate
    MIN_SL_DISTANCE = 3.5     # $3.50 minimum SL

    # Trailing
    CHANDELIER_MULT = 1.5
    PROFIT_LOCK_1 = 0.5       # BE at +0.5 ATR (quick lock)
    PROFIT_LOCK_2 = 1.0       # 50% lock at +1 ATR

    # Adaptive Risk
    RISK_SNIPER = 0.02
    RISK_STANDARD = 0.01
    RISK_LOW = 0.005

    def __init__(self):
        self.name = "GOLD_SCALP_WR60"
        self.params = {}

    def update_parameters(self, params: dict):
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.params.update(params)

    def get_status(self):
        return {
            "name": self.name,
            "style": "WR60 Scalper",
            "indicators": ["EMA", "SuperTrend", "ADX", "RSI", "MACD"],
            "target": "WR ≥ 60%"
        }

    def analyze(self, df: pd.DataFrame, symbol: str = "XAUUSD", **kwargs) -> StrategyDecision:
        """
        WR60 Analysis — Confluence scoring with TIGHT TP.
        """
        if df is None or len(df) < 100:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient data")

        # Brain check
        regime_context = kwargs.get("regime_context")
        if regime_context and not regime_context.actionable:
            return StrategyDecision(signal="NO_TRADE", reason=f"Brain Block: {regime_context.reason}")

        # Ensure indicators
        df = self._ensure_indicators(df)

        r = df.iloc[-1]
        close = float(r['close'])
        atr = float(r.get('atr', 3.0))
        rsi = float(r.get('rsi', 50))
        adx = float(r.get('adx', 0))

        # SuperTrend direction
        st_col = f'SUPERT_d_{self.SUPERTREND_LEN}_{self.SUPERTREND_MUL}'
        st_dir = float(r.get(st_col, 0))

        # EMA trend
        ema_fast = float(r.get('ema_fast', close))
        ema_slow = float(r.get('ema_slow', close))
        ema_bullish = ema_fast > ema_slow
        ema_bearish = ema_fast < ema_slow

        # MACD
        macd_line = float(r.get('MACD_12_26_9', 0))
        macd_signal = float(r.get('MACDs_12_26_9', 0))

        # Vitality
        vitality = float(r.get('Vitality', 50))

        if pd.isna(rsi) or pd.isna(adx) or pd.isna(atr) or atr <= 0:
            return StrategyDecision(signal="NO_TRADE", reason="Loading indicators...")

        # ── High Volatility Block ──
        if regime_context:
            regime_val = regime_context.regime.value
            if regime_val == "HIGH_VOLATILITY":
                return StrategyDecision(signal="NO_TRADE", reason="High Volatility Block")

        # ── Regime Detection (internal) ──
        # CRITICAL: Use self.SL_ATR_MULT / self.TP_ATR_MULT as the BASE,
        # then apply regime-specific SCALING FACTORS (not hardcoded overrides)
        atr_avg = df['atr'].rolling(20).mean().iloc[-1] if 'atr' in df.columns else atr
        atr_ratio = atr / atr_avg if atr_avg > 0 else 1.0
        is_trending = adx > 25
        is_volatile = atr_ratio > 1.2

        # Base from class (so grid search can override)
        base_sl = self.SL_ATR_MULT
        base_tp = self.TP_ATR_MULT

        if is_trending and is_volatile:
            regime = "TRENDING_VOLATILE"
            sl_mult = base_sl * 1.3   # slightly wider in volatile trend
            tp_mult = base_tp * 1.3   # slightly wider target
            min_score = 55
        elif is_trending and not is_volatile:
            regime = "TRENDING_STABLE"
            sl_mult = base_sl         # base values
            tp_mult = base_tp
            min_score = 50            # easiest entry in stable trend
        elif not is_trending and is_volatile:
            regime = "CHOPPY"
            sl_mult = base_sl * 1.2   # wider SL in chop
            tp_mult = base_tp * 0.8   # tighter TP in chop
            min_score = 70            # strict in chop
        else:
            regime = "FLAT"
            sl_mult = base_sl
            tp_mult = base_tp * 0.9   # even tighter in flat
            min_score = 65

        # ── Scoring System ──
        buy_score = 0
        sell_score = 0
        reasons = []

        # 1. SuperTrend (25 pts)
        if st_dir == 1:
            buy_score += 25
            reasons.append("ST↑")
        elif st_dir == -1:
            sell_score += 25
            reasons.append("ST↓")

        # 2. EMA Trend (20 pts)
        if ema_bullish:
            buy_score += 20
            reasons.append("EMA↑")
        elif ema_bearish:
            sell_score += 20
            reasons.append("EMA↓")

        # 3. RSI Momentum (15 pts)
        if 45 < rsi < 70:
            buy_score += 15
        elif rsi < 30:
            buy_score += 10  # oversold bounce
        if 30 < rsi < 55:
            sell_score += 15
        elif rsi > 70:
            sell_score += 10  # overbought drop

        # 4. ADX Trend Strength (15 pts)
        if adx > 25:
            if buy_score > sell_score:
                buy_score += 15
                reasons.append(f"ADX:{adx:.0f}")
            elif sell_score > buy_score:
                sell_score += 15
                reasons.append(f"ADX:{adx:.0f}")

        # 5. MACD Confirmation (10 pts)
        if macd_line > macd_signal:
            buy_score += 10
        elif macd_line < macd_signal:
            sell_score += 10

        # 6. Vitality Boost (10 pts)
        if vitality > 60:
            if buy_score > sell_score:
                buy_score += 10
            elif sell_score > buy_score:
                sell_score += 10
            reasons.append(f"Vit:{vitality:.0f}")

        # 7. Price above/below EMA Slow (5 pts — trend confirmation)
        if close > ema_slow:
            buy_score += 5
        elif close < ema_slow:
            sell_score += 5

        # ── Direction Control ──
        direction_mode = kwargs.get('direction', 'AUTO')
        if direction_mode == "BUY_ONLY":
            sell_score = 0
        if direction_mode == "SELL_ONLY":
            buy_score = 0

        # ── Signal Decision ──
        signal = "NO_TRADE"
        sl = 0.0
        tp = 0.0
        active_score = 0

        if buy_score >= min_score and buy_score > sell_score:
            signal = "BUY"
            active_score = buy_score
            sl = close - (atr * sl_mult)
            # Enforce minimum SL distance
            if abs(close - sl) < self.MIN_SL_DISTANCE:
                sl = close - self.MIN_SL_DISTANCE
            tp = close + (atr * tp_mult)
            reasons.append(f"[{regime}]")

        elif sell_score >= min_score and sell_score > buy_score:
            signal = "SELL"
            active_score = sell_score
            sl = close + (atr * sl_mult)
            if abs(sl - close) < self.MIN_SL_DISTANCE:
                sl = close + self.MIN_SL_DISTANCE
            tp = close - (atr * tp_mult)
            reasons.append(f"[{regime}]")

        if signal != "NO_TRADE":
            # Adaptive Risk
            risk_pct = self.RISK_LOW
            if active_score >= 90:
                risk_pct = self.RISK_SNIPER
                reasons.append("🎯SNIPER")
            elif active_score >= 75:
                risk_pct = self.RISK_STANDARD

            conf = active_score / 100.0
            reason_str = f"[WR60] Score:{active_score} | {' '.join(reasons)}"

            return StrategyDecision(
                signal=signal,
                entry_price=close,
                sl=sl,
                tp=tp,
                reason=reason_str,
                confidence=conf,
                risk_pct=risk_pct
            )

        return StrategyDecision(signal="WAIT", reason=f"Score B{buy_score}/S{sell_score}", confidence=0)

    def _ensure_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate indicators using pandas_ta."""
        # Vitality Score
        if 'Vitality' not in df.columns and 'ADX_14' in df.columns:
            df['Vitality'] = (df['ADX_14'] / 100 * 50) + 50

        # SuperTrend
        st_col = f'SUPERT_{self.SUPERTREND_LEN}_{self.SUPERTREND_MUL}'
        if st_col not in df.columns:
            st = ta.supertrend(df['high'], df['low'], df['close'],
                               length=self.SUPERTREND_LEN, multiplier=self.SUPERTREND_MUL)
            if st is not None:
                df = pd.concat([df, st], axis=1)

        # EMA Fast / Slow
        if 'ema_fast' not in df.columns:
            df['ema_fast'] = ta.ema(df['close'], length=self.EMA_FAST)
        if 'ema_slow' not in df.columns:
            df['ema_slow'] = ta.ema(df['close'], length=self.EMA_SLOW)

        # RSI
        if 'rsi' not in df.columns:
            df['rsi'] = ta.rsi(df['close'], length=self.RSI_PERIOD)

        # ADX
        if 'adx' not in df.columns:
            adx = ta.adx(df['high'], df['low'], df['close'], length=self.ADX_PERIOD)
            if adx is not None:
                endpoint = f"ADX_{self.ADX_PERIOD}"
                if endpoint in adx.columns:
                    df['adx'] = adx[endpoint]

        # ATR
        if 'atr' not in df.columns:
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=self.ATR_PERIOD)

        # MACD
        if 'MACD_12_26_9' not in df.columns:
            macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
            if macd is not None:
                df = pd.concat([df, macd], axis=1)

        return df


# Global Instance
gold_scalp_wr60_strategy = GoldScalpWR60Strategy()
