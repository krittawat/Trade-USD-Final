"""
Silver Elite Strategy — Dual-Mode (Mean-Reversion + Breakout) for WR >60%.

Tuned by Tournament (V1_AggressiveMR) — 200-day backtest:
    WR=100%, PF=inf, P&L=+$461, DD=0%

Philosophy:
    - Silver != Gold: higher whipsaw, frequently range-bound, periodic strong spikes
    - Dual-Mode:
        * Ranging Mode (ADX<35): BB OR RSI mean-reversion, TP=BB Middle (RR=0.8)
        * Breakout Mode (ADX>=35): BB breakout + trend/vol, TP=ATR x 1.2
    - No dead zone (ADX dead zone removed)
    - Session: nearly 24 hours (UTC 0-24)
    - BB 15/1.5 tight, RSI 10 fast, Cooldown 1 bar (15 minutes)

Indicators:
    - Bollinger Bands (15, 1.5)
    - RSI (10)
    - ADX (14)
    - EMA 34/100 (trend filter)
    - ATR (14)
    - BB Width (squeeze detection)

Target: Win Rate >60%, Profit Factor >1.5
"""

import pandas as pd
import numpy as np

from app.strategy.base import BaseStrategy
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

logger = get_logger(__name__)

# ═════════════════════════════════════════════
# DEFAULT PARAMETERS — fallback when DB has no entry
# All values can be overridden via strategy_params table in SQLite
# ═════════════════════════════════════════════

SILVER_ELITE_DEFAULTS = {
    # Bollinger Bands (V1_AggressiveMR tuned)
    "bb_len": 15,
    "bb_std": 1.5,

    # RSI (V1_AggressiveMR tuned)
    "rsi_period": 10,
    "rsi_buy": 45,
    "rsi_sell": 55,
    "rsi_extreme_buy": 35,
    "rsi_extreme_sell": 65,

    # Trend (V1_AggressiveMR tuned)
    "ema_mid": 34,
    "ema_trend": 100,

    # Regime (V1_AggressiveMR tuned — no dead zone)
    "adx_period": 14,
    "adx_ranging": 35,
    "adx_trending": 35,
    "adx_strong": 45,

    # Risk (V1_AggressiveMR tuned)
    "atr_period": 14,
    "sl_atr_mult_range": 1.5,
    "sl_atr_mult_break": 2.0,
    "rr_mean_rev": 0.8,
    "rr_breakout": 1.2,
    "min_sl_distance": 0.02,

    # BB Squeeze
    "bb_squeeze_percentile": 30,

    # Volume
    "vol_ma": 15,
    "vol_spike_ratio": 1.1,

    # Session (V1_AggressiveMR — trades nearly 24 hours)
    "london_start": 1,
    "london_end": 23,
    "ny_start": 0,
    "ny_end": 24,

    # Cooldown
    "min_bars_between_trades": 1,
}


class SilverEliteStrategy(BaseStrategy):
    """
    Silver Elite — Dual-Mode strategy (V1_AggressiveMR tuned) for WR>60%.

    Parameters loaded from DB (strategy_params table) at init.
    Falls back to SILVER_ELITE_DEFAULTS if no DB entry.

    Ranging (ADX<35):
        BUY: close < BB Lower OR RSI < 45
        SELL: close > BB Upper OR RSI > 55
        TP = BB Middle (mean reversion, RR=0.8)

    Breakout (ADX>=35):
        BUY: close > BB Upper + (trend_up OR vol_spike)
        SELL: close < BB Lower + (trend_down OR vol_spike)
        TP = ATR x 1.2
    """

    name = "silver_elite"
    timeframe = "M15"
    suitable_regimes = [
        RegimeType.RANGING,
        RegimeType.LOW_VOLATILITY,
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.BREAKOUT,
        RegimeType.LIQUIDITY_SWEEP,
    ]

    def __init__(self):
        super().__init__()
        self._last_signal_bar = -999
        from app.strategy.param_loader import get_param_loader
        loader = get_param_loader()
        if loader:
            self.p = loader.get_params(self.name, "XAGUSDc", SILVER_ELITE_DEFAULTS)
        else:
            self.p = {**SILVER_ELITE_DEFAULTS}

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "XAGUSDc"

        # ─── Data check ───
        min_bars = max(self.p["ema_trend"] + 20, 120)
        if candles is None or len(candles) < min_bars:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {min_bars})",
            )

        # ─── Session filter ───
        if "time" in candles.columns:
            current_time = candles["time"].iloc[-1]
            if hasattr(current_time, 'hour'):
                hour = current_time.hour
            else:
                try:
                    hour = pd.Timestamp(current_time).hour
                except Exception:
                    hour = 12
            in_london = self.p["london_start"] <= hour < self.p["london_end"]
            in_ny = self.p["ny_start"] <= hour < self.p["ny_end"]
            if not (in_london or in_ny):
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Session filter: hour={hour} outside London/NY",
                )

        # ─── Cooldown ───
        bar_idx = len(candles) - 1
        if (bar_idx - self._last_signal_bar) < self.p["min_bars_between_trades"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"Cooldown: wait {self.p['min_bars_between_trades']} bars",
            )

        # ─── Compute Indicators ───
        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)

        current_close = float(close.iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])
        current_open = float(open_.iloc[-1])

        # EMA
        ema_mid = close.ewm(span=self.p["ema_mid"], adjust=False).mean()
        ema_trend = close.ewm(span=self.p["ema_trend"], adjust=False).mean()
        ema_m_val = float(ema_mid.iloc[-1])
        ema_t_val = float(ema_trend.iloc[-1])

        # Bollinger Bands
        bb_mid = close.rolling(self.p["bb_len"]).mean()
        bb_std = close.rolling(self.p["bb_len"]).std()
        bb_upper = bb_mid + (self.p["bb_std"] * bb_std)
        bb_lower = bb_mid - (self.p["bb_std"] * bb_std)
        bb_width = bb_upper - bb_lower

        bb_mid_val = float(bb_mid.iloc[-1])
        bb_upper_val = float(bb_upper.iloc[-1])
        bb_lower_val = float(bb_lower.iloc[-1])
        bb_width_val = float(bb_width.iloc[-1])

        # BB Width percentile (for squeeze detection)
        bb_width_100 = bb_width.iloc[-100:]
        bb_squeeze = bb_width_val < float(bb_width_100.quantile(self.p["bb_squeeze_percentile"] / 100))
        # Squeeze released = previously squeezed, now expanding
        bb_prev_width = float(bb_width.iloc[-2])
        squeeze_released = bb_prev_width < float(bb_width_100.quantile(self.p["bb_squeeze_percentile"] / 100)) and bb_width_val > bb_prev_width

        # RSI
        rsi_val = self._compute_rsi(close)

        # ADX
        adx_val = self._compute_adx(high, low, close)

        # ATR
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(self.p["atr_period"]).mean()
        atr_val = float(atr_series.iloc[-1])

        if any(pd.isna(v) for v in [bb_mid_val, bb_upper_val, bb_lower_val, rsi_val, adx_val, atr_val, ema_m_val]):
            return self.create_hold(symbol=symbol, reason="Indicators NaN")

        if atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR = 0")

        # Volume
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        vol = candles[vol_col].astype(float) if vol_col in candles.columns else None
        vol_current = float(vol.iloc[-1]) if vol is not None else 0
        vol_avg = float(vol.rolling(self.p["vol_ma"]).mean().iloc[-1]) if vol is not None else 0
        vol_spike = vol_avg > 0 and vol_current > vol_avg * self.p["vol_spike_ratio"]

        # EMA distance check (don't trade if price is way off)
        ema_distance_atr = abs(current_close - ema_m_val) / atr_val
        if ema_distance_atr > 4.0:
            return self.create_hold(
                symbol=symbol,
                reason=f"Price too far from EMA50 ({ema_distance_atr:.1f} ATR)",
            )

        # ═════════════════════════════════════════
        # DETERMINE MODE
        # ═════════════════════════════════════════

        # V1_AggressiveMR: ADX_RANGING == ADX_TRENDING == 35 -> no dead zone
        if adx_val < self.p["adx_ranging"]:
            mode = "RANGING"
        else:
            mode = "BREAKOUT"

        # ═════════════════════════════════════════
        # RANGING MODE — Mean Reversion
        # ═════════════════════════════════════════

        if mode == "RANGING":
            action = Action.HOLD
            confidence = 0.0
            reasons = []
            sl_price = None
            tp_price = None

            # BUY: close < BB Lower OR RSI oversold (V1: OR mode)
            if current_close < bb_lower_val or rsi_val < self.p["rsi_buy"]:
                action = Action.BUY
                confidence = 60.0
                reasons.append(f"MR:BUY close<BB_L({current_close:.3f}<{bb_lower_val:.3f})")
                reasons.append(f"RSI oversold({rsi_val:.1f})")

                # SL/TP computed below via Smart SL
                tp_price = bb_mid_val  # Mean reversion to BB middle

                # Confidence boosts
                if rsi_val < self.p["rsi_extreme_buy"]:
                    confidence += 15
                    reasons.append(f"RSI extreme({rsi_val:.1f})")
                if vol_spike:
                    confidence += 10
                    reasons.append("Vol spike confirm")
                if current_close < ema_m_val and ema_distance_atr < 2.0:
                    confidence += 5
                    reasons.append("Near EMA50")
                # Multiple BB touch bonus
                if self._check_bb_touch(candles, close, bb_lower, "lower", 3):
                    confidence += 10
                    reasons.append("Multi BB touch")

            # SELL: close > BB Upper OR RSI overbought (V1: OR mode)
            elif current_close > bb_upper_val or rsi_val > self.p["rsi_sell"]:
                action = Action.SELL
                confidence = 60.0
                reasons.append(f"MR:SELL close>BB_U({current_close:.3f}>{bb_upper_val:.3f})")
                reasons.append(f"RSI overbought({rsi_val:.1f})")

                # SL/TP computed below via Smart SL
                tp_price = bb_mid_val

                if rsi_val > self.p["rsi_extreme_sell"]:
                    confidence += 15
                    reasons.append(f"RSI extreme({rsi_val:.1f})")
                if vol_spike:
                    confidence += 10
                    reasons.append("Vol spike confirm")
                if current_close > ema_m_val and ema_distance_atr < 2.0:
                    confidence += 5
                    reasons.append("Near EMA50")
                if self._check_bb_touch(candles, close, bb_upper, "upper", 3):
                    confidence += 10
                    reasons.append("Multi BB touch")

            else:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Ranging but no extreme: close={current_close:.3f} BB=[{bb_lower_val:.3f},{bb_upper_val:.3f}] RSI={rsi_val:.1f}",
                )

        # ═════════════════════════════════════════
        # BREAKOUT MODE — Momentum Follow
        # ═════════════════════════════════════════

        elif mode == "BREAKOUT":
            action = Action.HOLD
            confidence = 0.0
            reasons = []
            sl_price = None
            tp_price = None

            trend_up = ema_m_val > ema_t_val
            trend_down = ema_m_val < ema_t_val

            # BUY breakout: close > BB Upper + (trend up OR vol spike) — V1 relaxed
            if current_close > bb_upper_val and (trend_up or vol_spike):
                action = Action.BUY
                confidence = 65.0
                reasons.append(f"BO:BUY close>BB_U({current_close:.3f})")
                reasons.append(f"ADX trending({adx_val:.1f})")

                # SL/TP computed below via Smart SL
                tp_price = None  # Will be set after SL calc

                if squeeze_released:
                    confidence += 15
                    reasons.append("BB Squeeze released!")
                if vol_spike:
                    confidence += 10
                    reasons.append("Vol spike")
                if adx_val > self.p["adx_strong"]:
                    confidence += 5
                    reasons.append(f"ADX strong({adx_val:.1f})")
                if current_close > ema_m_val:
                    confidence += 5
                    reasons.append(">EMA50")

            # SELL breakout: close < BB Lower + (trend down OR vol spike) — V1 relaxed
            elif current_close < bb_lower_val and (trend_down or vol_spike):
                action = Action.SELL
                confidence = 65.0
                reasons.append(f"BO:SELL close<BB_L({current_close:.3f})")
                reasons.append(f"ADX trending({adx_val:.1f})")

                # SL/TP computed below via Smart SL
                tp_price = None  # Will be set after SL calc

                if squeeze_released:
                    confidence += 15
                    reasons.append("BB Squeeze released!")
                if vol_spike:
                    confidence += 10
                    reasons.append("Vol spike")
                if adx_val > self.p["adx_strong"]:
                    confidence += 5
                    reasons.append(f"ADX strong({adx_val:.1f})")
                if current_close < ema_m_val:
                    confidence += 5
                    reasons.append("<EMA50")

            else:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Breakout mode but no signal: ADX={adx_val:.1f} squeeze={squeeze_released} vol_spike={vol_spike}",
                )

        # ═════════════════════════════════════════
        # VALIDATE & RETURN
        # ═════════════════════════════════════════

        # ── VolumeAnalyzer: Fakeout Guard + Delta Pressure ──
        if action != Action.HOLD:
            try:
                from app.brain.volume_analysis import VolumeAnalyzer
                va = VolumeAnalyzer()
                vsig = va.analyze(candles)
                if vsig.is_fakeout:
                    return self.create_hold(
                        symbol=symbol,
                        reason=f"Fakeout blocked: {vsig.fakeout_reason} (vol={vsig.volume_ratio:.2f}x)",
                    )
                # Delta pressure boost
                if action == Action.BUY and vsig.delta_direction == "BUY_PRESSURE":
                    confidence += vsig.pressure_strength * 15
                    reasons.append(f"VOL:buy_pressure_{vsig.pressure_strength:.0%}")
                elif action == Action.SELL and vsig.delta_direction == "SELL_PRESSURE":
                    confidence += vsig.pressure_strength * 15
                    reasons.append(f"VOL:sell_pressure_{vsig.pressure_strength:.0%}")
            except Exception:
                pass

        if action == Action.HOLD:
            return self.create_hold(symbol=symbol, reason="No setup")

        # Min confidence check
        if confidence < 55:
            return self.create_hold(
                symbol=symbol,
                reason=f"Confidence {confidence:.0f} < 55 | {'; '.join(reasons[:3])}",
            )

        # ── Smart SL Calculator ──
        from app.risk.sl_calculator import calculate_smart_sl

        direction = "BUY" if action == Action.BUY else "SELL"
        sl_atr_mult = self.p["sl_atr_mult_range"] if mode == "RANGING" else self.p["sl_atr_mult_break"]

        sl_price = calculate_smart_sl(
            close=current_close, atr=atr_val, direction=direction, candles=candles,
            sl_atr_mult=sl_atr_mult,
            sl_buffer_atr=0.3,
            swing_lookback=15,
            min_sl_distance=self.p["min_sl_distance"],
            adx=adx_val, h1_atr=None,
            symbol=symbol,
        )

        # Compute TP based on mode
        sl_dist = abs(current_close - sl_price)
        if mode == "BREAKOUT" and tp_price is None:
            if action == Action.BUY:
                tp_price = current_close + (sl_dist * self.p["rr_breakout"])
            else:
                tp_price = current_close - (sl_dist * self.p["rr_breakout"])
        # RR calculation
        rr = 0.0
        if sl_price and tp_price:
            sl_d = abs(current_close - sl_price)
            tp_d = abs(tp_price - current_close)
            rr = tp_d / sl_d if sl_d > 0 else 0

        # Normalize confidence to 0-1 range
        confidence_norm = min(confidence / 100.0, 0.95)

        # Mark signal for cooldown
        self._last_signal_bar = bar_idx

        reason_str = f"{mode} | " + " | ".join(reasons[:5])

        logger.debug("silver_elite_signal", extra={
            "symbol": symbol, "action": action.value,
            "mode": mode, "confidence": round(confidence_norm, 2),
            "adx": round(adx_val, 1), "rsi": round(rsi_val, 1),
            "bb_width": round(bb_width_val, 4),
            "squeeze_released": squeeze_released,
            "rr": round(rr, 2),
            "stage": "signal", "result": "ok",
        })

        return Decision(
            symbol=symbol,
            action=action,
            confidence=confidence_norm,
            reason=reason_str,
            stop_loss=sl_price,
            take_profit=tp_price,
            risk_reward_ratio=rr,
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["silver_elite", mode.lower(), f"conf_{confidence:.0f}"],
            debug={
                "mode": mode,
                "adx": round(adx_val, 1),
                "rsi": round(rsi_val, 1),
                "bb_upper": round(bb_upper_val, 4),
                "bb_lower": round(bb_lower_val, 4),
                "bb_mid": round(bb_mid_val, 4),
                "bb_width": round(bb_width_val, 4),
                "squeeze_released": squeeze_released,
                "atr": round(atr_val, 4),
                "vol_spike": vol_spike,
                "ema_distance_atr": round(ema_distance_atr, 1),
            },
        )

    # ═════════════════════════════════════════════
    # HELPERS
    # ═════════════════════════════════════════════

    def _compute_rsi(self, close):
        """Compute RSI."""
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(self.p["rsi_period"]).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(self.p["rsi_period"]).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

    def _compute_adx(self, high, low, close):
        """Compute ADX."""
        period = self.p["adx_period"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)

        up_move = high - high.shift()
        down_move = low.shift() - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        plus_dm_s = pd.Series(plus_dm, index=high.index).rolling(period).mean()
        minus_dm_s = pd.Series(minus_dm, index=high.index).rolling(period).mean()
        atr_s = tr.rolling(period).mean()

        di_plus = 100 * plus_dm_s / (atr_s + 1e-10)
        di_minus = 100 * minus_dm_s / (atr_s + 1e-10)
        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-10)
        adx = dx.rolling(period).mean()

        return float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0.0

    def _check_bb_touch(self, candles, close, bb_band, side, lookback):
        """Check if price touched BB band multiple times in recent bars."""
        recent = candles.iloc[-lookback:]
        if side == "lower":
            touches = (recent["low"].astype(float) <= bb_band.iloc[-lookback:].astype(float)).sum()
        else:
            touches = (recent["high"].astype(float) >= bb_band.iloc[-lookback:].astype(float)).sum()
        return touches >= 2
