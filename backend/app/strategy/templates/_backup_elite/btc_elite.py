"""
BTC Elite Strategy — 5-Layer 100-Point Scoring for Short-Term BTC Speculation.

Optimized for: BTCUSDc (Exness MT5 cent contract)
Timeframe: M5

Philosophy:
    - BTC is 24/7, momentum-driven, high volatility → need wider SL + fast signals
    - 5-Layer scoring ensures confluence before entry
    - Dual-Mode: Trend Follow (ADX≥18) vs Mean Reversion (ADX<18)
    - Adaptive RR: higher score → wider TP → bigger profit per trade
    - Pressure data (buy/sell flow from TickVolumeAnalyzer) boosts conviction

5-Layer Scoring (100 points):
    1. Trend Alignment (EMA 9/21/50/200)        — 25 pts
    2. Momentum (ADX + RSI + MACD histogram)    — 25 pts
    3. BTC Volatility (ATR regime + BB squeeze) — 20 pts
    4. Volume + Pressure (vol spike + flow)     — 15 pts
    5. Risk Quality (structure SL + RR)         — 15 pts

Decision Thresholds:
    >= 80 → Trade RR=2.5 (Elite)
    >= 70 → Trade RR=2.0 (Strong)
    >= 60 → Trade RR=1.5 (Standard)
    <  60 → HOLD

Session (BTC trades 24/7):
    - US Equities window (13-21 UTC) → +5 bonus (highest BTC volume)
    - No session block (always tradeable), but extreme low-vol filter at ATR level

Target: Win Rate >55%, Profit Factor >1.5
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

BTC_ELITE_DEFAULTS = {
    # EMA
    "ema_fast": 9,
    "ema_mid": 21,
    "ema_slow": 50,
    "ema_trend": 200,

    # ADX
    "adx_period": 14,
    "adx_min": 18,       # Lower than gold (BTC trends faster)
    "adx_strong": 28,

    # RSI
    "rsi_period": 10,     # Faster for crypto
    "rsi_ob": 75,
    "rsi_os": 25,
    "rsi_extreme_high": 85,
    "rsi_extreme_low": 15,

    # MACD
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,

    # Bollinger Bands (Mean-Reversion mode)
    "bb_len": 20,
    "bb_std": 2.0,
    "bb_squeeze_percentile": 25,

    # Volume
    "vol_ma": 20,
    "vol_spike_ratio": 1.3,

    # Structure
    "swing_lookback": 12,    # Shorter for M5

    # Risk — wider SL for BTC volatility
    "atr_period": 14,
    "sl_atr_mult": 2.0,      # Trend mode SL
    "sl_atr_mult_mr": 1.5,   # Mean-reversion SL
    "sl_buffer_atr": 0.3,
    "min_sl_pct": 0.002,     # 0.2% minimum SL distance (BTC price-based)

    # Adaptive RR
    "min_score_trade": 60,
    "rr_standard": 1.5,
    "rr_strong": 2.0,
    "rr_elite": 2.5,

    # Mean-Reversion mode
    "rsi_mr_buy": 30,
    "rsi_mr_sell": 70,
    "rr_mean_rev": 1.0,

    # Volatility filter
    "atr_pct_max": 0.05,     # Block if ATR% > 5% (extreme whipsaw)
    "atr_pct_min": 0.002,    # Block if ATR% < 0.2% (dead market)

    # Session (UTC hours — BTC is 24/7)
    "us_session_start": 13,
    "us_session_end": 21,

    # Cooldown
    "min_bars_between_trades": 2,
}


class BtcEliteStrategy(BaseStrategy):
    """
    BTC Elite — 5-Layer 100 Point Scoring for Short-Term Profit.

    Parameters loaded from DB (strategy_params table) at init.
    Falls back to BTC_ELITE_DEFAULTS if no DB entry.

    Modes:
        - Trend Follow (ADX >= adx_min): 5-layer scoring → confluence trade
        - Mean Reversion (ADX < adx_min): BB + RSI extremes → TP at BB mid

    Scoring (Trend Mode):
        1. Trend Alignment (EMA stack)       — 25 pts
        2. Momentum (ADX + DI + RSI + MACD)  — 25 pts
        3. BTC Volatility (ATR% + BB squeeze) — 20 pts
        4. Volume + Pressure                 — 15 pts
        5. Risk Quality (swing SL + RR)      — 15 pts
    """

    name = "btc_elite"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.BREAKOUT,
        RegimeType.RANGING,
    ]

    def __init__(self) -> None:
        """Load params from DB, fallback to BTC_ELITE_DEFAULTS."""
        super().__init__()
        self._last_signal_bar = -999
        from app.strategy.param_loader import get_param_loader
        loader = get_param_loader()
        if loader:
            self.p = loader.get_params(self.name, "BTCUSDc", BTC_ELITE_DEFAULTS)
        else:
            self.p = {**BTC_ELITE_DEFAULTS}

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "BTCUSDc"
        pressure = kwargs.get("pressure")

        # ─── Data check ───
        min_bars = self.p["ema_trend"] + 20
        if candles is None or len(candles) < min_bars:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {min_bars})",
            )

        # ─── Cooldown ───
        bar_idx = len(candles) - 1
        if (bar_idx - self._last_signal_bar) < self.p["min_bars_between_trades"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"Cooldown: wait {self.p['min_bars_between_trades']} bars",
            )

        # ─── Session bonus (BTC is 24/7 — no block, just bonus) ───
        session_bonus = 0
        if "time" in candles.columns:
            current_time = candles["time"].iloc[-1]
            if hasattr(current_time, 'hour'):
                hour = current_time.hour
            else:
                try:
                    hour = pd.Timestamp(current_time).hour
                except Exception:
                    hour = 12
            if self.p["us_session_start"] <= hour < self.p["us_session_end"]:
                session_bonus = 5  # US equities = highest BTC volume

        # ─── Extract price arrays ───
        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)

        current_close = float(close.iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])
        current_open = float(open_.iloc[-1])
        prev_close = float(close.iloc[-2])
        prev_open = float(open_.iloc[-2])
        prev_high = float(high.iloc[-2])
        prev_low = float(low.iloc[-2])

        # ─── ATR ───
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(self.p["atr_period"]).mean()
        atr_val = float(atr_series.iloc[-1])
        if pd.isna(atr_val) or atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR NaN or zero")

        # ─── Volatility filter (BTC-specific — block extreme whipsaw) ───
        atr_pct = atr_val / current_close if current_close > 0 else 0
        if atr_pct > self.p["atr_pct_max"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"Volatility too extreme: ATR%={atr_pct:.3%} > {self.p['atr_pct_max']:.1%}",
            )
        if atr_pct < self.p["atr_pct_min"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"Market dead: ATR%={atr_pct:.3%} < {self.p['atr_pct_min']:.1%}",
            )

        # ─── Compute Indicators ───
        ema_f = close.ewm(span=self.p["ema_fast"], adjust=False).mean()
        ema_m = close.ewm(span=self.p["ema_mid"], adjust=False).mean()
        ema_s = close.ewm(span=self.p["ema_slow"], adjust=False).mean()
        ema_t = close.ewm(span=self.p["ema_trend"], adjust=False).mean()

        ema_f_val = float(ema_f.iloc[-1])
        ema_m_val = float(ema_m.iloc[-1])
        ema_s_val = float(ema_s.iloc[-1])
        ema_t_val = float(ema_t.iloc[-1])

        if any(pd.isna(v) for v in [ema_f_val, ema_m_val, ema_s_val, ema_t_val]):
            return self.create_hold(symbol=symbol, reason="EMA NaN")

        # ADX + DI
        adx_val, di_plus, di_minus = self._compute_adx(high, low, close)

        # ═════════════════════════════════════════
        # MODE SWITCH: Trend Follow vs Mean Reversion
        # ═════════════════════════════════════════
        is_ranging = (adx_val < self.p["adx_min"]) or regime in (
            RegimeType.RANGING, RegimeType.LOW_VOLATILITY
        )

        if is_ranging:
            return self._analyze_mean_reversion(
                candles, close, high, low, open_,
                current_close, current_high, current_low, current_open,
                atr_val, adx_val, atr_pct, symbol, bar_idx, pressure,
            )

        # ─── Trend Mode ─── continues below

        # RSI
        rsi_val = self._compute_rsi(close)

        # RSI extreme block — don't enter at exhaustion
        if rsi_val > self.p["rsi_extreme_high"] or rsi_val < self.p["rsi_extreme_low"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"RSI extreme: {rsi_val:.1f} (wait for pullback)",
            )

        # MACD
        macd_hist = self._compute_macd(close)

        # Volume
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        vol = candles[vol_col].astype(float) if vol_col in candles.columns else None
        vol_current = float(vol.iloc[-1]) if vol is not None else 0
        vol_avg = float(vol.rolling(self.p["vol_ma"]).mean().iloc[-1]) if vol is not None else 0
        vol_spike = vol_avg > 0 and vol_current > vol_avg * self.p["vol_spike_ratio"]

        # VWAP approximation
        vwap_val = None
        if vol is not None and len(vol) >= self.p["vol_ma"]:
            try:
                cv = (close * vol).rolling(self.p["vol_ma"]).sum()
                sv = vol.rolling(self.p["vol_ma"]).sum()
                vwap_series = cv / sv
                vwap_val = float(vwap_series.iloc[-1])
            except Exception:
                pass

        # Bollinger Bands (for squeeze detection in trend mode too)
        bb_mid = close.rolling(self.p["bb_len"]).mean()
        bb_std_s = close.rolling(self.p["bb_len"]).std()
        bb_upper = bb_mid + (self.p["bb_std"] * bb_std_s)
        bb_lower = bb_mid - (self.p["bb_std"] * bb_std_s)
        bb_width = bb_upper - bb_lower

        bb_width_val = float(bb_width.iloc[-1]) if not pd.isna(bb_width.iloc[-1]) else 0
        bb_prev_width = float(bb_width.iloc[-2]) if len(bb_width) > 1 and not pd.isna(bb_width.iloc[-2]) else bb_width_val

        # BB squeeze detection
        bb_width_window = bb_width.iloc[-100:] if len(bb_width) >= 100 else bb_width
        bb_squeeze_thresh = float(bb_width_window.quantile(self.p["bb_squeeze_percentile"] / 100))
        bb_is_squeezed = bb_width_val < bb_squeeze_thresh
        squeeze_released = bb_prev_width < bb_squeeze_thresh and bb_width_val > bb_prev_width

        # Swing High/Low
        lookback = min(self.p["swing_lookback"], len(candles) - 2)
        swing_data = candles.iloc[-(lookback + 1):-1]
        swing_high = float(swing_data["high"].max())
        swing_low = float(swing_data["low"].min())

        # ═════════════════════════════════════════
        # DETERMINE DIRECTION
        # ═════════════════════════════════════════

        trend_bullish = ema_s_val > ema_t_val
        trend_bearish = ema_s_val < ema_t_val

        # ═════════════════════════════════════════
        # 5-LAYER SCORING (100 pts + session bonus)
        # ═════════════════════════════════════════

        buy_score = 0
        sell_score = 0
        buy_reasons = []
        sell_reasons = []

        # ── Layer 1: Trend Alignment (25 pts) ──
        b1, s1, br1, sr1 = self._score_trend(
            ema_f_val, ema_m_val, ema_s_val, ema_t_val, current_close
        )
        buy_score += b1
        sell_score += s1
        buy_reasons.extend(br1)
        sell_reasons.extend(sr1)

        # ── Layer 2: Momentum (25 pts) ──
        b2, s2, br2, sr2 = self._score_momentum(
            adx_val, di_plus, di_minus, rsi_val, macd_hist
        )
        buy_score += b2
        sell_score += s2
        buy_reasons.extend(br2)
        sell_reasons.extend(sr2)

        # ── Layer 3: BTC Volatility (20 pts) ──
        b3, s3, br3, sr3 = self._score_btc_volatility(
            atr_pct, bb_is_squeezed, squeeze_released, bb_width_val
        )
        buy_score += b3
        sell_score += s3
        buy_reasons.extend(br3)
        sell_reasons.extend(sr3)

        # ── Layer 4: Volume + Pressure (15 pts) ──
        b4, s4, br4, sr4 = self._score_volume_pressure(
            vol_current, vol_avg, vol_spike, current_close, vwap_val, pressure
        )
        buy_score += b4
        sell_score += s4
        buy_reasons.extend(br4)
        sell_reasons.extend(sr4)

        # ── Layer 5: Risk Quality (15 pts) ──
        b5, s5, br5, sr5 = self._score_risk_quality(
            current_close, swing_high, swing_low, atr_val
        )
        buy_score += b5
        sell_score += s5
        buy_reasons.extend(br5)
        sell_reasons.extend(sr5)

        # ── Session bonus ──
        buy_score += session_bonus
        sell_score += session_bonus

        # ── VolumeAnalyzer: Fakeout Guard + Delta Pressure ──
        try:
            from app.brain.volume_analysis import VolumeAnalyzer
            va = VolumeAnalyzer()
            vsig = va.analyze(candles)
            if vsig.is_fakeout:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Fakeout blocked: {vsig.fakeout_reason} (vol={vsig.volume_ratio:.2f}x)",
                )
            buy_score += vsig.buy_score
            sell_score += vsig.sell_score
            buy_reasons.extend(vsig.buy_reasons)
            sell_reasons.extend(vsig.sell_reasons)
        except Exception:
            pass

        # ═════════════════════════════════════════
        # DECISION
        # ═════════════════════════════════════════

        if buy_score >= sell_score and trend_bullish:
            total_score = buy_score
            action = Action.BUY
            reasons = buy_reasons
            is_buy = True
        elif sell_score > buy_score and trend_bearish:
            total_score = sell_score
            action = Action.SELL
            reasons = sell_reasons
            is_buy = False
        else:
            max_s = max(buy_score, sell_score)
            side = "BUY" if buy_score >= sell_score else "SELL"
            return self.create_hold(
                symbol=symbol,
                reason=f"Score {max_s}/100 ({side}) trend not aligned",
            )

        # Score threshold
        if total_score < self.p["min_score_trade"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"Score {total_score}/100 < {self.p['min_score_trade']} min",
            )

        # ─── Adaptive RR ───
        if total_score >= 80:
            rr = self.p["rr_elite"]
            tier = "🔥 Elite"
        elif total_score >= 70:
            rr = self.p["rr_strong"]
            tier = "✅ Strong"
        else:
            rr = self.p["rr_standard"]
            tier = "🎯 Standard"

        # ─── Confidence ───
        confidence = min(0.55 + (total_score - 55) * 0.01, 0.95)

        # ─── SL / TP (Smart SL Calculator + Anti-Hunt) ───
        from app.risk.sl_calculator import calculate_smart_sl

        direction = "BUY" if is_buy else "SELL"
        min_sl_abs = current_close * self.p["min_sl_pct"]

        sl_price = calculate_smart_sl(
            close=current_close, atr=atr_val, direction=direction, candles=candles,
            sl_atr_mult=self.p["sl_atr_mult"],
            sl_buffer_atr=self.p["sl_buffer_atr"],
            swing_lookback=self.p["swing_lookback"],
            min_sl_distance=min_sl_abs,
            adx=adx_val, h1_atr=None,
            symbol=symbol,
        )

        sl_dist = abs(current_close - sl_price)
        if is_buy:
            tp_price = current_close + (sl_dist * rr)
        else:
            tp_price = current_close - (sl_dist * rr)

        # Mark cooldown
        self._last_signal_bar = bar_idx

        reason_str = f"{tier} Score={total_score}/100 RR={rr} | " + "; ".join(reasons[:6])

        logger.debug("btc_elite_signal", extra={
            "symbol": symbol, "action": action.value,
            "total_score": total_score, "confidence": round(confidence, 2),
            "rr": rr, "atr": round(atr_val, 2), "atr_pct": round(atr_pct, 4),
            "adx": round(adx_val, 1), "rsi": round(rsi_val, 1),
            "session_bonus": session_bonus,
            "stage": "signal", "result": "ok",
        })

        return Decision(
            symbol=symbol,
            action=action,
            confidence=confidence,
            reason=reason_str,
            stop_loss=sl_price,
            take_profit=tp_price,
            risk_reward_ratio=rr,
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["btc_elite", tier.split()[0], f"score_{total_score}"],
            debug={
                "buy_score": buy_score, "sell_score": sell_score,
                "total_score": total_score, "rr": rr,
                "adx": round(adx_val, 1), "rsi": round(rsi_val, 1),
                "macd_hist": round(macd_hist, 6),
                "atr": round(atr_val, 2), "atr_pct": round(atr_pct, 4),
                "swing_high": round(swing_high, 2),
                "swing_low": round(swing_low, 2),
                "bb_squeezed": bb_is_squeezed,
                "squeeze_released": squeeze_released,
                "vol_spike": vol_spike,
                "session_bonus": session_bonus,
            },
        )

    # ═════════════════════════════════════════════
    # MEAN REVERSION MODE — BB + RSI
    # ═════════════════════════════════════════════

    def _analyze_mean_reversion(
        self, candles, close, high, low, open_,
        current_close, current_high, current_low, current_open,
        atr_val, adx_val, atr_pct, symbol, bar_idx, pressure,
    ) -> Decision:
        """
        Mean-Reversion Mode for BTC (low ADX / ranging).
        BUY: close < BB Lower + RSI < 30
        SELL: close > BB Upper + RSI > 70
        TP: BB Middle
        SL: ATR × 1.5
        """
        # Bollinger Bands
        bb_mid = close.rolling(self.p["bb_len"]).mean()
        bb_std_s = close.rolling(self.p["bb_len"]).std()
        bb_upper = bb_mid + (self.p["bb_std"] * bb_std_s)
        bb_lower = bb_mid - (self.p["bb_std"] * bb_std_s)

        bb_mid_val = float(bb_mid.iloc[-1])
        bb_upper_val = float(bb_upper.iloc[-1])
        bb_lower_val = float(bb_lower.iloc[-1])

        if any(pd.isna(v) for v in [bb_mid_val, bb_upper_val, bb_lower_val]):
            return self.create_hold(symbol=symbol, reason="BB NaN (MR mode)")

        # RSI
        rsi_val = self._compute_rsi(close)

        # RSI extreme block
        if rsi_val > self.p["rsi_extreme_high"] or rsi_val < self.p["rsi_extreme_low"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"MR: RSI extreme {rsi_val:.1f} (wait)",
            )

        # Volume
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        vol = candles[vol_col].astype(float) if vol_col in candles.columns else None
        vol_current = float(vol.iloc[-1]) if vol is not None else 0
        vol_avg = float(vol.rolling(self.p["vol_ma"]).mean().iloc[-1]) if vol is not None else 0
        vol_spike = vol_avg > 0 and vol_current > vol_avg * self.p["vol_spike_ratio"]

        # ─── Signal Detection ───
        action = Action.HOLD
        confidence = 0.0
        reasons = []
        sl_price = None
        tp_price = None

        # BUY: close < BB Lower AND RSI oversold
        if current_close < bb_lower_val and rsi_val < self.p["rsi_mr_buy"]:
            action = Action.BUY
            confidence = 60.0
            reasons.append(f"MR:BUY close<BB_L({current_close:.0f}<{bb_lower_val:.0f})")
            reasons.append(f"RSI={rsi_val:.1f}")

            # Confidence boosts
            if rsi_val < 20:
                confidence += 15
                reasons.append(f"RSI deep({rsi_val:.0f})")
            if vol_spike:
                confidence += 10
                reasons.append("Vol spike")
            if pressure and pressure.get("buying_pressure", 0) > 0.6:
                confidence += 10
                reasons.append(f"Buy pressure={pressure['buying_pressure']:.0%}")

        # SELL: close > BB Upper AND RSI overbought
        elif current_close > bb_upper_val and rsi_val > self.p["rsi_mr_sell"]:
            action = Action.SELL
            confidence = 60.0
            reasons.append(f"MR:SELL close>BB_U({current_close:.0f}>{bb_upper_val:.0f})")
            reasons.append(f"RSI={rsi_val:.1f}")

            if rsi_val > 80:
                confidence += 15
                reasons.append(f"RSI deep({rsi_val:.0f})")
            if vol_spike:
                confidence += 10
                reasons.append("Vol spike")
            if pressure and pressure.get("selling_pressure", 0) > 0.6:
                confidence += 10
                reasons.append(f"Sell pressure={pressure['selling_pressure']:.0%}")

        else:
            return self.create_hold(
                symbol=symbol,
                reason=f"MR: no extreme — close={current_close:.0f} BB=[{bb_lower_val:.0f},{bb_upper_val:.0f}] RSI={rsi_val:.1f} ADX={adx_val:.1f}",
            )

        # Min confidence
        if confidence < 55:
            return self.create_hold(
                symbol=symbol,
                reason=f"MR: confidence {confidence:.0f} < 55",
            )

        # SL via Smart SL Calculator
        from app.risk.sl_calculator import calculate_smart_sl

        min_sl_abs = current_close * self.p["min_sl_pct"]
        direction_mr = "BUY" if action == Action.BUY else "SELL"

        sl_price = calculate_smart_sl(
            close=current_close, atr=atr_val, direction=direction_mr, candles=candles,
            sl_atr_mult=self.p["sl_atr_mult_mr"],
            sl_buffer_atr=self.p.get("sl_buffer_atr", 0.3),
            swing_lookback=self.p["swing_lookback"],
            min_sl_distance=min_sl_abs,
            adx=adx_val, h1_atr=None,
            symbol=symbol,
            enable_regime_adaptive=False,  # MR mode: fixed SL mult
        )
        tp_price = bb_mid_val

        # RR
        rr = 0.0
        if sl_price and tp_price:
            sl_d = abs(current_close - sl_price)
            tp_d = abs(tp_price - current_close)
            rr = tp_d / sl_d if sl_d > 0 else 0

        confidence_norm = min(confidence / 100.0, 0.95)

        # Mark cooldown
        self._last_signal_bar = bar_idx

        reason_str = f"🔄 MR | " + " | ".join(reasons[:5])

        logger.debug("btc_elite_mr_signal", extra={
            "symbol": symbol, "action": action.value,
            "mode": "MEAN_REV", "confidence": round(confidence_norm, 2),
            "adx": round(adx_val, 1), "rsi": round(rsi_val, 1),
            "bb_upper": round(bb_upper_val, 0), "bb_lower": round(bb_lower_val, 0),
            "atr_pct": round(atr_pct, 4), "rr": round(rr, 2),
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
            tags=["btc_elite", "mean_rev", f"conf_{confidence:.0f}"],
            debug={
                "mode": "MEAN_REV",
                "adx": round(adx_val, 1), "rsi": round(rsi_val, 1),
                "bb_upper": round(bb_upper_val, 0),
                "bb_lower": round(bb_lower_val, 0),
                "bb_mid": round(bb_mid_val, 0),
                "atr": round(atr_val, 2),
                "atr_pct": round(atr_pct, 4),
                "vol_spike": vol_spike,
                "rr": round(rr, 2),
            },
        )

    # ═════════════════════════════════════════════
    # INDICATOR HELPERS
    # ═════════════════════════════════════════════

    def _compute_adx(self, high, low, close):
        """Compute ADX, DI+, DI-."""
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

        di_plus_s = 100 * plus_dm_s / (atr_s + 1e-10)
        di_minus_s = 100 * minus_dm_s / (atr_s + 1e-10)
        dx = 100 * (di_plus_s - di_minus_s).abs() / (di_plus_s + di_minus_s + 1e-10)
        adx_s = dx.rolling(period).mean()

        adx = float(adx_s.iloc[-1]) if not pd.isna(adx_s.iloc[-1]) else 0
        di_p = float(di_plus_s.iloc[-1]) if not pd.isna(di_plus_s.iloc[-1]) else 0
        di_m = float(di_minus_s.iloc[-1]) if not pd.isna(di_minus_s.iloc[-1]) else 0
        return adx, di_p, di_m

    def _compute_rsi(self, close):
        """Compute RSI (fast period for crypto)."""
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(self.p["rsi_period"]).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(self.p["rsi_period"]).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

    def _compute_macd(self, close):
        """Compute MACD histogram."""
        fast = close.ewm(span=self.p["macd_fast"], adjust=False).mean()
        slow = close.ewm(span=self.p["macd_slow"], adjust=False).mean()
        macd_line = fast - slow
        signal_line = macd_line.ewm(span=self.p["macd_signal"], adjust=False).mean()
        hist = macd_line - signal_line
        return float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0

    # ═════════════════════════════════════════════
    # 5-LAYER SCORING
    # ═════════════════════════════════════════════

    def _score_trend(self, ema_f, ema_m, ema_s, ema_t, close):
        """Layer 1: Trend Alignment — 25 pts max."""
        b, s = 0, 0
        br, sr = [], []

        # Full bull stack: EMA 9>21>50>200 (+15)
        if ema_f > ema_m > ema_s > ema_t:
            b += 15
            br.append("L1:Full bull stack")
        elif ema_f > ema_m and close > ema_t:
            b += 8
            br.append("L1:Partial bull")

        # Full bear stack (+15)
        if ema_f < ema_m < ema_s < ema_t:
            s += 15
            sr.append("L1:Full bear stack")
        elif ema_f < ema_m and close < ema_t:
            s += 8
            sr.append("L1:Partial bear")

        # Close vs EMA50 (+5)
        if close > ema_s:
            b += 5
            br.append("L1:>EMA50")
        if close < ema_s:
            s += 5
            sr.append("L1:<EMA50")

        # EMA9 vs EMA21 momentum (+5)
        if ema_f > ema_m:
            b += 5
            br.append("L1:EMA9>21")
        if ema_f < ema_m:
            s += 5
            sr.append("L1:EMA9<21")

        return min(b, 25), min(s, 25), br, sr

    def _score_momentum(self, adx, di_plus, di_minus, rsi, macd_hist):
        """Layer 2: Momentum — 25 pts max."""
        b, s = 0, 0
        br, sr = [], []

        # ADX strength
        if adx > self.p["adx_strong"]:
            b += 10
            s += 10
            br.append(f"L2:ADX {adx:.0f} strong")
            sr.append(f"L2:ADX {adx:.0f} strong")
        elif adx > self.p["adx_min"]:
            b += 5
            s += 5
            br.append(f"L2:ADX {adx:.0f}>min")
            sr.append(f"L2:ADX {adx:.0f}>min")

        # DI crossover (+5)
        if di_plus > di_minus:
            b += 5
            br.append("L2:DI+>DI-")
        if di_minus > di_plus:
            s += 5
            sr.append("L2:DI->DI+")

        # RSI zones (+5)
        if 40 < rsi < 70:
            b += 5
            br.append(f"L2:RSI {rsi:.0f} bull zone")
        if 30 < rsi < 60:
            s += 5
            sr.append(f"L2:RSI {rsi:.0f} bear zone")

        # MACD histogram (+5)
        if macd_hist > 0:
            b += 5
            br.append("L2:MACD+")
        if macd_hist < 0:
            s += 5
            sr.append("L2:MACD-")

        return min(b, 25), min(s, 25), br, sr

    def _score_btc_volatility(self, atr_pct, bb_squeezed, squeeze_released, bb_width):
        """
        Layer 3: BTC Volatility — 20 pts max.

        BTC-specific: uses ATR% (not absolute ATR) and BB squeeze for
        detecting volatility expansion (ideal for momentum entries).
        """
        b, s = 0, 0
        br, sr = [], []

        # ATR% in sweet spot (0.5%-2% = ideal for scalping) → +8 both sides
        if 0.005 <= atr_pct <= 0.02:
            b += 8
            s += 8
            br.append(f"L3:ATR% {atr_pct:.2%} sweet spot")
            sr.append(f"L3:ATR% {atr_pct:.2%} sweet spot")
        elif 0.002 < atr_pct < 0.005:
            b += 4
            s += 4
            br.append(f"L3:ATR% {atr_pct:.2%} low-moderate")
            sr.append(f"L3:ATR% {atr_pct:.2%} low-moderate")

        # BB Squeeze released = volatility expansion (+7 both sides)
        if squeeze_released:
            b += 7
            s += 7
            br.append("L3:BB squeeze released!")
            sr.append("L3:BB squeeze released!")
        elif bb_squeezed:
            b += 3
            s += 3
            br.append("L3:BB squeezed (pending)")
            sr.append("L3:BB squeezed (pending)")

        # BB width expanding (+5)
        # We can approximate expansion by checking current width > recent average
        if bb_width > 0:
            b += 5
            s += 5
            br.append("L3:BB width present")
            sr.append("L3:BB width present")

        return min(b, 20), min(s, 20), br, sr

    def _score_volume_pressure(self, vol_current, vol_avg, vol_spike, close, vwap_val, pressure):
        """
        Layer 4: Volume + Pressure — 15 pts max.

        Uses tick volume spike + buy/sell pressure from TickVolumeAnalyzer
        for directional conviction.
        """
        b, s = 0, 0
        br, sr = [], []

        # Volume spike (+5)
        if vol_spike:
            b += 5
            s += 5
            br.append("L4:Vol spike")
            sr.append("L4:Vol spike")

        # VWAP positioning (+5)
        if vwap_val is not None:
            if close > vwap_val:
                b += 5
                br.append("L4:>VWAP")
            if close < vwap_val:
                s += 5
                sr.append("L4:<VWAP")

        # Buy/Sell pressure from TickVolumeAnalyzer (+5)
        if pressure:
            buy_p = pressure.get("buying_pressure", 0.5)
            sell_p = pressure.get("selling_pressure", 0.5)
            if buy_p > 0.6:
                b += 5
                br.append(f"L4:BuyP={buy_p:.0%}")
            if sell_p > 0.6:
                s += 5
                sr.append(f"L4:SellP={sell_p:.0%}")

        return min(b, 15), min(s, 15), br, sr

    def _score_risk_quality(self, close, swing_high, swing_low, atr):
        """
        Layer 5: Risk Quality — 15 pts max.

        Evaluates how clean the risk/reward setup is using
        swing structure and ATR distance.
        """
        b, s = 0, 0
        br, sr = [], []

        # Good swing structure for SL placement
        sl_dist_buy = close - swing_low
        sl_dist_sell = swing_high - close

        # BUY: swing low is 0.5-2.0 ATR away = clean structure (+8)
        if 0.5 * atr <= sl_dist_buy <= 2.5 * atr:
            b += 8
            br.append("L5:Clean swing SL (buy)")
        elif sl_dist_buy > 0:
            b += 3
            br.append("L5:SL structure present")

        # SELL: swing high is 0.5-2.0 ATR away (+8)
        if 0.5 * atr <= sl_dist_sell <= 2.5 * atr:
            s += 8
            sr.append("L5:Clean swing SL (sell)")
        elif sl_dist_sell > 0:
            s += 3
            sr.append("L5:SL structure present")

        # RR potential — enough room for 1.5R+ (+7)
        if sl_dist_buy > 0 and sl_dist_buy <= 2.0 * atr:
            b += 7
            br.append("L5:Good RR potential")
        if sl_dist_sell > 0 and sl_dist_sell <= 2.0 * atr:
            s += 7
            sr.append("L5:Good RR potential")

        return min(b, 15), min(s, 15), br, sr
