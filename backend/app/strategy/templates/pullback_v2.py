"""
Pullback V2 Strategy — Smart Pullback BUY / Rejection SELL.

Backtest Results (30-day XAUUSDc):
    V2_SELL_BIAS: WR=69.2%, PF=3.28, DD=3.0%, P&L=+$1,714
    Key insight: SELL ชนะ 69% vs BUY 38% → SELL-biased mode

Enhanced with:
    - Candlestick Pattern Detection (Engulfing, Hammer, Shooting Star,
      Morning/Evening Star, Pin Bar, Double Bottom/Top)
    - Web Sentiment Integration (RSS news → confidence modifier)
    - Pattern-trade alignment → boost/penalty confidence

Logic:
    TREND:     EMA21 vs EMA50 — กำหนดทิศทาง
    FILTER:    ADX ≥ 12 + MACD histogram + body size + pattern confirmation
    BUY:       Uptrend + pullback to EMA21 + RSI ≤ 40 + bullish pattern
    SELL:      Downtrend + rejection at EMA21 + RSI ≥ 50 + bearish pattern
    PATTERN:   Confirming pattern → +0.10 conf, Conflicting → -0.15 conf
    WEB:       Bullish news + BUY → +0.05, Bearish news + SELL → +0.05

Suitable:
    - Gold (XAUUSDc), Silver (XAGUSDc)
    - Trending regimes
"""

import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)

# ── Try import WebResearcher (optional — graceful fallback) ──
try:
    from app.brain.web_researcher import WebResearcher
    _web_researcher = WebResearcher()
except Exception:
    _web_researcher = None


class PullbackV2Strategy(BaseStrategy):
    """
    Smart Pullback-Rejection Strategy V2 — SELL-biased.

    เน้น SELL (rejection) มากกว่า BUY (pullback) เพราะ backtest ยืนยัน
    SELL WR สูงกว่า BUY อย่างชัดเจน (69% vs 38%).
    """

    name = "pullback_v2"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.STRONG_TREND,
        RegimeType.WEAK_TREND,
    ]

    # ── Parameters (V2_SELL_BIAS config) ──
    p = {
        "ema_fast": 21,
        "ema_slow": 50,
        "rr_ratio": 2.0,           # Risk:Reward 1:2
        "sl_atr_mult": 1.5,        # SL = 1.5 × ATR
        "pullback_pct": 0.3,       # Pullback zone = 0.3 × ATR
        "rsi_buy_max": 40,         # RSI ≤ 40 for BUY (tight)
        "rsi_sell_min": 50,        # RSI ≥ 50 for SELL (loose)
        "adx_min": 12,             # Min ADX for trending
        "min_body_pct": 0.15,      # Min body/range ratio (skip dojis)
        "min_trend_gap_atr": 0.15, # Min EMA gap as % of ATR
    }

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        pressure: dict | None = None,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol

        if len(candles) < 120:
            return self.create_hold(symbol, "Insufficient data (need ≥120 bars)")

        # ── Compute Indicators ──
        close = candles["close"]
        high = candles["high"]
        low = candles["low"]
        open_ = candles["open"]

        ema_fast = close.ewm(span=self.p["ema_fast"], adjust=False).mean()
        ema_slow = close.ewm(span=self.p["ema_slow"], adjust=False).mean()
        rsi = self._calc_rsi(close, 14)
        atr = self._calc_atr(candles, 14)
        adx = self._calc_adx(candles, 14)
        macd_hist = self._calc_macd_hist(close)

        # Latest values
        i = len(candles) - 1
        row = candles.iloc[i]
        prev = candles.iloc[i - 1]
        c = close.iloc[i]
        h = high.iloc[i]
        l = low.iloc[i]
        o = open_.iloc[i]
        ef = ema_fast.iloc[i]
        es = ema_slow.iloc[i]
        r = rsi.iloc[i]
        a = atr.iloc[i]
        dx = adx.iloc[i]
        mh = macd_hist.iloc[i]
        mh_prev = macd_hist.iloc[i - 1] if not pd.isna(macd_hist.iloc[i - 1]) else 0

        if pd.isna(ef) or pd.isna(es) or pd.isna(r) or pd.isna(a) or a <= 0 or pd.isna(dx):
            return self.create_hold(symbol, "Indicators not ready")

        # ── Filter 1: ADX trend strength ──
        if dx < self.p["adx_min"]:
            return self.create_hold(symbol, f"Sideways market (ADX={dx:.0f} < {self.p['adx_min']})")

        # ── Filter 2: Min body size ──
        body = abs(c - o)
        total_range = h - l
        if total_range > 0 and body / total_range < self.p["min_body_pct"]:
            return self.create_hold(symbol, "Indecision candle (doji)")

        # ── Filter 3: Trend gap ──
        trend_gap = abs(ef - es)
        if trend_gap < a * self.p["min_trend_gap_atr"]:
            return self.create_hold(symbol, "Weak trend (EMA gap too small)")

        pb_zone = a * self.p["pullback_pct"]
        sl_dist = a * self.p["sl_atr_mult"]
        rr = self.p["rr_ratio"]

        # ── Detect Candlestick Patterns ──
        patterns = self._detect_patterns(candles, i)
        bull_patterns = patterns["bullish"]
        bear_patterns = patterns["bearish"]

        # ── Web Sentiment (cached, non-blocking) ──
        web_sentiment = self._get_web_sentiment(symbol)

        # ═════════════════════════════════
        # SELL REJECTION (preferred — higher WR)
        # ═════════════════════════════════
        if ef < es:
            near_ema = h >= ef - pb_zone and c < ef + pb_zone
            rsi_ok = r >= self.p["rsi_sell_min"] and r <= 75
            candle_bearish = c < o and body / total_range > 0.3 if total_range > 0 else False
            below_slow = c < es
            macd_ok = mh < mh_prev  # MACD histogram turning down

            if near_ema and rsi_ok and candle_bearish and below_slow and macd_ok:
                sl = c + sl_dist
                recent_highs = high.iloc[max(0, i-5):i+1]
                sl = max(sl, recent_highs.max() + a * 0.2)
                tp = c - sl_dist * rr

                confidence = 0.65 + min(0.15, (dx - 12) / 100)  # ADX bonus

                # Pattern boost/penalty
                if bear_patterns:
                    confidence += 0.10  # Bearish pattern confirms SELL
                if bull_patterns and not bear_patterns:
                    confidence -= 0.15  # Bullish pattern conflicts SELL → skip
                    return self.create_hold(symbol,
                        f"SELL blocked by bullish pattern: {','.join(bull_patterns)}")

                # Web sentiment modifier
                if web_sentiment < -0.1:  # Bearish news
                    confidence += 0.05
                elif web_sentiment > 0.2:  # Strong bullish news conflicts
                    confidence -= 0.05

                confidence = max(0.0, min(1.0, confidence))
                pat_str = ','.join(bear_patterns) if bear_patterns else 'none'

                logger.info("pullback_v2_signal", extra={
                    "symbol": symbol, "action": "SELL", "type": "REJECTION",
                    "rsi": round(r, 1), "adx": round(dx, 1),
                    "patterns": pat_str, "web_sentiment": round(web_sentiment, 2),
                    "confidence": round(confidence, 2),
                })

                return Decision(
                    symbol=symbol,
                    action=Action.SELL,
                    confidence=confidence,
                    reason=f"REJ-SELL EMA{self.p['ema_fast']} | RSI={r:.0f} ADX={dx:.0f} | MACD↓ | Pat:{pat_str}",
                    stop_loss=round(sl, profile.digits),
                    take_profit=round(tp, profile.digits),
                    strategy_name=self.name,
                    timeframe=self.timeframe,
                    tags=[f"pattern:{pat_str}", f"adx:{round(dx, 1)}", f"rsi:{round(r, 1)}",
                          f"web_sentiment:{round(web_sentiment, 2)}"],
                )

        # ═════════════════════════════════
        # BUY PULLBACK (stricter — requires macro uptrend + lower RR)
        # ═════════════════════════════════
        e200 = close.ewm(span=200, adjust=False).mean().iloc[i]
        
        if ef > es and es > e200:  # Macro uptrend alignment
            near_ema = l <= ef + pb_zone and c > ef - pb_zone
            rsi_ok = 25 <= r <= self.p["rsi_buy_max"]
            candle_bullish = c > o and body / total_range > 0.3 if total_range > 0 else False
            above_slow = c > es
            macd_ok = mh > mh_prev  # MACD histogram turning up

            if near_ema and rsi_ok and candle_bullish and above_slow and macd_ok:
                sl = c - sl_dist
                recent_lows = low.iloc[max(0, i-5):i+1]
                sl = min(sl, recent_lows.min() - a * 0.2)
                buy_rr = 1.5  # Lower target for BUY since it struggles more than SELL
                tp = c + (c - sl) * buy_rr

                confidence = 0.55 + min(0.10, (dx - 12) / 100)

                # Pattern boost/penalty
                if bull_patterns:
                    confidence += 0.10  # Bullish pattern confirms BUY
                if bear_patterns and not bull_patterns:
                    confidence -= 0.15  # Bearish pattern conflicts BUY → skip
                    return self.create_hold(symbol,
                        f"BUY blocked by bearish pattern: {','.join(bear_patterns)}")

                # Web sentiment modifier
                if web_sentiment > 0.1:  # Bullish news
                    confidence += 0.05
                elif web_sentiment < -0.2:  # Strong bearish news conflicts
                    confidence -= 0.05

                confidence = max(0.0, min(1.0, confidence))
                pat_str = ','.join(bull_patterns) if bull_patterns else 'none'

                logger.info("pullback_v2_signal", extra={
                    "symbol": symbol, "action": "BUY", "type": "PULLBACK",
                    "rsi": round(r, 1), "adx": round(dx, 1),
                    "patterns": pat_str, "web_sentiment": round(web_sentiment, 2),
                    "confidence": round(confidence, 2),
                })

                return Decision(
                    symbol=symbol,
                    action=Action.BUY,
                    confidence=confidence,
                    reason=f"PB-BUY EMA{self.p['ema_fast']} | RSI={r:.0f} ADX={dx:.0f} | MACD↑ | Pat:{pat_str}",
                    stop_loss=round(sl, profile.digits),
                    take_profit=round(tp, profile.digits),
                    strategy_name=self.name,
                    timeframe=self.timeframe,
                    tags=[f"pattern:{pat_str}", f"adx:{round(dx, 1)}", f"rsi:{round(r, 1)}",
                          f"web_sentiment:{round(web_sentiment, 2)}"],
                )

        return self.create_hold(symbol, f"No setup | EMA{'↑' if ef>es else '↓'} RSI={r:.0f} ADX={dx:.0f}")

    # ══════════════════════════════════════════════════════
    # Candlestick Pattern Detection
    # ══════════════════════════════════════════════════════

    @staticmethod
    def _detect_patterns(df: pd.DataFrame, i: int) -> dict:
        """
        Detect candlestick patterns at bar index i.

        Returns:
            {"bullish": ["ENGULFING", ...], "bearish": ["SHOOTING_STAR", ...]}
        """
        patterns = {"bullish": [], "bearish": []}
        if i < 20 or i >= len(df):
            return patterns

        curr = df.iloc[i]
        prev = df.iloc[i - 1]
        prev2 = df.iloc[i - 2] if i >= 2 else prev

        co, cc, ch, cl = curr["open"], curr["close"], curr["high"], curr["low"]
        po, pc, ph, pl = prev["open"], prev["close"], prev["high"], prev["low"]
        p2o, p2c = prev2["open"], prev2["close"]

        body = abs(cc - co)
        prev_body = abs(pc - po)
        upper_wick = ch - max(co, cc)
        lower_wick = min(co, cc) - cl
        total = ch - cl
        if total <= 0:
            return patterns

        # ── Bullish Engulfing ──
        if pc < po and cc > co:  # prev bearish, curr bullish
            if cc > po and co < pc:  # curr body engulfs prev body
                patterns["bullish"].append("ENGULFING")

        # ── Bearish Engulfing ──
        if pc > po and cc < co:  # prev bullish, curr bearish
            if cc < po and co > pc:
                patterns["bearish"].append("ENGULFING")

        # ── Hammer (bullish reversal) ──
        if body > 0 and lower_wick > body * 2 and upper_wick < body * 0.5:
            patterns["bullish"].append("HAMMER")

        # ── Shooting Star (bearish reversal) ──
        if body > 0 and upper_wick > body * 2 and lower_wick < body * 0.5:
            patterns["bearish"].append("SHOOTING_STAR")

        # ── Morning Star (bullish — 3-candle) ──
        if i >= 2:
            p2_bearish = p2c < p2o
            prev_small = prev_body < body * 0.3  # Middle candle is small
            curr_bullish = cc > co
            if p2_bearish and prev_small and curr_bullish and cc > (p2o + p2c) / 2:
                patterns["bullish"].append("MORNING_STAR")

        # ── Evening Star (bearish — 3-candle) ──
        if i >= 2:
            p2_bullish = p2c > p2o
            prev_small = prev_body < body * 0.3
            curr_bearish = cc < co
            if p2_bullish and prev_small and curr_bearish and cc < (p2o + p2c) / 2:
                patterns["bearish"].append("EVENING_STAR")

        # ── Pin Bar (long wick rejection) ──
        if total > 0 and body / total < 0.25:
            if lower_wick > total * 0.6:
                patterns["bullish"].append("PIN_BAR")
            elif upper_wick > total * 0.6:
                patterns["bearish"].append("PIN_BAR")

        # ── Double Bottom (lookback 20 bars → bullish) ──
        if i >= 20:
            lookback = df.iloc[i-20:i]
            lows = lookback["low"]
            min_val = lows.min()
            threshold = min_val * 0.003
            nearby_lows = lows[(lows - min_val).abs() < threshold]
            if len(nearby_lows) >= 2 and cc > co:  # 2+ lows + bullish now
                patterns["bullish"].append("DOUBLE_BOTTOM")

        # ── Double Top (lookback 20 bars → bearish) ──
        if i >= 20:
            lookback = df.iloc[i-20:i]
            highs = lookback["high"]
            max_val = highs.max()
            threshold = max_val * 0.003
            nearby_highs = highs[(highs - max_val).abs() < threshold]
            if len(nearby_highs) >= 2 and cc < co:  # 2+ highs + bearish now
                patterns["bearish"].append("DOUBLE_TOP")

        return patterns

    # ══════════════════════════════════════════════════════
    # Web Sentiment
    # ══════════════════════════════════════════════════════

    @staticmethod
    def _get_web_sentiment(symbol: str) -> float:
        """
        Get web sentiment for symbol from WebResearcher cache.
        Returns: float (-1.0 = bearish, 0 = neutral, +1.0 = bullish)
        """
        if _web_researcher is None:
            return 0.0
        try:
            knowledge = _web_researcher._cache.get(symbol)
            if knowledge and hasattr(knowledge, "overall_sentiment"):
                return knowledge.overall_sentiment
        except Exception:
            pass
        return 0.0

    # ── Indicator Helpers ──

    @staticmethod
    def _calc_rsi(s: pd.Series, period: int = 14) -> pd.Series:
        d = s.diff()
        g = d.clip(lower=0).ewm(span=period, adjust=False).mean()
        l = (-d).clip(lower=0).ewm(span=period, adjust=False).mean()
        return 100 - (100 / (1 + g / l.replace(0, np.nan)))

    @staticmethod
    def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        plus_dm = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)
        plus_dm[plus_dm < minus_dm] = 0
        minus_dm[minus_dm < plus_dm] = 0
        tr = pd.concat([high-low, (high-close.shift(1)).abs(), (low-close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.ewm(span=period, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr.replace(0, np.nan)
        minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr.replace(0, np.nan)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        return dx.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _calc_macd_hist(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
        ef = s.ewm(span=fast, adjust=False).mean()
        es = s.ewm(span=slow, adjust=False).mean()
        macd = ef - es
        sig = macd.ewm(span=signal, adjust=False).mean()
        return macd - sig
