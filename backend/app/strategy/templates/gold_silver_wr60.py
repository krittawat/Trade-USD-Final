"""
Gold/Silver WR60 Strategy.

Design goals:
- One rule-set for XAU and XAG.
- M15 entries aligned with H1 trend.
- Conservative, high-probability pullback entries.
- Native BaseStrategy output (Decision) for the existing pipeline.
"""

from __future__ import annotations

import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, 1e-12)
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(length).mean()


class GoldSilverWr60Strategy(BaseStrategy):
    name = "gold_silver_wr60"
    timeframe = "M15"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.STRONG_TREND,
        RegimeType.WEAK_TREND,
        RegimeType.BREAKOUT,
    ]

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "XAUUSD"
        s = symbol.upper()
        is_silver = "XAG" in s or "SILVER" in s

        # Symbol-tuned defaults
        rsi_long_th = 12.0 if is_silver else 15.0
        rsi_short_th = 88.0 if is_silver else 85.0
        sl_atr_mult = float(kwargs.get("sl_atr_mult", 1.2 if is_silver else 1.0))
        rr_target = float(kwargs.get("rr_target", 1.25 if is_silver else 1.2))
        min_rvol = float(kwargs.get("min_rvol", 1.0 if is_silver else 0.9))
        use_session_filter = bool(kwargs.get("use_session_filter", True))

        if candles is None or len(candles) < 80:
            return self.create_hold(symbol=symbol, reason="Insufficient M15 candles")

        h1 = kwargs.get("h1_candles")
        if h1 is None or len(h1) < 220:
            return self.create_hold(symbol=symbol, reason="Insufficient H1 candles")

        session = str(kwargs.get("session", "") or "")
        if use_session_filter and session not in ("LONDON", "NEW_YORK", "OVERLAP"):
            return self.create_hold(symbol=symbol, reason=f"Session filtered: {session or 'CLOSED'}")

        # Regime guard: avoid flat/noise regimes.
        if regime in (RegimeType.RANGING, RegimeType.LOW_VOLATILITY, RegimeType.ACCUMULATION):
            return self.create_hold(symbol=symbol, reason=f"Regime filtered: {regime.value}")

        m15 = candles.copy()
        m15["close"] = m15["close"].astype(float)
        m15["open"] = m15["open"].astype(float)
        m15["high"] = m15["high"].astype(float)
        m15["low"] = m15["low"].astype(float)

        h1 = h1.copy()
        h1["close"] = h1["close"].astype(float)

        # Indicators
        ema20 = _ema(m15["close"], 20)
        rsi2 = _rsi(m15["close"], 2)
        atr14 = _atr(m15, 14)
        h1_ema50 = _ema(h1["close"], 50)
        h1_ema200 = _ema(h1["close"], 200)

        curr = m15.iloc[-1]
        prev = m15.iloc[-2]
        close = float(curr["close"])
        open_ = float(curr["open"])
        high = float(curr["high"])
        low = float(curr["low"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])
        ema20_v = float(ema20.iloc[-1])
        rsi2_v = float(rsi2.iloc[-1])
        atr_v = float(atr14.iloc[-1])

        h1_close = float(h1["close"].iloc[-1])
        h1_50 = float(h1_ema50.iloc[-1])
        h1_200 = float(h1_ema200.iloc[-1])

        if any(pd.isna(v) for v in [ema20_v, rsi2_v, atr_v, h1_close, h1_50, h1_200]) or atr_v <= 0:
            return self.create_hold(symbol=symbol, reason="Indicators not ready")

        # Trend filter from H1
        long_trend = h1_close > h1_200 and h1_50 > h1_200
        short_trend = h1_close < h1_200 and h1_50 < h1_200
        if not long_trend and not short_trend:
            return self.create_hold(symbol=symbol, reason="No H1 trend alignment")

        # Pullback + reversal trigger on M15
        touch_long = low <= ema20_v
        touch_short = high >= ema20_v
        bull_reversal = close > open_ and close > prev_high
        bear_reversal = close < open_ and close < prev_low

        long_setup = long_trend and touch_long and (rsi2_v < rsi_long_th) and bull_reversal
        short_setup = short_trend and touch_short and (rsi2_v > rsi_short_th) and bear_reversal

        # Volume quality filter
        vol_col = "tick_volume" if "tick_volume" in m15.columns else "volume"
        rvol = 1.0
        if vol_col in m15.columns:
            vol = m15[vol_col].astype(float)
            vol_ma = vol.rolling(20).mean().iloc[-1]
            if vol_ma and vol_ma > 0:
                rvol = float(vol.iloc[-1] / vol_ma)
        if rvol < min_rvol:
            return self.create_hold(symbol=symbol, reason=f"Low RVOL {rvol:.2f} < {min_rvol:.2f}")

        if not long_setup and not short_setup:
            return self.create_hold(
                symbol=symbol,
                reason=(
                    f"No setup: trend(L={long_trend},S={short_trend}) "
                    f"touch(L={touch_long},S={touch_short}) rsi2={rsi2_v:.1f}"
                ),
            )

        digits = profile.digits if profile else 3
        sl_dist = atr_v * sl_atr_mult
        tp_dist = sl_dist * rr_target

        # Confidence score
        body = abs(close - open_)
        range_ = max(high - low, 1e-9)
        body_ratio = body / range_
        h1_gap_pct = abs(h1_50 - h1_200) / max(abs(h1_200), 1e-9)

        confidence = 0.60
        if rvol >= 1.2:
            confidence += 0.08
        if body_ratio >= 0.5:
            confidence += 0.07
        if h1_gap_pct >= 0.0015:
            confidence += 0.07
        if regime in self.suitable_regimes:
            confidence += 0.05
        confidence = max(0.0, min(0.95, confidence))

        if long_setup:
            action = Action.BUY
            stop_loss = round(close - sl_dist, digits)
            take_profit = round(close + tp_dist, digits)
            reason = (
                f"H1 uptrend + EMA20 pullback + RSI2={rsi2_v:.1f} oversold + bullish reversal; "
                f"RVOL={rvol:.2f}"
            )
        else:
            action = Action.SELL
            stop_loss = round(close + sl_dist, digits)
            take_profit = round(close - tp_dist, digits)
            reason = (
                f"H1 downtrend + EMA20 pullback + RSI2={rsi2_v:.1f} overbought + bearish reversal; "
                f"RVOL={rvol:.2f}"
            )

        rr_real = tp_dist / max(sl_dist, 1e-9)
        return Decision(
            symbol=symbol,
            action=action,
            confidence=round(confidence, 3),
            reason=reason,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=round(rr_real, 2),
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["wr60", "m15", "h1_filter", "pullback"],
            debug={
                "rsi2": round(rsi2_v, 2),
                "atr14": round(atr_v, digits),
                "ema20": round(ema20_v, digits),
                "h1_ema50": round(h1_50, digits),
                "h1_ema200": round(h1_200, digits),
                "rvol": round(rvol, 2),
                "sl_atr_mult": sl_atr_mult,
                "rr_target": rr_target,
            },
        )
