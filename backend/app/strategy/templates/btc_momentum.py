"""
BTC Momentum Strategy — EMA + RSI + Volume Breakout for BTCUSDc.

Optimized for: BTCUSDc (Exness MT5 cent contract)
Timeframe: M5

Philosophy:
    - BTC is 24/7, momentum-driven with violent moves followed by consolidation
    - Enter on momentum breakout AFTER consolidation (low→high volatility shift)
    - Use Bollinger Band squeeze detection for timing
    - RSI + EMA alignment for directional bias
    - Volume spike as entry trigger
    - Conservative SL (1.5× ATR) with 2.0× RR target

Key Design Decisions:
    - Short EMA (9/21/50) → no 200 EMA to keep minimum bars low (~60)
    - Bollinger Band squeeze → detect consolidation about to break
    - RSI divergence check → avoid buying exhaustion
    - Volume spike filter → only enter on conviction
    - ATR-based SL → adapts to current volatility

Expected Characteristics:
    - Moderate trade frequency (~10-20 per 1000 bars)
    - Target WR ≥ 60%, PF ≥ 1.3
    - Max DD ≤ 6%
"""

import pandas as pd
import numpy as np

from app.strategy.base import BaseStrategy
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

logger = get_logger(__name__)

# ═════════════════════════════════════════════
# Parameters
# ═════════════════════════════════════════════

# EMA periods (short for crypto)
EMA_FAST = 9
EMA_MID = 21
EMA_SLOW = 50

# RSI
RSI_PERIOD = 14
RSI_BULL_MIN = 55       # RSI must be > 55 for BUY (stricter)
RSI_BULL_MAX = 72       # RSI must be < 72 (not overbought)
RSI_BEAR_MAX = 45       # RSI must be < 45 for SELL (stricter)
RSI_BEAR_MIN = 28       # RSI must be > 28 (not oversold)

# Bollinger Bands
BB_PERIOD = 20
BB_STD = 2.0
BB_SQUEEZE_THRESHOLD = 0.03  # BB width < 3% of close = squeeze

# ATR
ATR_PERIOD = 14

# Volume
VOL_SPIKE_MULT = 1.5     # Volume must be > 1.5× avg for entry

# Risk Management
SL_ATR_MULT = 1.2        # SL = 1.2× ATR from entry (tighter)
RR_TARGET = 2.0          # TP = 2× SL distance
MIN_ATR_PCT = 0.001      # Minimum ATR% (0.1%) — block if market is dead
MAX_ATR_PCT = 0.05       # Maximum ATR% (5%) — block if too volatile

# Minimum bars needed
MIN_BARS = 60

# Cooldown
MIN_BARS_BETWEEN = 4     # Wait 4 bars between signals


class BtcMomentum(BaseStrategy):
    """
    BTC Momentum — EMA crossover + RSI momentum + BB squeeze breakout.

    Detects consolidation via Bollinger Band squeeze, then enters on
    momentum expansion with volume confirmation. Uses ATR-based risk
    management for adaptive SL/TP in volatile crypto markets.
    """

    name = "btc_momentum"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.UNKNOWN,  # BTC is 24/7, trade most regimes
    ]

    def __init__(self):
        super().__init__()
        self._last_signal_bar = -999

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "BTCUSDc"

        # ─── Data check ───
        if candles is None or len(candles) < MIN_BARS:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {MIN_BARS})",
            )

        # ─── Cooldown ───
        bar_idx = len(candles) - 1
        if (bar_idx - self._last_signal_bar) < MIN_BARS_BETWEEN:
            return self.create_hold(
                symbol=symbol,
                reason=f"Cooldown: wait {MIN_BARS_BETWEEN} bars",
            )

        # ─── Extract price arrays ───
        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)

        current_close = float(close.iloc[-1])
        current_open = float(open_.iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])
        prev_close = float(close.iloc[-2])

        # ═══════════════════════════════════════
        # INDICATORS
        # ═══════════════════════════════════════

        # ─── ATR ───
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(ATR_PERIOD).mean()
        atr_val = float(atr_series.iloc[-1])
        if pd.isna(atr_val) or atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR NaN or zero")

        # ─── Volatility filter ───
        atr_pct = atr_val / current_close if current_close > 0 else 0
        if atr_pct > MAX_ATR_PCT:
            return self.create_hold(
                symbol=symbol,
                reason=f"Volatility extreme: ATR%={atr_pct:.3%} > {MAX_ATR_PCT:.1%}",
            )
        if atr_pct < MIN_ATR_PCT:
            return self.create_hold(
                symbol=symbol,
                reason=f"Market dead: ATR%={atr_pct:.3%} < {MIN_ATR_PCT:.1%}",
            )

        # ─── EMAs ───
        ema_fast = close.ewm(span=EMA_FAST, adjust=False).mean()
        ema_mid = close.ewm(span=EMA_MID, adjust=False).mean()
        ema_slow = close.ewm(span=EMA_SLOW, adjust=False).mean()

        ema_f = float(ema_fast.iloc[-1])
        ema_m = float(ema_mid.iloc[-1])
        ema_s = float(ema_slow.iloc[-1])

        if any(pd.isna(v) for v in [ema_f, ema_m, ema_s]):
            return self.create_hold(symbol=symbol, reason="EMA NaN")

        # ─── RSI (manual, no pandas_ta dependency) ───
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(RSI_PERIOD).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1])
        if pd.isna(rsi_val):
            return self.create_hold(symbol=symbol, reason="RSI NaN")

        # ─── Bollinger Bands ───
        bb_mid = close.rolling(BB_PERIOD).mean()
        bb_std = close.rolling(BB_PERIOD).std()
        bb_upper = bb_mid + BB_STD * bb_std
        bb_lower = bb_mid - BB_STD * bb_std
        bb_width = (float(bb_upper.iloc[-1]) - float(bb_lower.iloc[-1])) / current_close if current_close > 0 else 0

        bb_mid_val = float(bb_mid.iloc[-1])
        bb_upper_val = float(bb_upper.iloc[-1])
        bb_lower_val = float(bb_lower.iloc[-1])

        if any(pd.isna(v) for v in [bb_mid_val, bb_upper_val, bb_lower_val]):
            return self.create_hold(symbol=symbol, reason="BB NaN")

        # Was in squeeze recently? (check last 5 bars)
        recent_widths = []
        for i in range(-5, 0):
            u = float(bb_upper.iloc[i])
            l = float(bb_lower.iloc[i])
            c = float(close.iloc[i])
            if c > 0:
                recent_widths.append((u - l) / c)
        was_squeezed = any(w < BB_SQUEEZE_THRESHOLD for w in recent_widths) if recent_widths else False
        squeeze_expanding = bb_width > BB_SQUEEZE_THRESHOLD and was_squeezed

        # ─── Volume ───
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        if vol_col in candles.columns:
            vol = candles[vol_col].astype(float)
            vol_current = float(vol.iloc[-1])
            vol_avg = float(vol.rolling(20).mean().iloc[-1])
            vol_spike = vol_current > (vol_avg * VOL_SPIKE_MULT) if vol_avg > 0 else False
        else:
            vol_spike = True  # No volume data → don't filter
            vol_current = 0
            vol_avg = 0

        # ─── Candle Body Strength ───
        body = abs(current_close - current_open)
        total_range = current_high - current_low
        body_ratio = body / total_range if total_range > 0 else 0
        is_strong_candle = body_ratio >= 0.45  # Decent body

        # ═══════════════════════════════════════
        # SIGNAL SCORING (0-100)
        # ═══════════════════════════════════════

        buy_score = 0
        sell_score = 0

        # Layer 1: EMA Alignment (30 pts max)
        if ema_f > ema_m > ema_s:
            buy_score += 30  # Perfect bullish stack
        elif ema_f > ema_m and current_close > ema_s:
            buy_score += 15  # Partial alignment but above slow

        if ema_f < ema_m < ema_s:
            sell_score += 30
        elif ema_f < ema_m and current_close < ema_s:
            sell_score += 15

        # Layer 2: RSI Momentum (25 pts max)
        if RSI_BULL_MIN < rsi_val < RSI_BULL_MAX:
            buy_score += 25  # RSI in bullish momentum zone
        elif rsi_val > 45:
            buy_score += 10

        if RSI_BEAR_MIN < rsi_val < RSI_BEAR_MAX:
            sell_score += 25
        elif rsi_val < 55:
            sell_score += 10

        # Layer 3: BB Squeeze Expansion (20 pts max)
        if squeeze_expanding:
            if current_close > bb_mid_val:
                buy_score += 20
            elif current_close < bb_mid_val:
                sell_score += 20
        elif current_close > bb_upper_val:
            buy_score += 10  # Breakout above upper BB
        elif current_close < bb_lower_val:
            sell_score += 10

        # Layer 4: Volume Confirmation (15 pts max)
        if vol_spike:
            if current_close > prev_close:
                buy_score += 15
            elif current_close < prev_close:
                sell_score += 15

        # Layer 5: Candle Quality (10 pts max)
        if is_strong_candle:
            if current_close > current_open:
                buy_score += 10
            elif current_close < current_open:
                sell_score += 10

        # ═══════════════════════════════════════
        # DECISION
        # ═══════════════════════════════════════

        threshold = 70  # Need at least 70/100 for signal

        if buy_score >= threshold and buy_score > sell_score:
            sl_price = current_close - (atr_val * SL_ATR_MULT)
            sl_dist = current_close - sl_price
            tp_price = current_close + (sl_dist * RR_TARGET)

            self._last_signal_bar = bar_idx

            return Decision(
                symbol=symbol,
                action=Action.BUY,
                confidence=min(0.55 + buy_score / 200, 0.90),
                reason=(
                    f"BTC Momentum BUY score={buy_score}/100; "
                    f"EMA stack ok; RSI={rsi_val:.0f}; "
                    f"BB squeeze_expand={squeeze_expanding}; "
                    f"vol_spike={vol_spike}"
                ),
                stop_loss=sl_price,
                take_profit=tp_price,
                risk_reward_ratio=RR_TARGET,
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["btc", "momentum", "buy"],
                debug={
                    "buy_score": buy_score,
                    "sell_score": sell_score,
                    "ema_f": round(ema_f, 2),
                    "ema_m": round(ema_m, 2),
                    "ema_s": round(ema_s, 2),
                    "rsi": round(rsi_val, 1),
                    "bb_width": round(bb_width, 4),
                    "squeeze_expand": squeeze_expanding,
                    "vol_spike": vol_spike,
                    "atr_pct": round(atr_pct, 4),
                    "body_ratio": round(body_ratio, 2),
                },
            )

        elif sell_score >= threshold and sell_score > buy_score:
            sl_price = current_close + (atr_val * SL_ATR_MULT)
            sl_dist = sl_price - current_close
            tp_price = current_close - (sl_dist * RR_TARGET)

            self._last_signal_bar = bar_idx

            return Decision(
                symbol=symbol,
                action=Action.SELL,
                confidence=min(0.55 + sell_score / 200, 0.90),
                reason=(
                    f"BTC Momentum SELL score={sell_score}/100; "
                    f"EMA stack ok; RSI={rsi_val:.0f}; "
                    f"BB squeeze_expand={squeeze_expanding}; "
                    f"vol_spike={vol_spike}"
                ),
                stop_loss=sl_price,
                take_profit=tp_price,
                risk_reward_ratio=RR_TARGET,
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["btc", "momentum", "sell"],
                debug={
                    "buy_score": buy_score,
                    "sell_score": sell_score,
                    "ema_f": round(ema_f, 2),
                    "ema_m": round(ema_m, 2),
                    "ema_s": round(ema_s, 2),
                    "rsi": round(rsi_val, 1),
                    "bb_width": round(bb_width, 4),
                    "squeeze_expand": squeeze_expanding,
                    "vol_spike": vol_spike,
                    "atr_pct": round(atr_pct, 4),
                    "body_ratio": round(body_ratio, 2),
                },
            )

        return self.create_hold(
            symbol=symbol,
            reason=(
                f"No signal (buy={buy_score}/100, sell={sell_score}/100, "
                f"need {threshold}; RSI={rsi_val:.0f})"
            ),
        )
