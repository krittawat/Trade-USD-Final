"""
Silver Evolution Strategy — WR>70% Fusion for XAGUSDc M5.

"THE ULTIMATE SILVER SNIPERS" — Fuses the best from 6 proven strategies.

Architecture:
    Gate: H1 EMA50/200 trend + Session London/NY + ADX≥15
    L1: M5 Trend Stack (EMA 9/21/50/200)           — 20 pts
    L2: H1 Trend Quality (EMA gap + H1 RSI)        — 15 pts
    L3: Pullback Quality (EMA21 touch + reversal)   — 15 pts
    L4: Momentum (ADX + DI + MACD)                  — 15 pts
    L5: BB + Mean-Reversion Confluence + Squeeze    — 15 pts  ← Silver-specific
    L6: Volume Confirm (RVOL)                       — 10 pts
    L7: Candle Quality (engulfing + pin bar)         — 10 pts
    AI Boost: Deep V4 probability (optional)        — +15 bonus

Decision:
    Trending Mode (ADX ≥ 25):
        ≥ 65 → Trade (standard), ≥ 75 → Trade (elite)
    Ranging Mode  (ADX < 25):
        ≥ 50 → Trade if BB/RSI extreme confirms
    < threshold → HOLD

Key to WR>70%:
    - Tight TP (0.8-1.0× ATR ranging, 1.2× trending)
    - Dual-mode adaptive: trend-follow + mean-reversion
    - BB squeeze breakout detection
    - Session-aware Silver overlap bonus

Source strategies:
    gold_evolution (7-layer scoring)
    silver_elite   (BB dual-mode, mean-reversion)
    silver_mean_rev (BB+RSI mean-reversion tuning)
    gold_silver_wr60 (H1 trend + RSI2 pullback)
    gold_smart_money (SMC concepts backup)
    jtg_zone_fvg   (zone confluence)
"""

import pandas as pd
import numpy as np

from app.strategy.base import BaseStrategy
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

logger = get_logger(__name__)

# ═════════════════════════════════════════════
# DEFAULT PARAMETERS
# ═════════════════════════════════════════════

SILVER_EVOLUTION_DEFAULTS = {
    # EMA (M5)
    "ema_fast": 9,
    "ema_mid": 21,
    "ema_slow": 50,
    "ema_trend": 200,

    # H1 filter
    "h1_ema_fast": 50,
    "h1_ema_slow": 200,
    "h1_rsi_period": 14,

    # ADX (M5) — lower gate for Silver
    "adx_period": 14,
    "adx_min": 18,
    "adx_mode_switch": 25,   # < 25 = Ranging, >= 25 = Trending
    "adx_strong": 35,

    # RSI (M5)
    "rsi_period": 14,

    # MACD (M5)
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,

    # Bollinger Bands — Silver Mean-Reversion
    "bb_len": 15,
    "bb_std": 2.0,
    "bb_squeeze_percentile": 30,

    # Volume
    "vol_ma": 20,
    "vol_spike_ratio": 1.0,

    # Structure
    "swing_lookback": 15,

    # Risk — Tighter TP for Silver WR boost
    "atr_period": 14,
    "sl_atr_mult": 2.2,                # Wider SL to avoid spikes
    "tp_atr_mult_ranging": 1.0,        
    "tp_atr_mult_trending": 1.5,       
    "sl_buffer_atr": 0.3,              
    "min_sl_distance": 0.05,           # Wider minimum SL
    "min_score_ranging": 60,
    "min_score_trending": 60,          
    "min_score_high": 70,

    # Session (UTC hours)
    "session_start": 7,
    "session_end": 21,
    "overlap_start": 12,
    "overlap_end": 16,

    # AI boost
    "ai_boost_enabled": True,
    "ai_threshold_weak": 0.55,
    "ai_threshold_strong": 0.65,

    # Bidirectional / Counter-Trend
    "counter_trend_enabled": False,
    "h1_trend_bonus": 10,              # bonus pts for H1 trend-aligned trades
    "min_score_counter": 65,           # higher bar for counter-trend (was 50/55)
    "tp_atr_mult_counter": 0.7,        # tighter TP for counter-trend
    "counter_confidence_penalty": 0.10, # confidence reduction
}


class SilverEvolutionStrategy(BaseStrategy):
    """
    Silver Evolution — WR>70% Fusion Strategy for XAGUSDc M5.

    Combines: H1 trend filter + 7-layer 100pt scoring + BB mean-reversion
              + AI Deep V4 boost. Dual-mode (Ranging/Trending).
    Tight TP for maximum win rate.
    """

    name = "silver_evolution"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.STRONG_TREND,
        RegimeType.WEAK_TREND,
        RegimeType.BREAKOUT,
        RegimeType.RANGING,           # Silver-specific: we handle ranging via MR mode
        RegimeType.LOW_VOLATILITY,    # Squeeze breakouts possible
    ]

    def __init__(self) -> None:
        """Load params from DB, fallback to SILVER_EVOLUTION_DEFAULTS."""
        try:
            from app.strategy.param_loader import get_param_loader
            loader = get_param_loader()
            if loader:
                self.p = loader.get_params(self.name, "XAGUSDc", SILVER_EVOLUTION_DEFAULTS)
            else:
                self.p = {**SILVER_EVOLUTION_DEFAULTS}
        except Exception:
            self.p = {**SILVER_EVOLUTION_DEFAULTS}
        self._ai_model = None
        self._ai_engine = None
        self._ai_init_attempted = False

    # ═══════════════════════════════════════════════
    # MAIN ANALYZE
    # ═══════════════════════════════════════════════

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "XAGUSDc"
        self._last_candles = candles  # For VolumeAnalyzer

        # ─── Data check ───
        min_bars = self.p["ema_trend"] + 20
        if candles is None or len(candles) < min_bars:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {min_bars})",
            )

        # ─── Session Gate ───
        session_bonus = 0
        if "time" in candles.columns:
            current_time = candles["time"].iloc[-1]
            if hasattr(current_time, "hour"):
                hour = current_time.hour
            else:
                try:
                    hour = pd.Timestamp(current_time).hour
                except Exception:
                    hour = 12
            if not (self.p["session_start"] <= hour < self.p["session_end"]):
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Session gate: hour={hour} outside {self.p['session_start']}-{self.p['session_end']}",
                )
            if self.p["overlap_start"] <= hour < self.p["overlap_end"]:
                session_bonus = 3

        # ─── Extract M5 price arrays ───
        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)

        c = float(close.iloc[-1])
        h = float(high.iloc[-1])
        lo = float(low.iloc[-1])
        o = float(open_.iloc[-1])
        pc = float(close.iloc[-2])
        po = float(open_.iloc[-2])
        ph = float(high.iloc[-2])
        pl = float(low.iloc[-2])

        # ─── Compute M5 indicators ───
        ema_f = close.ewm(span=self.p["ema_fast"], adjust=False).mean()
        ema_m = close.ewm(span=self.p["ema_mid"], adjust=False).mean()
        ema_s = close.ewm(span=self.p["ema_slow"], adjust=False).mean()
        ema_t = close.ewm(span=self.p["ema_trend"], adjust=False).mean()

        ef = float(ema_f.iloc[-1])
        em = float(ema_m.iloc[-1])
        es = float(ema_s.iloc[-1])
        et = float(ema_t.iloc[-1])

        if any(pd.isna(v) for v in [ef, em, es, et]):
            return self.create_hold(symbol=symbol, reason="EMA NaN")

        # ATR
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(self.p["atr_period"]).mean()
        atr = float(atr_series.iloc[-1])
        if pd.isna(atr) or atr <= 0:
            return self.create_hold(symbol=symbol, reason="ATR NaN")

        # ADX + DI
        adx, di_p, di_m = self._compute_adx(high, low, close)

        # ADX Gate — lower threshold for Silver
        if adx < self.p["adx_min"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"ADX gate: {adx:.1f} < {self.p['adx_min']}",
            )

        # ═════════════════════════════════════════
        # DETERMINE MODE: RANGING vs TRENDING
        # ═════════════════════════════════════════
        if adx < self.p["adx_mode_switch"]:
            market_mode = "RANGING"
        else:
            market_mode = "TRENDING"

        # ═════════════════════════════════════════
        # H1 TREND GATE
        # ═════════════════════════════════════════
        h1_candles = kwargs.get("h1_candles")
        h1_long = False
        h1_short = False
        h1_trend_quality = 0.0
        h1_rsi = 50.0

        if h1_candles is not None and len(h1_candles) >= 220:
            h1c = h1_candles["close"].astype(float)
            h1_ema50 = h1c.ewm(span=self.p["h1_ema_fast"], adjust=False).mean()
            h1_ema200 = h1c.ewm(span=self.p["h1_ema_slow"], adjust=False).mean()
            h1_50v = float(h1_ema50.iloc[-1])
            h1_200v = float(h1_ema200.iloc[-1])
            h1_close = float(h1c.iloc[-1])

            if not pd.isna(h1_50v) and not pd.isna(h1_200v):
                # Relaxed: use H1 close vs EMA50 as primary direction
                # EMA50/200 crossover is bonus quality, not hard gate
                h1_long = h1_close > h1_50v
                h1_short = h1_close < h1_50v
                h1_trend_quality = abs(h1_50v - h1_200v) / max(abs(h1_200v), 1e-9)

            # H1 RSI
            h1_rsi = self._compute_rsi_series(h1c, self.p["h1_rsi_period"])
        else:
            # Fallback: use M5 EMA50 vs EMA200 as trend proxy
            h1_long = es > et
            h1_short = es < et

        if not h1_long and not h1_short:
            # In ranging mode, allow trade using M5 EMA direction
            if market_mode == "RANGING":
                h1_long = c > et
                h1_short = c < et
            else:
                return self.create_hold(
                    symbol=symbol,
                    reason="H1 gate: no clear trend (EMA50 not aligned)",
                )

        # ═════════════════════════════════════════
        # RSI (M5) — extreme block
        # ═════════════════════════════════════════
        rsi = self._compute_rsi(close)
        if rsi > 85 or rsi < 15:
            return self.create_hold(
                symbol=symbol,
                reason=f"RSI extreme: {rsi:.1f}",
            )

        # MACD
        macd_hist = self._compute_macd(close)

        # ─── Bollinger Bands ───
        bb_mid_s = close.rolling(self.p["bb_len"]).mean()
        bb_std_s = close.rolling(self.p["bb_len"]).std()
        bb_upper_s = bb_mid_s + (self.p["bb_std"] * bb_std_s)
        bb_lower_s = bb_mid_s - (self.p["bb_std"] * bb_std_s)
        bb_width_s = bb_upper_s - bb_lower_s

        bb_mid = float(bb_mid_s.iloc[-1])
        bb_upper = float(bb_upper_s.iloc[-1])
        bb_lower = float(bb_lower_s.iloc[-1])
        bb_width = float(bb_width_s.iloc[-1])

        if any(pd.isna(v) for v in [bb_mid, bb_upper, bb_lower]):
            return self.create_hold(symbol=symbol, reason="BB NaN")

        # BB Squeeze detection
        bb_width_window = bb_width_s.iloc[-100:]
        bb_squeeze_threshold = float(bb_width_window.quantile(self.p["bb_squeeze_percentile"] / 100))
        bb_squeeze = bb_width < bb_squeeze_threshold
        bb_prev_width = float(bb_width_s.iloc[-2]) if len(bb_width_s) > 1 else bb_width
        squeeze_released = bb_prev_width < bb_squeeze_threshold and bb_width > bb_prev_width

        # Volume
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        vol = candles[vol_col].astype(float) if vol_col in candles.columns else None
        vol_current = float(vol.iloc[-1]) if vol is not None else 0
        vol_avg = float(vol.rolling(self.p["vol_ma"]).mean().iloc[-1]) if vol is not None else 0

        # Swing
        sw = candles.iloc[-(self.p["swing_lookback"] + 1):-1]
        swing_high = float(sw["high"].max())
        swing_low = float(sw["low"].min())

        # ═════════════════════════════════════════
        # 7-LAYER SCORING (100 pts)
        # ═════════════════════════════════════════

        bs = 0  # buy score
        ss = 0  # sell score
        br = []  # buy reasons
        sr = []  # sell reasons

        # ── L1: M5 Trend Stack (20 pts) ──
        b, s, r1b, r1s = self._score_trend(ef, em, es, et, c)
        bs += b; ss += s; br.extend(r1b); sr.extend(r1s)

        # ── L2: H1 Trend Quality (15 pts) ──
        b, s, r2b, r2s = self._score_h1_trend(h1_long, h1_short, h1_trend_quality, h1_rsi)
        bs += b; ss += s; br.extend(r2b); sr.extend(r2s)

        # ── L3: Pullback Quality (15 pts) ──
        b, s, r3b, r3s = self._score_pullback(c, o, h, lo, em, rsi, pc, po, ph, pl, atr)
        bs += b; ss += s; br.extend(r3b); sr.extend(r3s)

        # ── L4: Momentum (15 pts) ──
        b, s, r4b, r4s = self._score_momentum(adx, di_p, di_m, rsi, macd_hist)
        bs += b; ss += s; br.extend(r4b); sr.extend(r4s)

        # ── L5: BB + Mean-Reversion Confluence (15 pts) ── Silver-specific!
        b, s, r5b, r5s = self._score_bb_mr(
            c, o, h, lo, bb_upper, bb_lower, bb_mid, bb_width, bb_squeeze,
            squeeze_released, rsi, atr, market_mode,
        )
        bs += b; ss += s; br.extend(r5b); sr.extend(r5s)

        # ── L6: Volume Confirm (10 pts) ──
        b, s, r6b, r6s = self._score_volume(vol_current, vol_avg)
        bs += b; ss += s; br.extend(r6b); sr.extend(r6s)

        # ── L7: Candle Quality (10 pts) ──
        b, s, r7b, r7s = self._score_candle(c, o, h, lo, pc, po, ph, pl, atr)
        bs += b; ss += s; br.extend(r7b); sr.extend(r7s)

        # ── Session bonus ──
        bs += session_bonus
        ss += session_bonus

        # ── AI Boost (optional +15) ──
        ai_boost_b = 0
        ai_boost_s = 0
        if self.p["ai_boost_enabled"]:
            ai_b, ai_s, aibr, aisr = self._get_ai_boost(candles, symbol, kwargs)
            ai_boost_b = ai_b
            ai_boost_s = ai_s
            br.extend(aibr)
            sr.extend(aisr)

        total_buy = bs + ai_boost_b
        total_sell = ss + ai_boost_s

        # ═════════════════════════════════════════
        # SMART BIDIRECTIONAL DECISION + MTF TREND FILTER
        # ═════════════════════════════════════════

        # กรองเทรนด์หลายทามเฟรม (H4 / H1 / M5)
        h4_candles = kwargs.get("h4_candles")
        mtf_bull = 0
        mtf_bear = 0
        # H4 (น้ำหนักสูงสุด: +5)
        if h4_candles is not None and len(h4_candles) >= 60:
            h4c = h4_candles["close"].astype(float)
            h4_ema20 = float(h4c.ewm(span=20, adjust=False).mean().iloc[-1])
            h4_ema50 = float(h4c.ewm(span=50, adjust=False).mean().iloc[-1])
            if not (pd.isna(h4_ema20) or pd.isna(h4_ema50)):
                if h4_ema20 > h4_ema50:
                    mtf_bull += 1
                    total_buy += 5
                    br.append("MTF:H4↑+5")
                elif h4_ema20 < h4_ema50:
                    mtf_bear += 1
                    total_sell += 5
                    sr.append("MTF:H4↓+5")
        # H1 (น้ำหนักกลาง: +3)
        if h1_long:
            mtf_bull += 1
            total_buy += 3
            br.append("MTF:H1↑+3")
        elif h1_short:
            mtf_bear += 1
            total_sell += 3
            sr.append("MTF:H1↓+3")
        # M5 (น้ำหนักต่ำ: +2) — จาก EMA เดิมที่คำนวณไว้แล้ว
        if es > et:   # M5 EMA50 > EMA200
            mtf_bull += 1
            total_buy += 2
            br.append("MTF:M5↑+2")
        elif es < et:
            mtf_bear += 1
            total_sell += 2
            sr.append("MTF:M5↓+2")

        # โหวตเสียงข้างมาก: ≥ 2 จาก 3 TF = ทิศทาง MTF
        mtf_bullish = mtf_bull >= 2
        mtf_bearish = mtf_bear >= 2

        # เลือกทิศทางที่ score สูงกว่า
        if total_buy >= total_sell:
            total_score = total_buy
            action = Action.BUY
            reasons = br
            is_buy = True
            is_counter_trend = mtf_bearish and not mtf_bullish  # BUY สวน MTF bearish = counter
        else:
            total_score = total_sell
            action = Action.SELL
            reasons = sr
            is_buy = False
            is_counter_trend = mtf_bullish and not mtf_bearish  # SELL สวน MTF bullish = counter

        # ถ้าไม่ชัดเจน MTF ไม่นับว่า counter-trend
        if not mtf_bullish and not mtf_bearish:
            is_counter_trend = False

        # Counter-trend gate: require higher score
        counter_enabled = self.p.get("counter_trend_enabled", True)
        min_score_counter = self.p.get("min_score_counter", 65)

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
            # Normal threshold — adaptive by mode
            if market_mode == "RANGING":
                min_score = self.p["min_score_ranging"]
            else:
                min_score = self.p["min_score_trending"]
            if total_score < min_score:
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Score {total_score}/100 < {min_score} min ({market_mode})",
                )

        # ── Adaptive TP by mode + counter-trend ──
        if is_counter_trend:
            tp_mult = self.p.get("tp_atr_mult_counter", 0.7)
            tier = "🔄 Counter"
        elif total_score >= self.p["min_score_high"]:
            if market_mode == "RANGING":
                tp_mult = self.p["tp_atr_mult_ranging"] * 1.3
            else:
                tp_mult = self.p["tp_atr_mult_trending"] * 1.25
            tier = "🔥 Elite"
        else:
            if market_mode == "RANGING":
                tp_mult = self.p["tp_atr_mult_ranging"]
            else:
                tp_mult = self.p["tp_atr_mult_trending"]
            tier = "✅ Standard"

        # No BB Mid override — let tp_mult dictate the R/R to preserve Profit Factor.
        use_bb_tp = False

        # ── Confidence (counter-trend penalty) ──
        confidence = min(0.60 + (total_score - 60) * 0.01, 0.95)
        if is_counter_trend:
            confidence -= self.p.get("counter_confidence_penalty", 0.10)
            confidence = max(confidence, 0.50)

        # ── SL / TP (Smart SL Calculator + Anti-Hunt) ──
        from app.risk.sl_calculator import calculate_smart_sl, compute_h1_atr

        h1_atr_val = None
        if h1_candles is not None and len(h1_candles) >= 20:
            h1_atr_val = compute_h1_atr(h1_candles)

        direction = "BUY" if is_buy else "SELL"
        sl_price = calculate_smart_sl(
            close=c, atr=atr, direction=direction, candles=candles,
            sl_atr_mult=self.p["sl_atr_mult"],
            sl_buffer_atr=self.p["sl_buffer_atr"],
            swing_lookback=self.p["swing_lookback"],
            min_sl_distance=self.p["min_sl_distance"],
            adx=adx, h1_atr=h1_atr_val,
            symbol=symbol,
        )

        sl_dist_actual = abs(c - sl_price)
        tp_dist = atr * tp_mult
        if is_buy:
            tp_price = c + tp_dist
        else:
            tp_price = c - tp_dist

        rr_actual = tp_dist / sl_dist_actual if sl_dist_actual > 0 else 0

        ct_tag = " [CT]" if is_counter_trend else ""
        reason_str = f"{tier}{ct_tag} {market_mode} Score={total_score}/100 RR={rr_actual:.2f} | " + "; ".join(reasons[:6])

        logger.debug("silver_evolution_signal", extra={
            "symbol": symbol, "action": action.value,
            "total_score": total_score, "confidence": round(confidence, 2),
            "rr": round(rr_actual, 2), "atr": round(atr, 4),
            "adx": round(adx, 1), "rsi": round(rsi, 1),
            "h1_long": h1_long, "h1_short": h1_short,
            "market_mode": market_mode,
            "is_counter_trend": is_counter_trend,
            "use_bb_tp": use_bb_tp,
            "stage": "signal", "result": "ok",
        })

        return Decision(
            symbol=symbol,
            action=action,
            confidence=confidence,
            reason=reason_str,
            stop_loss=sl_price,
            take_profit=tp_price,
            risk_reward_ratio=round(rr_actual, 2),
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["silver_evolution", market_mode.lower(), tier.split()[0], f"score_{total_score}"] + (["counter_trend"] if is_counter_trend else []),
            debug={
                "buy_score": bs, "sell_score": ss,
                "ai_boost_b": ai_boost_b, "ai_boost_s": ai_boost_s,
                "total_score": total_score, "rr": round(rr_actual, 2),
                "is_counter_trend": is_counter_trend,
                "adx": round(adx, 1), "rsi": round(rsi, 1),
                "macd_hist": round(macd_hist, 6),
                "h1_trend_quality": round(h1_trend_quality, 6),
                "h1_rsi": round(h1_rsi, 1),
                "atr": round(atr, 4),
                "bb_upper": round(bb_upper, 4),
                "bb_lower": round(bb_lower, 4),
                "bb_mid": round(bb_mid, 4),
                "bb_width": round(bb_width, 4),
                "squeeze_released": squeeze_released,
                "market_mode": market_mode,
                "use_bb_tp": use_bb_tp,
                "session_bonus": session_bonus,
            },
        )

    # ═══════════════════════════════════════════════
    # SCORING LAYERS
    # ═══════════════════════════════════════════════

    def _score_trend(self, ef, em, es, et, c):
        """L1: M5 Trend Stack — 20 pts max."""
        b, s = 0, 0
        br, sr = [], []

        # Full stack: EMA 9>21>50>200 (+12)
        if ef > em > es > et:
            b += 12; br.append("L1:Full bull stack")
        elif ef > em and c > et:
            b += 6; br.append("L1:Partial bull")

        if ef < em < es < et:
            s += 12; sr.append("L1:Full bear stack")
        elif ef < em and c < et:
            s += 6; sr.append("L1:Partial bear")

        # Close vs EMA50 (+4)
        if c > es: b += 4; br.append("L1:>EMA50")
        if c < es: s += 4; sr.append("L1:<EMA50")

        # EMA9 vs EMA21 (+4)
        if ef > em: b += 4; br.append("L1:EMA9>21")
        if ef < em: s += 4; sr.append("L1:EMA9<21")

        return min(b, 20), min(s, 20), br, sr

    def _score_h1_trend(self, h1_long, h1_short, h1_gap, h1_rsi):
        """L2: H1 Trend Quality — 15 pts max."""
        b, s = 0, 0
        br, sr = [], []

        # Trend alignment (+5)
        if h1_long: b += 5; br.append("L2:H1↑")
        if h1_short: s += 5; sr.append("L2:H1↓")

        # EMA gap quality — Silver has smaller gaps, adjusted thresholds (+5)
        if h1_gap > 0.002:
            b += 5; s += 5
            br.append(f"L2:H1 gap {h1_gap:.4f} strong")
            sr.append(f"L2:H1 gap {h1_gap:.4f} strong")
        elif h1_gap > 0.0008:
            b += 3; s += 3
            br.append(f"L2:H1 gap {h1_gap:.4f}")
            sr.append(f"L2:H1 gap {h1_gap:.4f}")

        # H1 RSI confirms direction (+5)
        if h1_long and 45 < h1_rsi < 75:
            b += 5; br.append(f"L2:H1 RSI={h1_rsi:.0f} bullish zone")
        if h1_short and 25 < h1_rsi < 55:
            s += 5; sr.append(f"L2:H1 RSI={h1_rsi:.0f} bearish zone")

        return min(b, 15), min(s, 15), br, sr

    def _score_pullback(self, c, o, h, lo, ema21, rsi, pc, po, ph, pl, atr):
        """L3: Pullback Quality — 15 pts max."""
        b, s = 0, 0
        br, sr = [], []

        # Price near EMA21 (within 0.7 ATR) — pullback zone (+7)
        dist_to_ema21 = abs(c - ema21)
        if dist_to_ema21 < atr * 0.7:
            if c > ema21:
                b += 7; br.append("L3:Pullback to EMA21")
            else:
                s += 7; sr.append("L3:Pullback to EMA21")

        # RSI dip into zone — wider range for Silver (+4)
        if 20 < rsi < 48:
            b += 4; br.append(f"L3:RSI dip {rsi:.0f}")
        if 52 < rsi < 80:
            s += 4; sr.append(f"L3:RSI spike {rsi:.0f}")

        # Reversal candle confirmation (+4)
        bull_reversal = c > o and c > ph
        bear_reversal = c < o and c < pl
        if bull_reversal:
            b += 4; br.append("L3:Bull reversal")
        if bear_reversal:
            s += 4; sr.append("L3:Bear reversal")

        return min(b, 15), min(s, 15), br, sr

    def _score_momentum(self, adx, di_p, di_m, rsi, macd_hist):
        """L4: Momentum — 15 pts max."""
        b, s = 0, 0
        br, sr = [], []

        # ADX strength — adjusted thresholds for Silver (+5)
        if adx > self.p["adx_strong"]:
            b += 5; s += 5
            br.append(f"L4:ADX {adx:.0f} strong")
            sr.append(f"L4:ADX {adx:.0f} strong")
        elif adx > self.p["adx_min"]:
            b += 3; s += 3
            br.append(f"L4:ADX {adx:.0f}")
            sr.append(f"L4:ADX {adx:.0f}")

        # DI crossover (+5)
        if di_p > di_m:
            b += 5; br.append("L4:DI+>DI-")
        if di_m > di_p:
            s += 5; sr.append("L4:DI->DI+")

        # MACD histogram confirmation (+5)
        if macd_hist > 0:
            b += 5; br.append("L4:MACD+")
        if macd_hist < 0:
            s += 5; sr.append("L4:MACD-")

        return min(b, 15), min(s, 15), br, sr

    def _score_bb_mr(self, c, o, h, lo, bb_upper, bb_lower, bb_mid, bb_width,
                     bb_squeeze, squeeze_released, rsi, atr, market_mode):
        """
        L5: BB + Mean-Reversion Confluence + Squeeze — 15 pts max.

        Silver-specific layer replacing SMC (Order Block / FVG).
        """
        b, s = 0, 0
        br, sr = [], []

        # ── BB Position scoring ──

        # Price at/below BB Lower → buy signal (+5)
        if c <= bb_lower:
            b += 5; br.append("L5:Close≤BB_L")
        elif c < bb_mid and (c - bb_lower) < (bb_mid - bb_lower) * 0.3:
            b += 3; br.append("L5:Near BB_L")

        # Price at/above BB Upper → sell signal (+5)
        if c >= bb_upper:
            s += 5; sr.append("L5:Close≥BB_U")
        elif c > bb_mid and (bb_upper - c) < (bb_upper - bb_mid) * 0.3:
            s += 3; sr.append("L5:Near BB_U")

        # ── RSI confluence with BB ──
        if c <= bb_lower and rsi < 35:
            b += 4; br.append(f"L5:BB_L+RSI{rsi:.0f}")
        elif c >= bb_upper and rsi > 65:
            s += 4; sr.append(f"L5:BB_U+RSI{rsi:.0f}")

        # ── Squeeze / Breakout ──
        if squeeze_released:
            # Squeeze released = strong momentum signal
            if c > bb_mid:
                b += 4; br.append("L5:Squeeze↑")
            else:
                s += 4; sr.append("L5:Squeeze↓")
        elif bb_squeeze:
            # Squeeze building — prepare for breakout (+2)
            b += 2; s += 2
            br.append("L5:Squeeze building")
            sr.append("L5:Squeeze building")

        # ── Mean-Reversion bonus in ranging mode ──
        if market_mode == "RANGING":
            # Price crossing BB mid from extreme → MR confirmation
            if c > bb_mid and lo <= bb_mid:
                b += 2; br.append("L5:MR cross mid↑")
            if c < bb_mid and h >= bb_mid:
                s += 2; sr.append("L5:MR cross mid↓")

        return min(b, 15), min(s, 15), br, sr

    def _score_volume(self, vol_current, vol_avg):
        """L6: Volume Confirm — 15 pts max (upgraded with VolumeAnalyzer)."""
        b, s = 0, 0
        br, sr = [], []

        # Basic RVOL check
        if vol_avg > 0:
            rvol = vol_current / vol_avg
            if rvol >= 1.5:
                b += 5; s += 5
                br.append(f"L6:RVOL {rvol:.1f}x spike")
                sr.append(f"L6:RVOL {rvol:.1f}x spike")
            elif rvol >= 1.2:
                b += 3; s += 3
                br.append(f"L6:RVOL {rvol:.1f}x")
                sr.append(f"L6:RVOL {rvol:.1f}x")

        # Advanced: VolumeAnalyzer (directional + delta + fakeout)
        try:
            from app.brain.volume_analysis import VolumeAnalyzer
            va = VolumeAnalyzer()
            if hasattr(self, '_last_candles') and self._last_candles is not None:
                vsig = va.analyze(self._last_candles)
                b += vsig.buy_score
                s += vsig.sell_score
                br.extend(vsig.buy_reasons)
                sr.extend(vsig.sell_reasons)
        except Exception:
            pass

        return min(b, 15), min(s, 15), br, sr

    def _score_candle(self, c, o, h, lo, pc, po, ph, pl, atr):
        """L7: Candle Quality — 10 pts max."""
        b, s = 0, 0
        br, sr = [], []

        body = abs(c - o)
        total_range = h - lo
        body_ratio = body / total_range if total_range > 0 else 0

        # Engulfing (+5)
        bull_eng = c > o and pc < po and c > po and o < pc
        bear_eng = c < o and pc > po and c < po and o > pc
        if bull_eng: b += 5; br.append("L7:Bull engulf")
        if bear_eng: s += 5; sr.append("L7:Bear engulf")

        # Momentum candle (+4)
        bull_mom = c > o and body_ratio > 0.5 and c > ph
        bear_mom = c < o and body_ratio > 0.5 and c < pl
        if bull_mom and not bull_eng: b += 4; br.append("L7:Bull momentum")
        if bear_mom and not bear_eng: s += 4; sr.append("L7:Bear momentum")

        # Pin bar (+3)
        lower_wick = min(o, c) - lo
        upper_wick = h - max(o, c)
        if body > 0:
            if lower_wick > body * 2 and c > o:
                b += 3; br.append("L7:Bull pin")
            if upper_wick > body * 2 and c < o:
                s += 3; sr.append("L7:Bear pin")

        # Strong body ratio (+2)
        if body_ratio > 0.6:
            if c > o: b += 2; br.append("L7:Strong bull body")
            else: s += 2; sr.append("L7:Strong bear body")

        return min(b, 10), min(s, 10), br, sr

    # ═══════════════════════════════════════════════
    # AI BOOST
    # ═══════════════════════════════════════════════

    def _get_ai_boost(self, candles, symbol, kwargs):
        """Get AI Deep V4 probability boost (+15 max)."""
        b, s = 0, 0
        br, sr = [], []

        try:
            if not self._ai_init_attempted:
                self._ai_init_attempted = True
                try:
                    from app.brain.deep_model_v4 import DeepModelV4
                    from app.brain.mtf_feature_engine import MTFFeatureEngine
                    self._ai_model = DeepModelV4(symbol)
                    self._ai_model._load_model()
                    self._ai_engine = MTFFeatureEngine()
                except Exception:
                    self._ai_model = None
                    self._ai_engine = None

            if self._ai_model and self._ai_model.model and self._ai_engine:
                features = self._ai_engine.build_live_features_v4(
                    candles_m5=candles,
                    candles_h1=kwargs.get("h1_candles"),
                )
                if features is not None:
                    probs = self._ai_model.predict(features)
                    if probs is not None and len(probs) >= 3:
                        p_buy, p_hold, p_sell = float(probs[0]), float(probs[1]), float(probs[2])

                        if p_buy > self.p["ai_threshold_strong"]:
                            b += 15; br.append(f"AI:BUY {p_buy:.0%}")
                        elif p_buy > self.p["ai_threshold_weak"]:
                            b += 8; br.append(f"AI:buy {p_buy:.0%}")

                        if p_sell > self.p["ai_threshold_strong"]:
                            s += 15; sr.append(f"AI:SELL {p_sell:.0%}")
                        elif p_sell > self.p["ai_threshold_weak"]:
                            s += 8; sr.append(f"AI:sell {p_sell:.0%}")
        except Exception:
            pass

        return b, s, br, sr

    # ═══════════════════════════════════════════════
    # INDICATOR HELPERS
    # ═══════════════════════════════════════════════

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
        """Compute RSI (last value)."""
        period = self.p["rsi_period"]
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

    def _compute_rsi_series(self, close, period=14):
        """Compute RSI for arbitrary series (last value)."""
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

    def _compute_macd(self, close):
        """Compute MACD histogram."""
        fast = close.ewm(span=self.p["macd_fast"], adjust=False).mean()
        slow = close.ewm(span=self.p["macd_slow"], adjust=False).mean()
        macd_line = fast - slow
        signal = macd_line.ewm(span=self.p["macd_signal"], adjust=False).mean()
        hist = macd_line - signal
        return float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0
