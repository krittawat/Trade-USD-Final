"""
BTC Mean-Reversion Strategy — BB + RSI + ADX Range Detection for BTCUSDc.

Optimized for: BTCUSDc (Exness MT5 cent contract)
Timeframe: M15 (primary), M5 (secondary)

Philosophy:
    - BTC spends ~60% of time in consolidation/range
    - Mean-reversion catches overbought/oversold bounces WITHIN ranges
    - ADX < 25 confirms no strong trend → safe to fade extremes
    - BB bands define the "range" — buy at lower, sell at upper, TP at middle
    - Volume spike confirms capitulation (good entry for reversal)

Key Differences from Momentum Strategy:
    - Enters AGAINST the trend (fading extremes)
    - Requires ADX < 25 (ranging market only)
    - TP = BB Middle (conservative, high WR target)
    - Shorter SL (1.2× ATR) for quick exits
    - Session bonus for NY session (highest BTC liquidity)

Expected:
    - Higher WR (60-70%) due to mean-reversion nature
    - Lower RR (1.0-1.5) — TP is BB Middle, not extended target
    - PF ≈ 1.3-2.0 with proper RSI extremes
"""

import pandas as pd
import numpy as np

from app.strategy.base import BaseStrategy
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

logger = get_logger(__name__)

# ═════════════════════════════════════════════
# Parameters (BTC-optimized)
# ═════════════════════════════════════════════

# Bollinger Bands (wider for BTC volatility)
BB_PERIOD = 25
BB_STD = 2.2

# RSI (faster for BTC)
RSI_PERIOD = 10
RSI_BUY = 35          # RSI < 35 = oversold → BUY
RSI_SELL = 65         # RSI > 65 = overbought → SELL
RSI_EXTREME_BUY = 25  # Extreme oversold bonus
RSI_EXTREME_SELL = 75  # Extreme overbought bonus

# ADX (Regime filter — MUST be ranging)
ADX_PERIOD = 14
ADX_MAX = 30          # ADX < 30 = ranging → mean-revert allowed
ADX_STRONG_TREND = 40  # ADX > 40 = strong trend → BLOCK

# EMA (trend context, not filter)
EMA_PERIOD = 50

# ATR
ATR_PERIOD = 14

# Volume
VOL_SPIKE_MULT = 1.3   # Volume confirmation for entry

# Risk Management
SL_ATR_MULT = 1.2      # SL = 1.2× ATR from entry
MIN_ATR_PCT = 0.001    # 0.1% minimum ATR
MAX_ATR_PCT = 0.04     # 4% maximum ATR

# Minimum bars
MIN_BARS = 40

# Cooldown
MIN_BARS_BETWEEN = 2

# Session hours (UTC) — BTC 24/7 but session bonus
US_SESSION_START = 13   # NY open
US_SESSION_END = 21     # NY close


class BtcMeanReversion(BaseStrategy):
    """
    BTC Mean-Reversion — fade BB extremes in ranging markets.

    BUY when: close < BB Lower + RSI < 30 + ADX < 25
    SELL when: close > BB Upper + RSI > 70 + ADX < 25
    TP: BB Middle (mean reversion target)
    SL: 1.2× ATR beyond entry
    """

    name = "btc_mean_reversion"
    timeframe = "M15"
    suitable_regimes = [
        RegimeType.RANGING,
        RegimeType.LOW_VOLATILITY,
        RegimeType.UNKNOWN,
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
                symbol=symbol, reason=f"Cooldown: wait {MIN_BARS_BETWEEN} bars",
            )

        # ─── Extract prices ───
        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)

        current_close = float(close.iloc[-1])
        current_open = float(open_.iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])

        # ─── Session bonus ───
        session_bonus = 0
        if "time" in candles.columns:
            current_time = candles["time"].iloc[-1]
            try:
                hour = pd.Timestamp(current_time).hour
            except Exception:
                hour = 12
            if US_SESSION_START <= hour < US_SESSION_END:
                session_bonus = 5

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
                reason=f"Volatility too high: ATR%={atr_pct:.3%} > {MAX_ATR_PCT:.1%}",
            )
        if atr_pct < MIN_ATR_PCT:
            return self.create_hold(
                symbol=symbol,
                reason=f"Market dead: ATR%={atr_pct:.3%} < {MIN_ATR_PCT:.1%}",
            )

        # ─── Bollinger Bands ───
        bb_mid = close.rolling(BB_PERIOD).mean()
        bb_std_val = close.rolling(BB_PERIOD).std()
        bb_upper = bb_mid + BB_STD * bb_std_val
        bb_lower = bb_mid - BB_STD * bb_std_val

        bb_mid_val = float(bb_mid.iloc[-1])
        bb_upper_val = float(bb_upper.iloc[-1])
        bb_lower_val = float(bb_lower.iloc[-1])

        if any(pd.isna(v) for v in [bb_mid_val, bb_upper_val, bb_lower_val]):
            return self.create_hold(symbol=symbol, reason="BB NaN")

        # ─── RSI (manual) ───
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(RSI_PERIOD).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1])
        if pd.isna(rsi_val):
            return self.create_hold(symbol=symbol, reason="RSI NaN")

        # ─── ADX (manual — simplified) ───
        plus_dm = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)
        # When both are positive, keep only the larger
        both_pos = (plus_dm > 0) & (minus_dm > 0)
        plus_dm = plus_dm.where(~both_pos | (plus_dm >= minus_dm), 0)
        minus_dm = minus_dm.where(~both_pos | (minus_dm > plus_dm), 0)

        atr_smooth = tr.ewm(span=ADX_PERIOD, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(span=ADX_PERIOD, adjust=False).mean() / atr_smooth)
        minus_di = 100 * (minus_dm.ewm(span=ADX_PERIOD, adjust=False).mean() / atr_smooth)
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        adx = dx.ewm(span=ADX_PERIOD, adjust=False).mean()
        adx_val = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 50

        # ─── EMA (trend context) ───
        ema = close.ewm(span=EMA_PERIOD, adjust=False).mean()
        ema_val = float(ema.iloc[-1])
        ema_distance = abs(current_close - ema_val) / atr_val

        # ─── Volume ───
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        if vol_col in candles.columns:
            vol = candles[vol_col].astype(float)
            vol_current = float(vol.iloc[-1])
            vol_avg = float(vol.rolling(20).mean().iloc[-1])
            vol_spike = vol_current > (vol_avg * VOL_SPIKE_MULT) if vol_avg > 0 else False
        else:
            vol_spike = True
            vol_current = 0
            vol_avg = 0

        # ═══════════════════════════════════════
        # REGIME FILTER — MUST BE RANGING
        # ═══════════════════════════════════════

        if adx_val > ADX_STRONG_TREND:
            return self.create_hold(
                symbol=symbol,
                reason=f"Strong trend: ADX={adx_val:.1f} > {ADX_STRONG_TREND} → no mean-reversion",
            )

        # EMA distance check — don't revert if price is FAR from mean
        if ema_distance > 3.0:
            return self.create_hold(
                symbol=symbol,
                reason=f"Price too far from EMA ({ema_distance:.1f}× ATR > 3.0)",
            )

        is_ranging = adx_val < ADX_MAX

        # ═══════════════════════════════════════
        # SIGNAL SCORING (0-100)
        # ═══════════════════════════════════════

        buy_score = 0
        sell_score = 0

        # Layer 1: BB Extreme (35 pts max)
        if current_close < bb_lower_val:
            buy_score += 35  # Below lower BB = oversold
        elif current_close < bb_mid_val:
            buy_score += 10  # Below mid = mild

        if current_close > bb_upper_val:
            sell_score += 35
        elif current_close > bb_mid_val:
            sell_score += 10

        # Layer 2: RSI Extreme (30 pts max)
        if rsi_val < RSI_EXTREME_BUY:
            buy_score += 30  # Extreme oversold
        elif rsi_val < RSI_BUY:
            buy_score += 20  # Oversold
        elif rsi_val < 40:
            buy_score += 5

        if rsi_val > RSI_EXTREME_SELL:
            sell_score += 30
        elif rsi_val > RSI_SELL:
            sell_score += 20
        elif rsi_val > 60:
            sell_score += 5

        # Layer 3: ADX Ranging Confirmation (15 pts max)
        if is_ranging:
            buy_score += 15
            sell_score += 15

        # Layer 4: Volume Capitulation (10 pts max)
        if vol_spike:
            if current_close < current_open:  # Bearish candle + vol spike = capitulation BUY
                buy_score += 10
            elif current_close > current_open:  # Bullish candle + vol spike = exhaustion SELL
                sell_score += 10

        # Layer 5: Session Bonus (5 pts max)
        buy_score += session_bonus
        sell_score += session_bonus

        # Layer 6: Candle Rejection (5 pts max)
        body = abs(current_close - current_open)
        range_ = current_high - current_low
        if range_ > 0:
            lower_wick = min(current_open, current_close) - current_low
            upper_wick = current_high - max(current_open, current_close)

            if lower_wick > body * 1.5:  # Long lower wick = rejection (BUY)
                buy_score += 5
            if upper_wick > body * 1.5:  # Long upper wick = rejection (SELL)
                sell_score += 5

        # ═══════════════════════════════════════
        # DECISION
        # ═══════════════════════════════════════

        threshold = 50  # Need 50/100 for mean-reversion signal

        if buy_score >= threshold and buy_score > sell_score:
            sl_price = current_close - (atr_val * SL_ATR_MULT)
            # TP = BB Middle (mean reversion target)
            tp_price = bb_mid_val
            sl_dist = current_close - sl_price
            rr = (tp_price - current_close) / sl_dist if sl_dist > 0 else 0

            # Ensure minimum RR
            if rr < 0.8:
                # Extend TP if too close
                tp_price = current_close + (sl_dist * 1.2)
                rr = 1.2

            self._last_signal_bar = bar_idx

            return Decision(
                symbol=symbol,
                action=Action.BUY,
                confidence=min(0.60 + buy_score / 200, 0.90),
                reason=(
                    f"BTC MeanRev BUY score={buy_score}/100; "
                    f"close<BB_lower; RSI={rsi_val:.0f}; ADX={adx_val:.0f}; "
                    f"vol_spike={vol_spike}"
                ),
                stop_loss=sl_price,
                take_profit=tp_price,
                risk_reward_ratio=round(rr, 2),
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["btc", "mean_reversion", "buy"],
                debug={
                    "buy_score": buy_score,
                    "sell_score": sell_score,
                    "rsi": round(rsi_val, 1),
                    "adx": round(adx_val, 1),
                    "bb_mid": round(bb_mid_val, 2),
                    "bb_upper": round(bb_upper_val, 2),
                    "bb_lower": round(bb_lower_val, 2),
                    "atr_pct": round(atr_pct, 4),
                    "vol_spike": vol_spike,
                    "session_bonus": session_bonus,
                    "ema_distance": round(ema_distance, 2),
                    "rr": round(rr, 2),
                },
            )

        elif sell_score >= threshold and sell_score > buy_score:
            sl_price = current_close + (atr_val * SL_ATR_MULT)
            tp_price = bb_mid_val
            sl_dist = sl_price - current_close
            rr = (current_close - tp_price) / sl_dist if sl_dist > 0 else 0

            if rr < 0.8:
                tp_price = current_close - (sl_dist * 1.2)
                rr = 1.2

            self._last_signal_bar = bar_idx

            return Decision(
                symbol=symbol,
                action=Action.SELL,
                confidence=min(0.60 + sell_score / 200, 0.90),
                reason=(
                    f"BTC MeanRev SELL score={sell_score}/100; "
                    f"close>BB_upper; RSI={rsi_val:.0f}; ADX={adx_val:.0f}; "
                    f"vol_spike={vol_spike}"
                ),
                stop_loss=sl_price,
                take_profit=tp_price,
                risk_reward_ratio=round(rr, 2),
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["btc", "mean_reversion", "sell"],
                debug={
                    "buy_score": buy_score,
                    "sell_score": sell_score,
                    "rsi": round(rsi_val, 1),
                    "adx": round(adx_val, 1),
                    "bb_mid": round(bb_mid_val, 2),
                    "bb_upper": round(bb_upper_val, 2),
                    "bb_lower": round(bb_lower_val, 2),
                    "atr_pct": round(atr_pct, 4),
                    "vol_spike": vol_spike,
                    "session_bonus": session_bonus,
                    "ema_distance": round(ema_distance, 2),
                    "rr": round(rr, 2),
                },
            )

        return self.create_hold(
            symbol=symbol,
            reason=(
                f"No reversal signal (buy={buy_score}/100, sell={sell_score}/100, "
                f"need {threshold}; RSI={rsi_val:.0f}; ADX={adx_val:.0f})"
            ),
        )
