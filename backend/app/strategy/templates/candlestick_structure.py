"""
Candlestick Structure Strategy — Bidirectional BUY/SELL from Chart Structure.

"โครงสร้างกราฟตัดสินทิศทาง" — ไม่ใช่ EMA อย่างเดียว

Philosophy:
    - Market Structure (HH/HL → BUY, LH/LL → SELL) เป็นตัวหลัก
    - BOS = trend continuation, CHoCH = potential reversal
    - Candlestick Patterns (engulfing, pin bar, hammer) ยืนยัน entry
    - SMC zones (FVG, OB) เพิ่ม confluence
    - ทั้ง BUY และ SELL คะแนนอิสระ — เลือกฝั่งที่ดีกว่า

6-Layer Scoring (100 pts + 15 bonus):
    L1: Market Structure (HH/HL / LH/LL)               — 25 pts
    L2: BOS / CHoCH confirmation                       — 20 pts
    L3: Candlestick Patterns (engulfing, pin, hammer)   — 20 pts
    L4: SMC Zones (FVG + Order Block)                   — 15 pts
    L5: Momentum Confirmation (RSI + MACD + ADX)        — 10 pts
    L6: Volume Confirmation (RVOL)                      — 10 pts
    AI Boost: optional                                  — +15 pts

Decision:
    >= 65  → Trade (standard)
    >= 80  → Trade (elite — wider TP)
    <  65  → HOLD

Symbols: Universal (Gold, Silver, BTC, Forex)
Timeframe: M5
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

STRUCTURE_DEFAULTS = {
    # Swing detection
    "swing_lookback": 5,
    "swing_lookback_long": 8,
    "min_swings": 3,

    # ADX (general market filter)
    "adx_period": 14,
    "adx_min": 15,          # Lower gate — structure is the main filter

    # RSI
    "rsi_period": 14,
    "rsi_extreme_high": 78,
    "rsi_extreme_low": 22,

    # MACD
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,

    # Volume
    "vol_ma": 20,
    "vol_spike_ratio": 1.0,

    # SMC
    "ob_lookback": 40,
    "ob_impulse_atr": 1.5,
    "fvg_min_gap_atr": 0.3,

    # Risk — wider SL for higher hit rate, tighter TP for faster wins
    "atr_period": 14,
    "sl_atr_mult": 2.0,       # Wider SL = fewer stop-outs
    "sl_buffer_atr": 0.3,
    "tp_atr_mult": 1.2,       # Tighter TP = higher WR (key to >50% WR)
    "tp_elite_mult": 1.4,     # Elite TP = base × 1.4

    # Score thresholds — require stronger structure confirmation
    "min_score_trade": 60,
    "min_score_elite": 80,

    # Session (UTC)
    "session_start": 7,
    "session_end": 21,
    "overlap_start": 12,
    "overlap_end": 16,

    # H1 filter (optional — structure remains primary)
    "h1_ema_fast": 50,
    "h1_ema_slow": 200,

    # AI boost
    "ai_boost_enabled": True,
    "ai_threshold_weak": 0.55,
    "ai_threshold_strong": 0.65,
}


class CandlestickStructureStrategy(BaseStrategy):
    """
    Candlestick Structure — BUY/SELL driven by chart structure.

    Uses PatternDetector for:
        - HH/HL (bullish) / LH/LL (bearish) → Market Structure
        - BOS (trend continuation) / CHoCH (reversal)
        - Engulfing, Pin Bar, Hammer, Morning/Evening Star
        - FVG, Order Block
    """

    name = "candlestick_structure"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.STRONG_TREND,
        RegimeType.WEAK_TREND,
        RegimeType.BREAKOUT,
        RegimeType.RANGING,         # CHoCH can signal reversal in ranging
    ]

    def __init__(self) -> None:
        """Load params from DB, fallback to STRUCTURE_DEFAULTS."""
        try:
            from app.strategy.param_loader import get_param_loader
            loader = get_param_loader()
            if loader:
                self.p = loader.get_params(self.name, "ALL", STRUCTURE_DEFAULTS)
            else:
                self.p = {**STRUCTURE_DEFAULTS}
        except Exception:
            self.p = {**STRUCTURE_DEFAULTS}

        self._detector = None
        self._ai_model = None
        self._ai_engine = None
        self._ai_init_attempted = False

    def _get_detector(self):
        """Lazy-load PatternDetector."""
        if self._detector is None:
            from app.brain.pattern_detector import PatternDetector
            self._detector = PatternDetector()
        return self._detector

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
        symbol = profile.symbol if profile else "UNKNOWN"

        # ─── Data check ───
        min_bars = max(self.p["atr_period"], 50) + 50
        if candles is None or len(candles) < min_bars:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {min_bars})",
            )

        # ─── Session gate (optional) ───
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
                # BTC is 24/7 — only gate non-crypto
                asset = symbol.upper()
                if not any(m in asset for m in ["BTC", "ETH", "SOL", "BNB"]):
                    return self.create_hold(
                        symbol=symbol,
                        reason=f"Session gate: hour={hour}",
                    )
            if self.p["overlap_start"] <= hour < self.p["overlap_end"]:
                session_bonus = 3

        # ─── Price arrays ───
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

        # ─── ATR ───
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(self.p["atr_period"]).mean()
        atr = float(atr_series.iloc[-1])
        if pd.isna(atr) or atr <= 0:
            return self.create_hold(symbol=symbol, reason="ATR NaN")

        # ─── ADX (light gate) ───
        adx, di_p, di_m = self._compute_adx(high, low, close)

        # ─── RSI extreme block ───
        rsi = self._compute_rsi(close)
        if rsi > self.p["rsi_extreme_high"] or rsi < self.p["rsi_extreme_low"]:
            return self.create_hold(symbol=symbol, reason=f"RSI extreme: {rsi:.1f}")

        # ─── MACD ───
        macd_hist = self._compute_macd(close)

        # ─── Volume ───
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        vol = candles[vol_col].astype(float) if vol_col in candles.columns else None
        vol_current = float(vol.iloc[-1]) if vol is not None else 0
        vol_avg = float(vol.rolling(self.p["vol_ma"]).mean().iloc[-1]) if vol is not None else 0

        # ─── Pattern Detection ───
        detector = self._get_detector()
        try:
            pattern_signals = detector.detect_all(candles)
        except Exception:
            pattern_signals = []

        # ═════════════════════════════════════════
        # 6-LAYER SCORING (100 pts)
        # ═════════════════════════════════════════

        bs = 0  # buy score
        ss = 0  # sell score
        br = []  # buy reasons
        sr = []  # sell reasons

        # ── L1: Market Structure (25 pts) ──
        b, s, r1b, r1s = self._score_market_structure(
            high.values, low.values, close.values, pattern_signals
        )
        bs += b; ss += s; br.extend(r1b); sr.extend(r1s)

        # ── L2: BOS / CHoCH (20 pts) ──
        b, s, r2b, r2s = self._score_bos_choch(pattern_signals)
        bs += b; ss += s; br.extend(r2b); sr.extend(r2s)

        # ── L3: Candlestick Patterns (20 pts) ──
        b, s, r3b, r3s = self._score_candle_patterns(
            c, o, h, lo, pc, po, ph, pl, atr, pattern_signals
        )
        bs += b; ss += s; br.extend(r3b); sr.extend(r3s)

        # ── L4: SMC Zones (15 pts) ──
        b, s, r4b, r4s = self._score_smc_zones(candles, c, atr, pattern_signals)
        bs += b; ss += s; br.extend(r4b); sr.extend(r4s)

        # ── L5: Momentum (10 pts) ──
        b, s, r5b, r5s = self._score_momentum(adx, di_p, di_m, rsi, macd_hist)
        bs += b; ss += s; br.extend(r5b); sr.extend(r5s)

        # ── L6: Volume (10 pts) ──
        b, s, r6b, r6s = self._score_volume(vol_current, vol_avg)
        bs += b; ss += s; br.extend(r6b); sr.extend(r6s)

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
        # DIRECTION — choose higher score
        # ═════════════════════════════════════════

        # Optional H1 trend check (soft boost, not hard gate)
        h1_long, h1_short = self._check_h1_trend(kwargs)

        # Apply H1 alignment bonus (+5)
        if h1_long:
            total_buy += 5
            br.append("H1:↑ aligned")
        if h1_short:
            total_sell += 5
            sr.append("H1:↓ aligned")

        # Direction: pick the stronger side
        if total_buy >= total_sell:
            total_score = total_buy
            action = Action.BUY
            reasons = br
            is_buy = True
        else:
            total_score = total_sell
            action = Action.SELL
            reasons = sr
            is_buy = False

        # Minimum structure requirement — must have L1 or L2 points
        l1_l2_buy = sum(1 for r in br if r.startswith(("L1:", "L2:")))
        l1_l2_sell = sum(1 for r in sr if r.startswith(("L1:", "L2:")))
        l1_l2_score = l1_l2_buy if is_buy else l1_l2_sell
        if l1_l2_score == 0:
            return self.create_hold(
                symbol=symbol,
                reason=f"No structure signal (L1+L2=0) B={total_buy} S={total_sell}",
            )

        # Score threshold
        if total_score < self.p["min_score_trade"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"Score {total_score}/100 < {self.p['min_score_trade']}",
            )

        # ── Adaptive TP ──
        if total_score >= self.p["min_score_elite"]:
            tp_mult = self.p["tp_atr_mult"] * self.p["tp_elite_mult"]
            tier = "🔥 Elite"
        else:
            tp_mult = self.p["tp_atr_mult"]
            tier = "✅ Standard"

        # ── Confidence ──
        confidence = min(0.55 + (total_score - 55) * 0.01, 0.95)

        # ── Strategy Type (Continuation vs Reversal) ──
        is_reversal_trade = False
        reversal_keywords = ["choch", "os→buy", "ob→sell", "reversal", "mean_rev", "exhaustion"]
        for r in reasons:
            if any(k in r.lower() for k in reversal_keywords):
                is_reversal_trade = True
                break
        trade_type_tag = "reversal" if is_reversal_trade else "continuation"

        # ── SL / TP ──
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

        sl_dist = abs(c - sl_price)
        tp_dist = atr * tp_mult
        tp_price = (c + tp_dist) if is_buy else (c - tp_dist)
        rr_actual = tp_dist / sl_dist if sl_dist > 0 else 0

        reason_str = f"{tier} Score={total_score}/115 RR={rr_actual:.2f} | " + "; ".join(reasons[:6])

        logger.debug("candlestick_structure_signal", extra={
            "symbol": symbol, "action": action.value,
            "total_score": total_score, "confidence": round(confidence, 2),
            "buy_score": bs, "sell_score": ss,
            "rr": round(rr_actual, 2), "atr": round(atr, 2),
            "adx": round(adx, 1), "rsi": round(rsi, 1),
            "patterns": len(pattern_signals),
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
            tags=["candlestick_structure", tier.split()[0], f"score_{total_score}", trade_type_tag],
            debug={
                "buy_score": bs, "sell_score": ss,
                "ai_boost_b": ai_boost_b, "ai_boost_s": ai_boost_s,
                "total_score": total_score, "rr": round(rr_actual, 2),
                "adx": round(adx, 1), "rsi": round(rsi, 1),
                "macd_hist": round(macd_hist, 4),
                "atr": round(atr, 2),
                "patterns_found": len(pattern_signals),
                "session_bonus": session_bonus,
                "l1_l2_score": l1_l2_score,
            },
        )

    # ═══════════════════════════════════════════════
    # SCORING LAYERS
    # ═══════════════════════════════════════════════

    def _score_market_structure(self, h_arr, l_arr, c_arr, signals):
        """L1: Market Structure — HH/HL / LH/LL (25 pts max)."""
        b, s = 0, 0
        br, sr = [], []

        # From PatternDetector signals
        for sig in signals:
            if sig.name == "hh_hl_uptrend":
                b += 20
                br.append(f"L1:HH/HL uptrend (str={sig.strength:.2f})")
            elif sig.name == "lh_ll_downtrend":
                s += 20
                sr.append(f"L1:LH/LL downtrend (str={sig.strength:.2f})")

        # Manual swing check for additional points
        n = len(c_arr)
        if n >= 30:
            # Simple HH/HL check on last 30 bars
            lookback = min(self.p["swing_lookback_long"], n - 5)
            recent_high = np.max(h_arr[-lookback:])
            prev_region_high = np.max(h_arr[-lookback*2:-lookback]) if n >= lookback * 2 else 0
            recent_low = np.min(l_arr[-lookback:])
            prev_region_low = np.min(l_arr[-lookback*2:-lookback]) if n >= lookback * 2 else float('inf')

            if recent_high > prev_region_high and recent_low > prev_region_low:
                if b == 0:  # Don't double-count
                    b += 15
                    br.append("L1:Manual HH/HL confirmed")
                else:
                    b += 5
                    br.append("L1:HH/HL reinforced")
            elif recent_high < prev_region_high and recent_low < prev_region_low:
                if s == 0:
                    s += 15
                    sr.append("L1:Manual LH/LL confirmed")
                else:
                    s += 5
                    sr.append("L1:LH/LL reinforced")

        return min(b, 25), min(s, 25), br, sr

    def _score_bos_choch(self, signals):
        """L2: BOS / CHoCH (20 pts max)."""
        b, s = 0, 0
        br, sr = [], []

        for sig in signals:
            if sig.name == "bullish_bos":
                b += 12
                br.append(f"L2:BOS↑ (str={sig.strength:.2f})")
            elif sig.name == "bearish_bos":
                s += 12
                sr.append(f"L2:BOS↓ (str={sig.strength:.2f})")
            elif sig.name == "bullish_choch":
                b += 15
                br.append(f"L2:CHoCH↑ reversal (str={sig.strength:.2f})")
            elif sig.name == "bearish_choch":
                s += 15
                sr.append(f"L2:CHoCH↓ reversal (str={sig.strength:.2f})")

        return min(b, 20), min(s, 20), br, sr

    def _score_candle_patterns(self, c, o, h, lo, pc, po, ph, pl, atr, signals):
        """L3: Candlestick Patterns (20 pts max)."""
        b, s = 0, 0
        br, sr = [], []

        # From PatternDetector
        for sig in signals:
            if sig.direction == "bullish":
                pts = self._pattern_points(sig)
                b += pts
                br.append(f"L3:{sig.name} ({pts}pts)")
            elif sig.direction == "bearish":
                pts = self._pattern_points(sig)
                s += pts
                sr.append(f"L3:{sig.name} ({pts}pts)")

        # Manual engulfing check (in case detector missed)
        body = abs(c - o)
        total_range = h - lo
        if body == 0 and total_range == 0:
            pass
        else:
            bull_eng = c > o and pc < po and c > po and o < pc
            bear_eng = c < o and pc > po and c < po and o > pc
            if bull_eng and b < 5:
                b += 5
                br.append("L3:Manual bull engulf")
            if bear_eng and s < 5:
                s += 5
                sr.append("L3:Manual bear engulf")

            # Pin bar
            if body > 0:
                lower_wick = min(o, c) - lo
                upper_wick = h - max(o, c)
                if lower_wick > body * 2.5 and c > o and b < 15:
                    b += 4
                    br.append("L3:Bull pin bar")
                if upper_wick > body * 2.5 and c < o and s < 15:
                    s += 4
                    sr.append("L3:Bear pin bar")

        return min(b, 20), min(s, 20), br, sr

    def _pattern_points(self, sig):
        """Convert pattern signal to points based on strength."""
        if sig.strength >= 0.80:
            return 8
        elif sig.strength >= 0.70:
            return 6
        elif sig.strength >= 0.55:
            return 4
        else:
            return 2

    def _score_smc_zones(self, candles, c, atr, signals):
        """L4: SMC Zones — FVG + Order Block (15 pts max)."""
        b, s = 0, 0
        br, sr = [], []

        # From PatternDetector signals
        for sig in signals:
            if sig.name == "bullish_fvg":
                b += 6
                br.append("L4:FVG↑")
            elif sig.name == "bearish_fvg":
                s += 6
                sr.append("L4:FVG↓")
            elif sig.name == "bullish_ob":
                b += 8
                br.append("L4:OB↑")
            elif sig.name == "bearish_ob":
                s += 8
                sr.append("L4:OB↓")

        # Additional manual OB check
        if b == 0 and s == 0:
            ob_buy = self._find_order_block(candles, "bullish", self.p["ob_lookback"], atr)
            ob_sell = self._find_order_block(candles, "bearish", self.p["ob_lookback"], atr)

            if ob_buy and ob_buy["low"] <= c <= ob_buy["high"]:
                b += 7
                br.append("L4:OB↑ manual")
            if ob_sell and ob_sell["low"] <= c <= ob_sell["high"]:
                s += 7
                sr.append("L4:OB↓ manual")

        return min(b, 15), min(s, 15), br, sr

    def _score_momentum(self, adx, di_p, di_m, rsi, macd_hist):
        """L5: Momentum Confirmation (10 pts max)."""
        b, s = 0, 0
        br, sr = [], []

        # DI crossover (+3)
        if di_p > di_m:
            b += 3
            br.append("L5:DI+>DI-")
        if di_m > di_p:
            s += 3
            sr.append("L5:DI->DI+")

        # RSI zone (+3)
        if 35 < rsi < 65:
            # Neutral zone — slight direction bias
            if rsi > 50:
                b += 2
                br.append(f"L5:RSI {rsi:.0f} bull zone")
            else:
                s += 2
                sr.append(f"L5:RSI {rsi:.0f} bear zone")
        elif rsi >= 65:
            s += 3
            sr.append(f"L5:RSI {rsi:.0f} OB→sell bias")
        elif rsi <= 35:
            b += 3
            br.append(f"L5:RSI {rsi:.0f} OS→buy bias")

        # MACD (+4)
        if macd_hist > 0:
            b += 4
            br.append("L5:MACD+")
        if macd_hist < 0:
            s += 4
            sr.append("L5:MACD-")

        return min(b, 10), min(s, 10), br, sr

    def _score_volume(self, vol_current, vol_avg):
        """L6: Volume Confirmation (10 pts max)."""
        b, s = 0, 0
        br, sr = [], []

        if vol_avg > 0:
            rvol = vol_current / vol_avg
            if rvol >= 1.5:
                b += 10; s += 10
                br.append(f"L6:RVOL {rvol:.1f}x spike")
                sr.append(f"L6:RVOL {rvol:.1f}x spike")
            elif rvol >= 1.2:
                b += 7; s += 7
                br.append(f"L6:RVOL {rvol:.1f}x")
                sr.append(f"L6:RVOL {rvol:.1f}x")
            elif rvol >= self.p["vol_spike_ratio"]:
                b += 4; s += 4
                br.append(f"L6:RVOL {rvol:.1f}x ok")
                sr.append(f"L6:RVOL {rvol:.1f}x ok")

        return min(b, 10), min(s, 10), br, sr

    # ═══════════════════════════════════════════════
    # H1 TREND (soft filter — boost only)
    # ═══════════════════════════════════════════════

    def _check_h1_trend(self, kwargs):
        """Check H1 trend for bonus points (not a gate)."""
        h1_candles = kwargs.get("h1_candles")
        h1_long = False
        h1_short = False

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

        return h1_long, h1_short

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

        di_plus_s = 100 * plus_dm_s / (atr_s + 1e-10)
        di_minus_s = 100 * minus_dm_s / (atr_s + 1e-10)
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
