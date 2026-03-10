"""
Gold Elite Strategy — 5-Layer 100-Point Scoring for Maximum Profit.

Philosophy:
    - Combines confluence from gold_precision + scoring from gold_break_checklist
    - Adaptive RR: wider TP for higher quality setups = bigger profit per trade
    - High RR (2.0-3.0) to let profits run
    - Session weighting: London/NY overlap gets bonus
    - ADX Gate: only trade markets with clear trend

5-Layer Scoring (100 points):
    1. Trend Alignment (EMA 9/21/50/200)     — 25 pts
    2. Momentum (ADX + RSI + MACD hist)      — 25 pts
    3. Price Action (Swing break + candle)    — 20 pts
    4. Volume + Money Flow (VWAP + vol spike) — 15 pts
    5. Risk Quality (ATR RR + structure SL)   — 15 pts

Decision:
    >= 85 -> Trade RR=3.0 (max profit)
    >= 75 -> Trade RR=2.5 (strong)
    >= 65 -> Trade RR=2.0 (standard)
    <  65 -> HOLD

Target: Profit Factor >1.5, Maximum total P&L
"""

import pandas as pd
import numpy as np

from app.strategy.base import BaseStrategy
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.analysis.indicators import heiken_ashi

logger = get_logger(__name__)

# ═════════════════════════════════════════════
# DEFAULT PARAMETERS — fallback when DB has no entry
# All values can be overridden via strategy_params table in SQLite
# ═════════════════════════════════════════════

GOLD_ELITE_DEFAULTS = {
    # EMA
    "ema_fast": 9,
    "ema_mid": 21,
    "ema_slow": 50,
    "ema_trend": 200,

    # ADX
    "adx_period": 14,
    "adx_min": 20,
    "adx_strong": 30,

    # RSI
    "rsi_period": 14,
    "rsi_ob": 75,
    "rsi_os": 25,

    # MACD
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,

    # Volume
    "vol_ma": 20,
    "vol_spike_ratio": 1.2,

    # Structure
    "swing_lookback": 15,

    # Risk
    "atr_period": 14,
    "sl_atr_mult": 1.5,        # แคบลงจาก 2.0 → loss เล็กลง
    "sl_buffer_atr": 0.3,       # buffer แคบลง
    "min_score_trade": 70,      # เข้มงวดขึ้นจาก 65 → ลดเทรดเสี่ยง
    "rr_conservative": 1.5,     # TP ใกล้ขึ้นจาก 2.0 → ถึง TP บ่อยกว่า
    "rr_standard": 1.8,         # จาก 2.5
    "rr_aggressive": 2.0,       # จาก 3.0

    # Sideways Mode (Mean-Reversion)
    "bb_len": 20,
    "bb_std": 2.0,
    "rsi_side_period": 14,
    "rsi_side_buy": 35,
    "rsi_side_sell": 65,
    "sl_atr_mult_side": 1.5,
    "rr_sideways": 1.0,

    # Session (UTC hours)
    "london_start": 7,
    "london_end": 16,
    "ny_start": 12,
    "ny_end": 21,
    "overlap_start": 12,
    "overlap_end": 16,

    # Bidirectional / Counter-Trend
    "counter_trend_enabled": False,
    "trend_bonus": 10,              # bonus pts for trend-aligned trades
    "min_score_counter": 75,        # higher bar for counter-trend
    "rr_counter": 1.5,              # tighter TP for counter-trend
    "counter_confidence_penalty": 0.10,  # confidence reduction
}


class GoldEliteStrategy(BaseStrategy):
    """
    Gold Elite — 5-Layer 100 Point Scoring for Maximum Profit.

    Parameters loaded from DB (strategy_params table) at init.
    Falls back to GOLD_ELITE_DEFAULTS if no DB entry.

    Modes:
        - Trend Mode (ADX >= adx_min): 5-layer scoring system
        - Sideways Mode (ADX < adx_min): BB + RSI mean-reversion

    Scoring Layers (Trend Mode):
        1. Trend Alignment (EMA stack) — 20 pts
        2. Momentum (ADX + DI crossover) — 25 pts
        3. Price Action (candle + MACD) — 20 pts
        4. Volume Confirmation — 15 pts
        5. Risk Quality (SL structure) — 20 pts
    """

    name = "gold_elite"
    timeframe = "M15"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.RANGING,
        RegimeType.LOW_VOLATILITY,
    ]

    def __init__(self) -> None:
        """Load params from DB, fallback to GOLD_ELITE_DEFAULTS."""
        from app.strategy.param_loader import get_param_loader
        loader = get_param_loader()
        if loader:
            self.p = loader.get_params(self.name, "XAUUSDc", GOLD_ELITE_DEFAULTS)
        else:
            self.p = {**GOLD_ELITE_DEFAULTS}

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "XAUUSDc"

        # ─── Data check ───
        min_bars = self.p["ema_trend"] + 20
        if candles is None or len(candles) < min_bars:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {min_bars})",
            )

        # ─── Session filter ───
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
            in_london = self.p["london_start"] <= hour < self.p["london_end"]
            in_ny = self.p["ny_start"] <= hour < self.p["ny_end"]
            in_overlap = self.p["overlap_start"] <= hour < self.p["overlap_end"]

            if not (in_london or in_ny):
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Session filter: hour={hour} outside London/NY",
                )

            # Overlap bonus (best Gold session)
            if in_overlap:
                session_bonus = 5

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

        # ─── Compute indicators ───
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

        # ATR
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(self.p["atr_period"]).mean()
        atr_val = float(atr_series.iloc[-1])
        if pd.isna(atr_val) or atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR NaN")

        # ADX + DI
        adx_val, di_plus, di_minus = self._compute_adx(high, low, close)

        # ═════════════════════════════════════════
        # MODE SWITCH: Trend (ADX>=20) vs Sideways (ADX<20 or RANGING regime)
        # ═════════════════════════════════════════
        is_sideways = (adx_val < self.p["adx_min"]) or regime in (RegimeType.RANGING, RegimeType.LOW_VOLATILITY)

        if is_sideways:
            return self._analyze_sideways(
                candles, close, high, low, open_,
                current_close, current_high, current_low, current_open,
                atr_val, adx_val, symbol,
            )

        # RSI
        rsi_val = self._compute_rsi(close)

        # ─── RSI EXTREME BLOCK — Don't enter at extremes ───
        if rsi_val > 80 or rsi_val < 20:
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

        # Swing High/Low
        swing_data = candles.iloc[-(self.p["swing_lookback"] + 1):-1]
        swing_high = float(swing_data["high"].max())
        swing_low = float(swing_data["low"].min())

        # ═════════════════════════════════════════
        # กรองเทรนด์หลายทามเฟรม (H4 / H1 / M15)
        # ═════════════════════════════════════════

        # M15 EMA trend (จากแท่งหลัก M15)
        trend_bullish = ema_s_val > ema_t_val
        trend_bearish = ema_s_val < ema_t_val

        # MTF trend filter: ใช้ H4/H1/M15 โหวตเสียงข้างมาก = ทิศทางแม่นยำกว่า
        mtf_result = self._compute_mtf_trend(
            kwargs.get("h4_candles"),
            kwargs.get("h1_candles"),
            trend_bullish, trend_bearish,
        )

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

        # ── Layer 3: Price Action (20 pts) ──
        b3, s3, br3, sr3 = self._score_price_action(
            current_close, current_high, current_low, current_open,
            prev_close, prev_high, prev_low, prev_open,
            swing_high, swing_low, atr_val
        )
        buy_score += b3
        sell_score += s3
        buy_reasons.extend(br3)
        sell_reasons.extend(sr3)

        # ── Layer 4: Volume + Money Flow (15 pts) ──
        b4, s4, br4, sr4 = self._score_volume(
            vol_current, vol_avg, current_close, vwap_val
        )
        buy_score += b4
        sell_score += s4
        buy_reasons.extend(br4)
        sell_reasons.extend(sr4)

        # ── Layer 4.5: Tick Volume Pressure (Optional Boost) ──
        pressure = kwargs.get("pressure", {})
        if pressure:
            b_p, s_p, br_p, sr_p = self._score_pressure(pressure)
            buy_score += b_p
            sell_score += s_p
            buy_reasons.extend(br_p)
            sell_reasons.extend(sr_p)

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
        # SMART BIDIRECTIONAL DECISION + MTF TREND FILTER
        # ═════════════════════════════════════════

        # HA Filter
        ha_df = heiken_ashi(open_, high, low, close)
        ha_open_val = float(ha_df['HA_Open'].iloc[-1])
        ha_close_val = float(ha_df['HA_Close'].iloc[-1])
        ha_bullish = ha_close_val > ha_open_val
        ha_bearish = ha_close_val < ha_open_val

        # โบนัสจาก MTF: ทุก TF ที่สอดคล้องได้โบนัส (H4=5, H1=3, M15=2)
        mtf_buy_bonus = mtf_result["buy_bonus"]
        mtf_sell_bonus = mtf_result["sell_bonus"]
        buy_score += mtf_buy_bonus
        sell_score += mtf_sell_bonus
        if mtf_result["buy_reasons"]:
            buy_reasons.extend(mtf_result["buy_reasons"])
        if mtf_result["sell_reasons"]:
            sell_reasons.extend(mtf_result["sell_reasons"])

        # กำหนดทิศทาง MTF สำหรับ counter-trend detection
        trend_bullish = mtf_result["overall_bullish"]
        trend_bearish = mtf_result["overall_bearish"]

        # Pick the strongest direction
        if buy_score >= sell_score:
            if not ha_bullish:
                return self.create_hold(symbol=symbol, reason="HA Filter: Not Bullish")
            total_score = buy_score
            action = Action.BUY
            reasons = buy_reasons
            is_buy = True
            is_counter_trend = trend_bearish  # BUY against bearish = counter
        else:
            if not ha_bearish:
                return self.create_hold(symbol=symbol, reason="HA Filter: Not Bearish")
            total_score = sell_score
            action = Action.SELL
            reasons = sell_reasons
            is_buy = False
            is_counter_trend = trend_bullish  # SELL against bullish = counter

        # If no clear trend, it's not counter-trend
        if not trend_bullish and not trend_bearish:
            is_counter_trend = False

        # Counter-trend gate: require higher score
        counter_enabled = self.p.get("counter_trend_enabled", True)
        min_score_counter = self.p.get("min_score_counter", 75)

        if is_counter_trend and not counter_enabled:
            return self.create_hold(
                symbol=symbol,
                reason=f"Counter-trend disabled (score={total_score}, side={action.value})",
            )

        if is_counter_trend:
            if total_score < min_score_counter:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Counter-trend score {total_score}/100 < {min_score_counter} min",
                )
        else:
            if total_score < self.p["min_score_trade"]:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Score {total_score}/100 < {self.p['min_score_trade']} min",
                )

        # ─── Adaptive RR — counter-trend uses tighter TP ───
        if is_counter_trend:
            rr = self.p.get("rr_counter", 1.5)
            tier = "🔄 Counter"
        elif total_score >= 85:
            rr = self.p["rr_aggressive"]
            tier = "🔥 Elite"
        elif total_score >= 75:
            rr = self.p["rr_standard"]
            tier = "✅ Strong"
        else:
            rr = self.p["rr_conservative"]
            tier = "🎯 Standard"

        # ─── Confidence (counter-trend penalty) ───
        confidence = min(0.60 + (total_score - 60) * 0.01, 0.95)
        if is_counter_trend:
            confidence -= self.p.get("counter_confidence_penalty", 0.10)
            confidence = max(confidence, 0.50)

        # ─── SL / TP ───
        if is_buy:
            sl_base = swing_low
            sl_price = sl_base - (atr_val * self.p["sl_buffer_atr"])
            # Also check ATR-based SL — use min() = wider SL = fewer stops
            sl_atr = current_close - (atr_val * self.p["sl_atr_mult"])
            sl_price = min(sl_price, sl_atr)
            sl_dist = current_close - sl_price
            if sl_dist < atr_val * 0.5:  # minimum SL distance
                sl_price = current_close - (atr_val * 1.0)
                sl_dist = current_close - sl_price
            tp_price = current_close + (sl_dist * rr)
        else:
            sl_base = swing_high
            sl_price = sl_base + (atr_val * self.p["sl_buffer_atr"])
            sl_atr = current_close + (atr_val * self.p["sl_atr_mult"])
            sl_price = max(sl_price, sl_atr)  # wider SL
            sl_dist = sl_price - current_close
            if sl_dist < atr_val * 0.5:
                sl_price = current_close + (atr_val * 1.0)
                sl_dist = sl_price - current_close
            tp_price = current_close - (sl_dist * rr)

        ct_tag = " [CT]" if is_counter_trend else ""
        reason_str = f"{tier}{ct_tag} Score={total_score}/100 RR={rr} | " + "; ".join(reasons[:6])

        logger.debug("gold_elite_signal", extra={
            "symbol": symbol, "action": action.value,
            "total_score": total_score, "confidence": round(confidence, 2),
            "rr": rr, "atr": round(atr_val, 2),
            "adx": round(adx_val, 1), "rsi": round(rsi_val, 1),
            "is_counter_trend": is_counter_trend,
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
            tags=["gold_elite", tier.split()[0], f"score_{total_score}"] + (["counter_trend"] if is_counter_trend else []),
            debug={
                "buy_score": buy_score, "sell_score": sell_score,
                "total_score": total_score, "rr": rr,
                "is_counter_trend": is_counter_trend,
                "adx": round(adx_val, 1), "rsi": round(rsi_val, 1),
                "macd_hist": round(macd_hist, 4),
                "swing_high": round(swing_high, 2),
                "swing_low": round(swing_low, 2),
                "atr": round(atr_val, 2),
                "session_bonus": session_bonus,
            },
        )

    # ═════════════════════════════════════════════
    # SIDEWAYS MODE — BB + RSI Mean-Reversion
    # ═════════════════════════════════════════════

    def _analyze_sideways(
        self, candles, close, high, low, open_,
        current_close, current_high, current_low, current_open,
        atr_val, adx_val, symbol,
    ) -> Decision:
        """
        Sideways Mean-Reversion Mode for Gold.
        BUY:  close < BB Lower + RSI < 35
        SELL: close > BB Upper + RSI > 65
        TP:   BB Middle (mean-reversion target)
        SL:   ATR × 1.5
        """
        # Bollinger Bands
        bb_mid = close.rolling(self.p["bb_len"]).mean()
        bb_std = close.rolling(self.p["bb_len"]).std()
        bb_upper = bb_mid + (self.p["bb_std"] * bb_std)
        bb_lower = bb_mid - (self.p["bb_std"] * bb_std)

        bb_mid_val = float(bb_mid.iloc[-1])
        bb_upper_val = float(bb_upper.iloc[-1])
        bb_lower_val = float(bb_lower.iloc[-1])

        if any(pd.isna(v) for v in [bb_mid_val, bb_upper_val, bb_lower_val]):
            return self.create_hold(symbol=symbol, reason="BB NaN (sideways mode)")

        # RSI
        rsi_val = self._compute_rsi(close)

        # RSI extreme block — don't trade at absolute extremes
        if rsi_val > 85 or rsi_val < 15:
            return self.create_hold(
                symbol=symbol,
                reason=f"Sideways: RSI extreme {rsi_val:.1f} (wait)",
            )

        # Volume
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        vol = candles[vol_col].astype(float) if vol_col in candles.columns else None
        vol_current = float(vol.iloc[-1]) if vol is not None else 0
        vol_avg = float(vol.rolling(20).mean().iloc[-1]) if vol is not None else 0
        vol_spike = vol_avg > 0 and vol_current > vol_avg * 1.2

        # ─── Signal Detection ───
        action = Action.HOLD
        confidence = 0.0
        reasons = []
        sl_price = None
        tp_price = None

        # HA Filter
        ha_df = heiken_ashi(open_, high, low, close)
        ha_open_val = float(ha_df['HA_Open'].iloc[-1])
        ha_close_val = float(ha_df['HA_Close'].iloc[-1])
        ha_bullish = ha_close_val > ha_open_val
        ha_bearish = ha_close_val < ha_open_val

        # BUY: close < BB Lower + RSI oversold
        if current_close < bb_lower_val and rsi_val < self.p["rsi_side_sell"]:
            if not ha_bullish:
                return self.create_hold(symbol=symbol, reason="Sideways: BUY HA Filter: Not Bullish")
            action = Action.BUY
            confidence = 60.0
            reasons.append(f"MR:BUY close<BB_L({current_close:.2f}<{bb_lower_val:.2f})")
            reasons.append(f"RSI={rsi_val:.1f}")

            sl_price = current_close - (atr_val * self.p["sl_atr_mult_side"])
            tp_price = bb_mid_val  # Mean-reversion target

            # Confidence boosts
            if rsi_val < self.p["rsi_side_buy"]:
                confidence += 15
                reasons.append(f"RSI deep oversold({rsi_val:.0f})")
            if vol_spike:
                confidence += 10
                reasons.append("Vol spike")

        # SELL: close > BB Upper + RSI overbought
        elif current_close > bb_upper_val and rsi_val > self.p["rsi_side_buy"]:
            if not ha_bearish:
                return self.create_hold(symbol=symbol, reason="Sideways: SELL HA Filter: Not Bearish")
            action = Action.SELL
            confidence = 60.0
            reasons.append(f"MR:SELL close>BB_U({current_close:.2f}>{bb_upper_val:.2f})")
            reasons.append(f"RSI={rsi_val:.1f}")

            sl_price = current_close + (atr_val * self.p["sl_atr_mult_side"])
            tp_price = bb_mid_val

            if rsi_val > self.p["rsi_side_sell"]:
                confidence += 15
                reasons.append(f"RSI deep overbought({rsi_val:.0f})")
            if vol_spike:
                confidence += 10
                reasons.append("Vol spike")

        else:
            return self.create_hold(
                symbol=symbol,
                reason=f"Sideways: no extreme — close={current_close:.2f} BB=[{bb_lower_val:.2f},{bb_upper_val:.2f}] RSI={rsi_val:.1f} ADX={adx_val:.1f}",
            )

        if action == Action.HOLD:
            return self.create_hold(symbol=symbol, reason="Sideways: no setup")

        # Min confidence
        if confidence < 55:
            return self.create_hold(
                symbol=symbol,
                reason=f"Sideways: confidence {confidence:.0f} < 55",
            )

        # SL distance check
        if sl_price is not None:
            sl_dist = abs(current_close - sl_price)
            if sl_dist < atr_val * 0.3:
                if action == Action.BUY:
                    sl_price = current_close - (atr_val * 0.8)
                else:
                    sl_price = current_close + (atr_val * 0.8)
                sl_dist = abs(current_close - sl_price)

        # RR
        rr = 0.0
        if sl_price and tp_price:
            sl_d = abs(current_close - sl_price)
            tp_d = abs(tp_price - current_close)
            rr = tp_d / sl_d if sl_d > 0 else 0

        confidence_norm = min(confidence / 100.0, 0.95)
        reason_str = f"🔄 Sideways MR | " + " | ".join(reasons[:5])

        logger.debug("gold_elite_sideways_signal", extra={
            "symbol": symbol, "action": action.value,
            "mode": "SIDEWAYS", "confidence": round(confidence_norm, 2),
            "adx": round(adx_val, 1), "rsi": round(rsi_val, 1),
            "bb_upper": round(bb_upper_val, 2), "bb_lower": round(bb_lower_val, 2),
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
            tags=["gold_elite", "sideways_mr", f"conf_{confidence:.0f}"],
            debug={
                "mode": "SIDEWAYS",
                "adx": round(adx_val, 1), "rsi": round(rsi_val, 1),
                "bb_upper": round(bb_upper_val, 2),
                "bb_lower": round(bb_lower_val, 2),
                "bb_mid": round(bb_mid_val, 2),
                "atr": round(atr_val, 2),
                "vol_spike": vol_spike,
                "rr": round(rr, 2),
            },
        )

    # ═════════════════════════════════════════════
    # INDICATOR HELPERS
    # ═════════════════════════════════════════════

    def _compute_mtf_trend(self, h4_candles, h1_candles, m15_bullish, m15_bearish):
        """
        กรองเทรนด์หลายทามเฟรม (H4 / H1 / M15).

        ใช้ EMA 50 vs EMA 200 ในแต่ละ TF:
            - H4: น้ำหนักสูงสุด (+5 โบนัส)
            - H1: น้ำหนักกลาง (+3 โบนัส)
            - M15: น้ำหนักต่ำ (+2 โบนัส) — จากข้อมูลหลักที่มีอยู่แล้ว

        ผลลัพธ์:
            overall_bullish/bearish = โหวตเสียงข้างมาก (≥2 จาก 3 TF)
            buy_bonus/sell_bonus = คะแนนโบนัสตามจำนวน TF ที่สอดคล้อง
        """
        buy_bonus = 0
        sell_bonus = 0
        buy_reasons = []
        sell_reasons = []
        bull_votes = 0
        bear_votes = 0

        # ── H4 (น้ำหนักสูงสุด: +5) ──
        if h4_candles is not None and len(h4_candles) >= 220:
            h4c = h4_candles["close"].astype(float)
            h4_ema50 = float(h4c.ewm(span=50, adjust=False).mean().iloc[-1])
            h4_ema200 = float(h4c.ewm(span=200, adjust=False).mean().iloc[-1])
            if not (pd.isna(h4_ema50) or pd.isna(h4_ema200)):
                if h4_ema50 > h4_ema200:
                    bull_votes += 1
                    buy_bonus += 5
                    buy_reasons.append("MTF:H4↑+5")
                elif h4_ema50 < h4_ema200:
                    bear_votes += 1
                    sell_bonus += 5
                    sell_reasons.append("MTF:H4↓+5")
        elif h4_candles is not None and len(h4_candles) >= 60:
            # H4 น้อยกว่า 220 แท่ง → ใช้ EMA 20 vs 50 แทน
            h4c = h4_candles["close"].astype(float)
            h4_ema20 = float(h4c.ewm(span=20, adjust=False).mean().iloc[-1])
            h4_ema50 = float(h4c.ewm(span=50, adjust=False).mean().iloc[-1])
            if not (pd.isna(h4_ema20) or pd.isna(h4_ema50)):
                if h4_ema20 > h4_ema50:
                    bull_votes += 1
                    buy_bonus += 4
                    buy_reasons.append("MTF:H4↑+4")
                elif h4_ema20 < h4_ema50:
                    bear_votes += 1
                    sell_bonus += 4
                    sell_reasons.append("MTF:H4↓+4")

        # ── H1 (น้ำหนักกลาง: +3) ──
        if h1_candles is not None and len(h1_candles) >= 220:
            h1c = h1_candles["close"].astype(float)
            h1_ema50 = float(h1c.ewm(span=50, adjust=False).mean().iloc[-1])
            h1_ema200 = float(h1c.ewm(span=200, adjust=False).mean().iloc[-1])
            if not (pd.isna(h1_ema50) or pd.isna(h1_ema200)):
                if h1_ema50 > h1_ema200:
                    bull_votes += 1
                    buy_bonus += 3
                    buy_reasons.append("MTF:H1↑+3")
                elif h1_ema50 < h1_ema200:
                    bear_votes += 1
                    sell_bonus += 3
                    sell_reasons.append("MTF:H1↓+3")

        # ── M15 (น้ำหนักต่ำ: +2) — จาก EMA ที่คำนวณไว้แล้ว ──
        if m15_bullish:
            bull_votes += 1
            buy_bonus += 2
            buy_reasons.append("MTF:M15↑+2")
        elif m15_bearish:
            bear_votes += 1
            sell_bonus += 2
            sell_reasons.append("MTF:M15↓+2")

        # ── โหวตเสียงข้างมาก: ≥2 จาก 3 = ทิศทาง MTF ──
        overall_bullish = bull_votes >= 2
        overall_bearish = bear_votes >= 2

        return {
            "overall_bullish": overall_bullish,
            "overall_bearish": overall_bearish,
            "bull_votes": bull_votes,
            "bear_votes": bear_votes,
            "buy_bonus": buy_bonus,
            "sell_bonus": sell_bonus,
            "buy_reasons": buy_reasons,
            "sell_reasons": sell_reasons,
        }

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

        di_plus_s = 100 * plus_dm_s / atr_s
        di_minus_s = 100 * minus_dm_s / atr_s
        dx = 100 * (di_plus_s - di_minus_s).abs() / (di_plus_s + di_minus_s + 1e-10)
        adx_s = dx.rolling(period).mean()

        adx = float(adx_s.iloc[-1]) if not pd.isna(adx_s.iloc[-1]) else 0
        di_p = float(di_plus_s.iloc[-1]) if not pd.isna(di_plus_s.iloc[-1]) else 0
        di_m = float(di_minus_s.iloc[-1]) if not pd.isna(di_minus_s.iloc[-1]) else 0
        return adx, di_p, di_m

    def _compute_rsi(self, close):
        """Compute RSI."""
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
        # Partial bull: EMA 9>21 + close > EMA 200 (+8)
        elif ema_f > ema_m and close > ema_t:
            b += 8
            br.append("L1:Partial bull")

        # Full bear stack: EMA 9<21<50<200 (+15)
        if ema_f < ema_m < ema_s < ema_t:
            s += 15
            sr.append("L1:Full bear stack")
        elif ema_f < ema_m and close < ema_t:
            s += 8
            sr.append("L1:Partial bear")

        # Close > EMA 50 (+5) — medium-term trend
        if close > ema_s:
            b += 5
            br.append("L1:>EMA50")
        if close < ema_s:
            s += 5
            sr.append("L1:<EMA50")

        # EMA 9 slope rising (+5)
        # Approximate slope from EMA diff
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

        # ADX > 20 (+5), ADX > 30 (+10)
        if adx > self.p["adx_strong"]:
            b += 10
            s += 10
            br.append(f"L2:ADX {adx:.0f}>{self.p['adx_strong']} strong")
            sr.append(f"L2:ADX {adx:.0f}>{self.p['adx_strong']} strong")
        elif adx > self.p["adx_min"]:
            b += 5
            s += 5
            br.append(f"L2:ADX {adx:.0f}>{self.p['adx_min']}")
            sr.append(f"L2:ADX {adx:.0f}>20")

        # DI+ > DI- (buy) / DI- > DI+ (sell) (+5)
        if di_plus > di_minus:
            b += 5
            br.append("L2:DI+>DI-")
        if di_minus > di_plus:
            s += 5
            sr.append("L2:DI->DI+")

        # RSI in bullish zone 40-70 (+5)
        if 40 < rsi < 70:
            b += 5
            br.append(f"L2:RSI {rsi:.0f} bull zone")
        if 30 < rsi < 60:
            s += 5
            sr.append(f"L2:RSI {rsi:.0f} bear zone")

        # MACD histogram positive/negative (+5)
        if macd_hist > 0:
            b += 5
            br.append("L2:MACD+")
        if macd_hist < 0:
            s += 5
            sr.append("L2:MACD-")

        return min(b, 25), min(s, 25), br, sr

    def _score_price_action(
        self, close, high, low, open_,
        prev_close, prev_high, prev_low, prev_open,
        swing_high, swing_low, atr
    ):
        """Layer 3: Price Action — 20 pts max."""
        b, s = 0, 0
        br, sr = [], []

        body = abs(close - open_)
        total_range = high - low
        body_ratio = body / total_range if total_range > 0 else 0

        # Swing break (+10)
        if close > swing_high:
            b += 10
            br.append("L3:Break swing high")
        if close < swing_low:
            s += 10
            sr.append("L3:Break swing low")

        # Near swing (within 0.5 ATR but not broken) — bullish pullback (+5)
        if abs(close - swing_low) < atr * 0.5 and close > swing_low:
            b += 5
            br.append("L3:Pullback to swing low")
        if abs(close - swing_high) < atr * 0.5 and close < swing_high:
            s += 5
            sr.append("L3:Pullback to swing high")

        # Bullish engulfing / momentum candle (+5)
        bullish_engulfing = (
            close > open_ and prev_close < prev_open and
            close > prev_open and open_ < prev_close
        )
        bullish_momentum = (
            close > open_ and body_ratio > 0.5 and close > prev_high
        )
        if bullish_engulfing or bullish_momentum:
            b += 5
            br.append("L3:Bull candle")

        # Bearish engulfing / momentum candle (+5)
        bearish_engulfing = (
            close < open_ and prev_close > prev_open and
            close < prev_open and open_ > prev_close
        )
        bearish_momentum = (
            close < open_ and body_ratio > 0.5 and close < prev_low
        )
        if bearish_engulfing or bearish_momentum:
            s += 5
            sr.append("L3:Bear candle")

        # Pin bar bonus (+5)
        lower_wick = min(open_, close) - low
        upper_wick = high - max(open_, close)
        if body > 0:
            if lower_wick > body * 2 and close > open_:
                b += 5
                br.append("L3:Bull pin bar")
            if upper_wick > body * 2 and close < open_:
                s += 5
                sr.append("L3:Bear pin bar")

        return min(b, 20), min(s, 20), br, sr

    def _score_volume(self, vol_current, vol_avg, close, vwap_val):
        """Layer 4: Volume + Money Flow — 15 pts max."""
        b, s = 0, 0
        br, sr = [], []

        # Volume spike > 1.2x avg (+5)
        if vol_avg > 0 and vol_current > vol_avg * self.p["vol_spike_ratio"]:
            ratio = vol_current / vol_avg
            b += 5
            s += 5
            br.append(f"L4:Vol {ratio:.1f}x")
            sr.append(f"L4:Vol {ratio:.1f}x")

        # Volume > avg (+3)
        elif vol_avg > 0 and vol_current > vol_avg:
            b += 3
            s += 3
            br.append("L4:Vol>avg")
            sr.append("L4:Vol>avg")

        # VWAP bias (+5)
        if vwap_val is not None and not pd.isna(vwap_val):
            if close > vwap_val:
                b += 5
                br.append("L4:>VWAP")
            if close < vwap_val:
                s += 5
                sr.append("L4:<VWAP")

        # High volume + momentum alignment (+5)
        if vol_avg > 0 and vol_current > vol_avg * 1.5:
            b += 5
            s += 5
            br.append("L4:Vol spike!")
            sr.append("L4:Vol spike!")

        return min(b, 15), min(s, 15), br, sr

    def _score_pressure(self, pressure: dict):
        """
        Layer 4.5: Tick Volume Pressure (Buying/Selling Pressure).
        Input from TickVolumeAnalyzer via kwargs['pressure'].
        
        Max 15 pts extra (Boost).
        """
        b, s = 0, 0
        br, sr = [], []
        
        if not pressure:
            return 0, 0, [], []

        buying_pressure = pressure.get("buying_pressure", 0.5)
        selling_pressure = pressure.get("selling_pressure", 0.5)
        score = pressure.get("score", 0)
        is_climax = pressure.get("is_climax", False)
        
        # High Buying Pressure (> 0.6)
        if buying_pressure > 0.6:
            b += 10
            br.append(f"L4.5:BuyPress {buying_pressure:.2f}")
        
        # High Selling Pressure (> 0.6)
        if selling_pressure > 0.6:
            s += 10
            sr.append(f"L4.5:SellPress {selling_pressure:.2f}")
            
        # Volume Climax (Reversal signal often, but can be breakout)
        if is_climax:
            # If Climax + High Buy Pressure -> Strong Buy (Breakout)
            if buying_pressure > 0.7:
                b += 5
                br.append("L4.5:Climax Buy")
            # If Climax + High Sell Pressure -> Strong Sell (Breakout)
            elif selling_pressure > 0.7:
                s += 5
                sr.append("L4.5:Climax Sell")
                
        return b, s, br, sr

    def _score_risk_quality(self, close, swing_high, swing_low, atr):
        """Layer 5: Risk Quality — 15 pts max."""
        b, s = 0, 0
        br, sr = [], []

        # SL distance check — tight SL = better RR (+5)
        buy_sl_dist = close - swing_low
        sell_sl_dist = swing_high - close

        # Buy: SL within 1-3 ATR = good (+5)
        if 0.5 * atr < buy_sl_dist < 3.0 * atr:
            b += 5
            br.append("L5:Good SL dist buy")
        # Very tight SL within 1.5 ATR (+10)
        if 0.5 * atr < buy_sl_dist < 1.5 * atr:
            b += 5
            br.append("L5:Tight SL buy")

        # Sell: SL within 1-3 ATR = good (+5)
        if 0.5 * atr < sell_sl_dist < 3.0 * atr:
            s += 5
            sr.append("L5:Good SL dist sell")
        if 0.5 * atr < sell_sl_dist < 1.5 * atr:
            s += 5
            sr.append("L5:Tight SL sell")

        # Price not at extreme (within 80% ATR of recent range) (+5)
        mid_range = (swing_high + swing_low) / 2
        range_pct = abs(close - mid_range) / (atr + 1e-10)
        if range_pct < 2.0:
            b += 5
            s += 5
            br.append("L5:Not extreme")
            sr.append("L5:Not extreme")

        return min(b, 15), min(s, 15), br, sr
