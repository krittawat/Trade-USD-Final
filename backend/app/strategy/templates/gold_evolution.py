"""
Gold Evolution Strategy — WR>70% Fusion for XAUUSDc M5.

"THE ULTIMATE GOLD SNIPERS" — Fuses the best from 8 proven strategies.

Architecture:
    Gate: H1 EMA50/200 trend + Session London/NY + ADX≥18
    L1: M5 Trend Stack (EMA 9/21/50/200)         — 20 pts
    L2: H1 Trend Quality (EMA gap + H1 RSI)      — 15 pts
    L3: Pullback Quality (EMA21 touch + reversal) — 15 pts
    L4: Momentum (ADX + DI + MACD)                — 15 pts
    L5: SMC Confluence (OB + FVG + Sweep)         — 15 pts
    L6: Volume Confirm (RVOL)                     — 10 pts
    L7: Candle Quality (engulfing + pin bar)       — 10 pts
    AI Boost: Deep V4 probability (optional)      — +15 bonus

Decision:
    ≥ 75 → Trade (high-quality)
    ≥ 65 → Trade (standard)
    <  65 → HOLD

Key to WR>70%: Tight TP (1.0-1.2× ATR) + selective scoring.
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

EVOLUTION_DEFAULTS = {
    # EMA (M5)
    "ema_fast": 9,
    "ema_mid": 21,
    "ema_slow": 50,
    "ema_trend": 200,

    # H1 filter
    "h1_ema_fast": 50,
    "h1_ema_slow": 200,
    "h1_rsi_period": 14,

    # ADX (M5)
    "adx_period": 14,
    "adx_min": 20,      # Hardened: 15→20 (reject weak trends)
    "adx_strong": 35,    # Hardened: 30→35

    # RSI (M5)
    "rsi_period": 14,

    # MACD (M5)
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,

    # Volume
    "vol_ma": 20,
    "vol_spike_ratio": 1.2,  # Hardened: 1.0→1.2

    # Structure
    "swing_lookback": 15,

    # SMC
    "ob_lookback": 40,
    "ob_impulse_atr": 1.5,
    "fvg_min_gap_atr": 0.3,
    "sweep_wick_min": 0.5,

    # Risk
    "atr_period": 14,
    "sl_atr_mult": 1.8,   # Hardened: 1.5→1.8 (wider SL, less stop hunts)
    "tp_atr_mult": 2.0,   # Hardened: 1.5→2.0 (wider TP, higher RR)
    "sl_buffer_atr": 0.3,
    "min_score_trade": 65,  # Hardened: 50→65 (higher selectivity)
    "min_score_high": 80,   # Hardened: 70→80 (elite tier)
    "min_rr": 1.5,          # NEW: minimum RR gate

    # Session (UTC hours)
    "session_start": 7,
    "session_end": 21,
    "overlap_start": 12,
    "overlap_end": 16,

    # AI boost
    "ai_boost_enabled": True,
    "ai_threshold_weak": 0.55,
    "ai_threshold_strong": 0.65,
}


class GoldEvolutionStrategy(BaseStrategy):
    """
    Gold Evolution — WR>70% Fusion Strategy for XAUUSDc M5.

    Combines: H1 trend filter + 7-layer 100pt scoring + AI Deep V4 boost.
    Tight TP (1.0-1.2× ATR) for maximum win rate.
    """

    name = "gold_evolution"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.STRONG_TREND,
        RegimeType.WEAK_TREND,
        RegimeType.BREAKOUT,
    ]

    def __init__(self) -> None:
        """Load params from DB, fallback to EVOLUTION_DEFAULTS."""
        try:
            from app.strategy.param_loader import get_param_loader
            loader = get_param_loader()
            if loader:
                self.p = loader.get_params(self.name, "XAUUSDc", EVOLUTION_DEFAULTS)
            else:
                self.p = {**EVOLUTION_DEFAULTS}
        except Exception:
            self.p = {**EVOLUTION_DEFAULTS}
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
        symbol = profile.symbol if profile else "XAUUSDc"
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
        current_time = None
        if "time" in candles.columns:
            current_time = candles["time"].iloc[-1]
        elif isinstance(candles.index, pd.DatetimeIndex):
            current_time = candles.index[-1]

        if current_time is not None:
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

        # ADX Gate
        if adx < self.p["adx_min"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"ADX gate: {adx:.1f} < {self.p['adx_min']}",
            )

        # Regime chop block
        if regime in (RegimeType.RANGING, RegimeType.LOW_VOLATILITY, RegimeType.ACCUMULATION):
            return self.create_hold(symbol=symbol, reason=f"Regime filtered: {regime.value}")

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
                h1_long = h1_close > h1_200v and h1_50v > h1_200v
                h1_short = h1_close < h1_200v and h1_50v < h1_200v
                h1_trend_quality = abs(h1_50v - h1_200v) / max(abs(h1_200v), 1e-9)

            # H1 RSI
            h1_rsi = self._compute_rsi_series(h1c, self.p["h1_rsi_period"])
        else:
            # Fallback: use M5 EMA200 as trend proxy
            h1_long = es > et
            h1_short = es < et

        if not h1_long and not h1_short:
            return self.create_hold(
                symbol=symbol,
                reason=f"H1 gate: no clear trend (EMA50/200 not aligned)",
            )

        # ═════════════════════════════════════════
        # RSI (M5) — extreme block
        # ═════════════════════════════════════════
        rsi = self._compute_rsi(close)
        if rsi > 82 or rsi < 18:
            return self.create_hold(
                symbol=symbol,
                reason=f"RSI extreme: {rsi:.1f}",
            )

        # MACD
        macd_hist = self._compute_macd(close)

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

        # ── L5: SMC Confluence (15 pts) ──
        b, s, r5b, r5s = self._score_smc(candles, c, o, h, lo, pc, po, ph, pl, swing_high, swing_low, atr)
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
        # DECISION
        # ═════════════════════════════════════════

        # Direction must align with H1 trend
        if total_buy >= total_sell and h1_long:
            total_score = total_buy
            action = Action.BUY
            reasons = br
            is_buy = True
        elif total_sell > total_buy and h1_short:
            total_score = total_sell
            action = Action.SELL
            reasons = sr
            is_buy = False
        else:
            ms = max(total_buy, total_sell)
            side = "BUY" if total_buy >= total_sell else "SELL"
            return self.create_hold(
                symbol=symbol,
                reason=f"Score {ms}/100 ({side}) not aligned with H1 trend",
            )

        # Score threshold
        if total_score < self.p["min_score_trade"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"Score {total_score}/100 < {self.p['min_score_trade']} min",
            )

        # ── Adaptive TP ──
        if total_score >= self.p["min_score_high"]:
            tp_mult = self.p["tp_atr_mult"] * 1.25  # let profits run more on high-quality
            tier = "🔥 Elite"
        else:
            tp_mult = self.p["tp_atr_mult"]
            tier = "✅ Standard"

        # ── Confidence ──
        confidence = min(0.60 + (total_score - 60) * 0.01, 0.95)

        # ── SL / TP (Smart SL Calculator + Anti-Hunt) ──
        from app.risk.sl_calculator import calculate_smart_sl, compute_h1_atr

        h1_atr_val = None
        h1_candles = kwargs.get("h1_candles")
        if h1_candles is not None and len(h1_candles) >= 20:
            h1_atr_val = compute_h1_atr(h1_candles)

        direction = "BUY" if is_buy else "SELL"
        sl_price = calculate_smart_sl(
            close=c, atr=atr, direction=direction, candles=candles,
            sl_atr_mult=self.p["sl_atr_mult"],
            sl_buffer_atr=self.p["sl_buffer_atr"],
            swing_lookback=self.p["swing_lookback"],
            min_sl_distance=0.0,
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

        # === MIN RR GATE ===
        min_rr_gate = self.p.get("min_rr", 1.5)
        if rr_actual < min_rr_gate:
            return self.create_hold(
                symbol=symbol,
                reason=f"RR {rr_actual:.2f} < min {min_rr_gate}",
            )

        reason_str = f"{tier} Score={total_score}/100 RR={rr_actual:.2f} | " + "; ".join(reasons[:6])

        logger.debug("gold_evolution_signal", extra={
            "symbol": symbol, "action": action.value,
            "total_score": total_score, "confidence": round(confidence, 2),
            "rr": round(rr_actual, 2), "atr": round(atr, 2),
            "adx": round(adx, 1), "rsi": round(rsi, 1),
            "h1_long": h1_long, "h1_short": h1_short,
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
            tags=["gold_evolution", tier.split()[0], f"score_{total_score}"],
            debug={
                "buy_score": bs, "sell_score": ss,
                "ai_boost_b": ai_boost_b, "ai_boost_s": ai_boost_s,
                "total_score": total_score, "rr": round(rr_actual, 2),
                "adx": round(adx, 1), "rsi": round(rsi, 1),
                "macd_hist": round(macd_hist, 4),
                "h1_trend_quality": round(h1_trend_quality, 6),
                "h1_rsi": round(h1_rsi, 1),
                "atr": round(atr, 2),
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

        # EMA gap quality (wider = stronger trend) (+5)
        if h1_gap > 0.003:
            b += 5; s += 5
            br.append(f"L2:H1 gap {h1_gap:.4f} strong")
            sr.append(f"L2:H1 gap {h1_gap:.4f} strong")
        elif h1_gap > 0.001:
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

        # Price near EMA21 (within 0.5 ATR) — pullback zone (+7)
        dist_to_ema21 = abs(c - ema21)
        if dist_to_ema21 < atr * 0.7:
            if c > ema21:  # above EMA21, touching from above = bull pullback
                b += 7; br.append(f"L3:Pullback to EMA21")
            else:
                s += 7; sr.append(f"L3:Pullback to EMA21")

        # RSI dip into zone for BUY (30-45), spike for SELL (55-70) — (+4)
        if 25 < rsi < 50:
            b += 4; br.append(f"L3:RSI dip {rsi:.0f}")
        if 50 < rsi < 75:
            s += 4; sr.append(f"L3:RSI spike {rsi:.0f}")

        # Reversal candle confirmation (+4)
        bull_reversal = c > o and c > ph  # bullish close above prev high
        bear_reversal = c < o and c < pl  # bearish close below prev low
        if bull_reversal:
            b += 4; br.append("L3:Bull reversal")
        if bear_reversal:
            s += 4; sr.append("L3:Bear reversal")

        return min(b, 15), min(s, 15), br, sr

    def _score_momentum(self, adx, di_p, di_m, rsi, macd_hist):
        """L4: Momentum — 15 pts max."""
        b, s = 0, 0
        br, sr = [], []

        # ADX strength (+5)
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

    def _score_smc(self, candles, c, o, h, lo, pc, po, ph, pl, swing_high, swing_low, atr):
        """L5: SMC Confluence (Order Block + FVG + Liquidity Sweep) — 15 pts max."""
        b, s = 0, 0
        br, sr = [], []

        # ── Liquidity Sweep (+6) ──
        sweep_min = self.p["sweep_wick_min"]
        bull_sweep = (lo < swing_low - sweep_min and c > swing_low and c > o)
        bear_sweep = (h > swing_high + sweep_min and c < swing_high and c < o)
        # 2-bar sweep
        prev_bull_sweep = (pl < swing_low - sweep_min and c > swing_low and c > o)
        prev_bear_sweep = (ph > swing_high + sweep_min and c < swing_high and c < o)

        if bull_sweep or prev_bull_sweep:
            b += 6; br.append("L5:LiqSweep↓")
        if bear_sweep or prev_bear_sweep:
            s += 6; sr.append("L5:LiqSweep↑")

        # ── Order Block (+5) ──
        ob_buy = self._find_order_block(candles, "bullish", self.p["ob_lookback"], atr)
        ob_sell = self._find_order_block(candles, "bearish", self.p["ob_lookback"], atr)

        if ob_buy and ob_buy["low"] <= c <= ob_buy["high"]:
            b += 5; br.append("L5:OB↑")
        if ob_sell and ob_sell["low"] <= c <= ob_sell["high"]:
            s += 5; sr.append("L5:OB↓")

        # ── Fair Value Gap (+4) ──
        fvg_bull, fvg_bear = self._find_fvg(candles, atr)
        if fvg_bull and fvg_bull["low"] <= c <= fvg_bull["high"]:
            b += 4; br.append("L5:FVG↑")
        if fvg_bear and fvg_bear["low"] <= c <= fvg_bear["high"]:
            s += 4; sr.append("L5:FVG↓")

        return min(b, 15), min(s, 15), br, sr

    def _score_volume(self, vol_current, vol_avg):
        """L6: Volume Confirm — 15 pts max (upgraded with VolumeAnalyzer)."""
        b, s = 0, 0
        br, sr = [], []

        # Basic RVOL check (backward compat)
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

    def _find_order_block(self, candles, direction, lookback, atr):
        """Find Order Block zone (SMC concept)."""
        data = candles.iloc[-lookback:]
        if len(data) < 5:
            return None

        close_v = data["close"].values
        open_v = data["open"].values
        high_v = data["high"].values
        low_v = data["low"].values
        threshold = atr * self.p["ob_impulse_atr"]

        for i in range(len(data) - 3, 1, -1):
            if direction == "bullish":
                is_opposing = close_v[i] < open_v[i]
                impulse = close_v[i + 1] - close_v[i]
                if is_opposing and impulse > threshold:
                    return {"low": float(low_v[i]), "high": float(high_v[i])}
            else:
                is_opposing = close_v[i] > open_v[i]
                impulse = close_v[i] - close_v[i + 1]
                if is_opposing and impulse > threshold:
                    return {"low": float(low_v[i]), "high": float(high_v[i])}
        return None

    def _find_fvg(self, candles, atr):
        """Find Fair Value Gap (bullish + bearish)."""
        min_gap = atr * self.p["fvg_min_gap_atr"]
        bull_fvg = None
        bear_fvg = None

        data = candles.iloc[-30:]
        if len(data) < 3:
            return None, None

        high_v = data["high"].values
        low_v = data["low"].values

        for i in range(len(data) - 3, 0, -1):
            if low_v[i + 2] > high_v[i] + min_gap:
                if bull_fvg is None:
                    bull_fvg = {"low": float(high_v[i]), "high": float(low_v[i + 2])}
            if high_v[i + 2] < low_v[i] - min_gap:
                if bear_fvg is None:
                    bear_fvg = {"low": float(high_v[i + 2]), "high": float(low_v[i])}
            if bull_fvg and bear_fvg:
                break

        return bull_fvg, bear_fvg
