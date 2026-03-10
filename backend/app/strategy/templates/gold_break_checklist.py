"""
Gold Break Entry Checklist — ระบบให้คะแนน 100 คะแนน สำหรับ Break High / Break โครงสร้าง

ปรัชญา:
    - ใช้เฉพาะ "Break High / Break โครงสร้าง"
    - ให้คะแนน 8 ด้าน ก่อนตัดสินใจเข้าเทรด
    - ≥ 85 = Break คุณภาพสูง 🔥, ≥ 70 = เข้าได้, < 70 = HOLD

8-Layer Scoring (100 คะแนน):
    1. Market Structure (MSB)      — 20 pts
    2. Volume Confirm              — 20 pts
    3. ADX + DI (Trend Strength)   — 15 pts
    4. VWMA Confirm (Money Flow)   — 10 pts
    5. Volume Profile (HVN/LVN)    — 10 pts
    6. Donchian Channel            — 10 pts
    7. RSI Momentum                — 10 pts
    8. Risk/Reward Setup           — 5 pts

การแปลผล:
    85–100  → Break คุณภาพสูง 🔥  (confidence 0.85+)
    70–84   → เข้าได้ แต่ SL ดี   (confidence 0.70+)
    55–69   → เสี่ยง Fake Break    → HOLD
    < 55    → ห้ามเข้า            → HOLD
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

# Indicator periods
ATR_PERIOD = 14
ADX_PERIOD = 14
RSI_PERIOD = 14
DONCHIAN_PERIOD = 20
VWMA_PERIOD = 20
VOL_MA_PERIOD = 20

# Structure
SWING_LOOKBACK = 20         # bars to find swing H/L
MSB_LOOKBACK = 50           # bars for market structure break detection
OB_PROXIMITY_ATR = 3.0      # OB "ใกล้" = within 3x ATR above

# Thresholds
ADX_STRONG = 25             # ADX > 25 = trending
ADX_WEAK = 20               # ADX < 20 = no trend
RSI_BUY_MIN = 60            # RSI > 60 for buy momentum
RSI_SELL_MAX = 40            # RSI < 40 for sell momentum
RSI_OVERBOUGHT = 80         # RSI > 80 = caution
RSI_OVERSOLD = 20           # RSI < 20 = caution
VOL_SPIKE_RATIO = 1.0       # Volume > 1.0x avg (above average)

# Risk
SL_BUFFER_ATR = 0.3         # SL buffer beyond structure
RR_HIGH = 2.5               # RR for score ≥ 85
RR_MID = 2.0                # RR for score 70-84
MIN_SCORE_TRADE = 70        # minimum score to trade

# Session filter (UTC hours)
LONDON_START = 7
LONDON_END = 16
NY_START = 12
NY_END = 21


class GoldBreakChecklistStrategy(BaseStrategy):
    """
    Gold Break Entry Checklist — ระบบ 100 คะแนนประเมิน breakout quality.

    ให้คะแนน 8 ด้าน:
        Structure(20) + Volume(20) + ADX(15) + VWMA(10)
        + VolProfile(10) + Donchian(10) + RSI(10) + Risk(5) = 100

    เข้าเทรดเมื่อคะแนน ≥ 70 เท่านั้น.
    """

    name = "gold_break_checklist"
    timeframe = "M15"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
    ]

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "XAUUSDc"

        # ─── Data check ───
        min_bars = max(MSB_LOOKBACK + 20, DONCHIAN_PERIOD + 10, 100)
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
            in_london = LONDON_START <= hour < LONDON_END
            in_ny = NY_START <= hour < NY_END
            if not (in_london or in_ny):
                return self.create_hold(
                    symbol=symbol,
                    reason=f"Session filter: hour={hour} outside London/NY",
                )

        # ─── Chop regime block ───
        if regime in (RegimeType.RANGING, RegimeType.LOW_VOLATILITY):
            return self.create_hold(
                symbol=symbol,
                reason=f"Regime {regime.value} — sideways, break unlikely",
            )

        # ─── Extract price arrays ───
        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)

        current_close = float(close.iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])
        current_open = float(open_.iloc[-1])

        # ─── Compute ATR ───
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(ATR_PERIOD).mean()
        atr_val = float(atr_series.iloc[-1])
        if pd.isna(atr_val) or atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR NaN")

        # ─── Compute ADX / DI+ / DI- ───
        adx_val, di_plus, di_minus, adx_slope = self._compute_adx_di(high, low, close)

        # ─── Compute RSI ───
        rsi_val, rsi_prev = self._compute_rsi(close)

        # ─── Compute Donchian Channel ───
        dc_upper, dc_lower, dc_upper_slope = self._compute_donchian(high, low)

        # ─── Compute VWMA ───
        vwma_val, vwma_slope = self._compute_vwma(candles, close)

        # ─── Volume ───
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        vol = candles[vol_col].astype(float) if vol_col in candles.columns else None
        vol_current = float(vol.iloc[-1]) if vol is not None else 0
        vol_prev = float(vol.iloc[-2]) if vol is not None else 0
        vol_avg = float(vol.rolling(VOL_MA_PERIOD).mean().iloc[-1]) if vol is not None else 0

        # ─── Swing High / Low ───
        swing_data = candles.iloc[-(SWING_LOOKBACK + 1):-1]
        swing_high = float(swing_data["high"].max())
        swing_low = float(swing_data["low"].min())

        # ═════════════════════════════════════════
        # DETECT BREAK DIRECTION
        # ═════════════════════════════════════════

        break_high = current_close > swing_high
        break_low = current_close < swing_low

        if not break_high and not break_low:
            return self.create_hold(
                symbol=symbol,
                reason=(
                    f"No break detected: close={current_close:.2f}, "
                    f"swing H/L={swing_high:.2f}/{swing_low:.2f}"
                ),
            )

        is_buy = break_high  # True = break high (buy), False = break low (sell)

        # ═════════════════════════════════════════
        # 8-LAYER SCORING (100 คะแนน)
        # ═════════════════════════════════════════

        scores = {}
        details = {}

        # ── 1. Market Structure (20 pts) ──
        s1, d1 = self._score_market_structure(
            candles, close, high, low, current_close, atr_val, is_buy
        )
        scores["structure"] = s1
        details["structure"] = d1

        # ── 2. Volume Confirm (20 pts) ──
        s2, d2 = self._score_volume(
            vol_current, vol_prev, vol_avg, close, vol, is_buy
        )
        scores["volume"] = s2
        details["volume"] = d2

        # ── 3. ADX + DI (15 pts) ──
        s3, d3 = self._score_adx_di(adx_val, adx_slope, di_plus, di_minus, is_buy)
        scores["adx_di"] = s3
        details["adx_di"] = d3

        # ── 4. VWMA Confirm (10 pts) ──
        s4, d4 = self._score_vwma(current_close, vwma_val, vwma_slope, is_buy)
        scores["vwma"] = s4
        details["vwma"] = d4

        # ── 5. Volume Profile (10 pts) ──
        s5, d5 = self._score_volume_profile(candles, close, high, low, current_close, atr_val, is_buy)
        scores["vol_profile"] = s5
        details["vol_profile"] = d5

        # ── 6. Donchian Channel (10 pts) ──
        s6, d6 = self._score_donchian(
            current_close, dc_upper, dc_lower, dc_upper_slope, is_buy
        )
        scores["donchian"] = s6
        details["donchian"] = d6

        # ── 7. RSI Momentum (10 pts) ──
        s7, d7 = self._score_rsi(rsi_val, rsi_prev, close, is_buy)
        scores["rsi"] = s7
        details["rsi"] = d7

        # ── 8. Risk/Reward Setup (5 pts) ──
        s8, d8 = self._score_risk_setup(
            current_close, swing_high, swing_low, dc_lower, dc_upper,
            atr_val, is_buy
        )
        scores["risk_setup"] = s8
        details["risk_setup"] = d8

        # ═════════════════════════════════════════
        # TOTAL SCORE
        # ═════════════════════════════════════════

        total_score = sum(scores.values())

        # ─── Build score breakdown string ───
        score_str = (
            f"Structure={scores['structure']}/20 "
            f"Vol={scores['volume']}/20 "
            f"ADX={scores['adx_di']}/15 "
            f"VWMA={scores['vwma']}/10 "
            f"VProfile={scores['vol_profile']}/10 "
            f"Donch={scores['donchian']}/10 "
            f"RSI={scores['rsi']}/10 "
            f"Risk={scores['risk_setup']}/5"
        )

        # ═════════════════════════════════════════
        # DECISION
        # ═════════════════════════════════════════

        if total_score < MIN_SCORE_TRADE:
            tier = "Fake Break ⚠️" if total_score >= 55 else "ห้ามเข้า ❌"
            return self.create_hold(
                symbol=symbol,
                reason=f"Break Checklist {total_score}/100 ({tier}) | {score_str}",
            )

        # ─── Determine confidence and RR by tier ───
        if total_score >= 85:
            confidence = min(0.85 + (total_score - 85) / 100, 0.95)
            rr = RR_HIGH
            tier = "🔥 High Quality Break"
        else:  # 70-84
            confidence = min(0.70 + (total_score - 70) / 100, 0.84)
            rr = RR_MID
            tier = "✅ Tradeable Break"

        # ─── SL / TP ───
        action = Action.BUY if is_buy else Action.SELL

        if is_buy:
            # SL below Donchian lower or swing low (whichever is higher = tighter)
            sl_base = max(dc_lower, swing_low) if dc_lower > 0 else swing_low
            sl_price = sl_base - (atr_val * SL_BUFFER_ATR)
            sl_dist = current_close - sl_price
            tp_price = current_close + (sl_dist * rr)
        else:
            sl_base = min(dc_upper, swing_high) if dc_upper > 0 else swing_high
            sl_price = sl_base + (atr_val * SL_BUFFER_ATR)
            sl_dist = sl_price - current_close
            tp_price = current_close - (sl_dist * rr)

        reason = (
            f"{tier} Score={total_score}/100 | {score_str} | "
            f"SL={sl_price:.2f} TP={tp_price:.2f} RR={rr}"
        )

        # ─── Log signal ───
        logger.debug("gold_break_checklist_signal", extra={
            "symbol": symbol,
            "action": action.value,
            "total_score": total_score,
            "scores": scores,
            "confidence": round(confidence, 2),
            "atr": round(atr_val, 2),
            "rsi": round(rsi_val, 1),
            "adx": round(adx_val, 1),
            "rr": rr,
            "stage": "signal",
            "result": "ok",
        })

        return Decision(
            symbol=symbol,
            action=action,
            confidence=confidence,
            reason=reason,
            stop_loss=sl_price,
            take_profit=tp_price,
            risk_reward_ratio=rr,
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["gold_break_checklist", f"score_{total_score}", tier.split()[0]],
            debug={
                "total_score": total_score,
                "scores": scores,
                "details": details,
                "swing_high": round(swing_high, 2),
                "swing_low": round(swing_low, 2),
                "atr": round(atr_val, 2),
                "adx": round(adx_val, 1),
                "di_plus": round(di_plus, 1),
                "di_minus": round(di_minus, 1),
                "rsi": round(rsi_val, 1),
                "vwma": round(vwma_val, 2) if vwma_val else None,
                "donchian_upper": round(dc_upper, 2),
                "donchian_lower": round(dc_lower, 2),
                "is_buy": is_buy,
            },
        )

    # ═════════════════════════════════════════════
    # INDICATOR HELPERS
    # ═════════════════════════════════════════════

    def _compute_adx_di(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> tuple[float, float, float, float]:
        """Compute ADX, DI+, DI-, ADX slope."""
        period = ADX_PERIOD

        # True Range
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)

        # +DM / -DM
        up_move = high - high.shift()
        down_move = low.shift() - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        plus_dm_s = pd.Series(plus_dm, index=high.index).rolling(period).mean()
        minus_dm_s = pd.Series(minus_dm, index=high.index).rolling(period).mean()
        atr_s = tr.rolling(period).mean()

        # DI+ / DI-
        di_plus_s = 100 * plus_dm_s / atr_s
        di_minus_s = 100 * minus_dm_s / atr_s

        # DX → ADX
        dx = 100 * (di_plus_s - di_minus_s).abs() / (di_plus_s + di_minus_s)
        adx_s = dx.rolling(period).mean()

        adx_val = float(adx_s.iloc[-1]) if not pd.isna(adx_s.iloc[-1]) else 0
        di_plus = float(di_plus_s.iloc[-1]) if not pd.isna(di_plus_s.iloc[-1]) else 0
        di_minus = float(di_minus_s.iloc[-1]) if not pd.isna(di_minus_s.iloc[-1]) else 0

        # ADX slope (current vs 3 bars ago)
        adx_prev = float(adx_s.iloc[-4]) if len(adx_s) >= 4 and not pd.isna(adx_s.iloc[-4]) else adx_val
        adx_slope = adx_val - adx_prev

        return adx_val, di_plus, di_minus, adx_slope

    def _compute_rsi(self, close: pd.Series) -> tuple[float, float]:
        """Compute RSI current and previous."""
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(RSI_PERIOD).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
        rsi_prev = float(rsi.iloc[-2]) if len(rsi) >= 2 and not pd.isna(rsi.iloc[-2]) else rsi_val
        return rsi_val, rsi_prev

    def _compute_donchian(
        self, high: pd.Series, low: pd.Series
    ) -> tuple[float, float, float]:
        """Compute Donchian Channel upper/lower and upper band slope."""
        dc_upper_s = high.rolling(DONCHIAN_PERIOD).max()
        dc_lower_s = low.rolling(DONCHIAN_PERIOD).min()

        dc_upper = float(dc_upper_s.iloc[-1]) if not pd.isna(dc_upper_s.iloc[-1]) else 0
        dc_lower = float(dc_lower_s.iloc[-1]) if not pd.isna(dc_lower_s.iloc[-1]) else 0

        # Upper band slope (current vs 3 bars ago)
        dc_upper_prev = float(dc_upper_s.iloc[-4]) if len(dc_upper_s) >= 4 and not pd.isna(dc_upper_s.iloc[-4]) else dc_upper
        dc_upper_slope = dc_upper - dc_upper_prev

        return dc_upper, dc_lower, dc_upper_slope

    def _compute_vwma(
        self, candles: pd.DataFrame, close: pd.Series
    ) -> tuple[float, float]:
        """Compute VWMA and its slope."""
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        if vol_col not in candles.columns:
            return float(close.iloc[-1]), 0.0

        vol = candles[vol_col].astype(float)
        # VWMA = SUM(close × volume, N) / SUM(volume, N)
        cv = close * vol
        vwma_s = cv.rolling(VWMA_PERIOD).sum() / vol.rolling(VWMA_PERIOD).sum()

        vwma_val = float(vwma_s.iloc[-1]) if not pd.isna(vwma_s.iloc[-1]) else float(close.iloc[-1])
        vwma_prev = float(vwma_s.iloc[-4]) if len(vwma_s) >= 4 and not pd.isna(vwma_s.iloc[-4]) else vwma_val
        vwma_slope = vwma_val - vwma_prev

        return vwma_val, vwma_slope

    # ═════════════════════════════════════════════
    # 8-LAYER SCORING FUNCTIONS
    # ═════════════════════════════════════════════

    def _score_market_structure(
        self,
        candles: pd.DataFrame,
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        current_close: float,
        atr_val: float,
        is_buy: bool,
    ) -> tuple[int, dict]:
        """
        Layer 1: Market Structure — 20 pts max.

        +10: MSB (Market Structure Break) — เปลี่ยนจากขาลงเป็นขาขึ้น (buy) หรือกลับกัน
        +5:  Higher High ต่อเนื่อง (buy) / Lower Low ต่อเนื่อง (sell)
        +5:  ไม่ติด Order Block ใหญ่ด้านบน/ล่าง
        """
        score = 0
        info = {}

        # ─── Find swing points for MSB detection ───
        lookback = min(MSB_LOOKBACK, len(candles) - 5)
        data = candles.iloc[-lookback:]
        highs = data["high"].values
        lows = data["low"].values
        closes = data["close"].values

        # Simple MSB: find if structure changed direction
        # Find last 4 swing points using simple peak/trough detection
        swing_highs = []
        swing_lows = []
        for i in range(2, len(data) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                swing_highs.append((i, float(highs[i])))
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                swing_lows.append((i, float(lows[i])))

        has_msb = False
        has_continuation = False

        if is_buy and len(swing_highs) >= 2 and len(swing_lows) >= 2:
            # Bullish MSB: last swing high breaks previous swing high
            # After making Higher Low
            last_sh = swing_highs[-1][1]
            prev_sh = swing_highs[-2][1]
            last_sl = swing_lows[-1][1]
            prev_sl = swing_lows[-2][1]

            if current_close > prev_sh:
                has_msb = True
                info["msb"] = f"Break above prev swing high {prev_sh:.2f}"

            if last_sh > prev_sh:
                has_continuation = True
                info["continuation"] = "Higher High confirmed"

        elif not is_buy and len(swing_highs) >= 2 and len(swing_lows) >= 2:
            last_sl = swing_lows[-1][1]
            prev_sl = swing_lows[-2][1]
            last_sh = swing_highs[-1][1]
            prev_sh = swing_highs[-2][1]

            if current_close < prev_sl:
                has_msb = True
                info["msb"] = f"Break below prev swing low {prev_sl:.2f}"

            if last_sl < prev_sl:
                has_continuation = True
                info["continuation"] = "Lower Low confirmed"

        if has_msb:
            score += 10
        if has_continuation:
            score += 5

        # ─── OB proximity check ───
        # Check if there's a big opposing "wall" (large candle body) nearby
        no_ob_wall = True
        check_range = min(20, len(data) - 1)
        for i in range(-check_range, 0):
            idx = len(data) + i
            if idx < 0 or idx >= len(data):
                continue
            body = abs(closes[idx] - data["open"].values[idx])
            if body > atr_val * 1.5:
                candle_mid = (highs[idx] + lows[idx]) / 2
                dist = abs(current_close - candle_mid)
                if dist < atr_val * OB_PROXIMITY_ATR:
                    if is_buy and candle_mid > current_close:
                        no_ob_wall = False
                        info["ob_wall"] = f"Large OB above at ~{candle_mid:.2f}"
                        break
                    elif not is_buy and candle_mid < current_close:
                        no_ob_wall = False
                        info["ob_wall"] = f"Large OB below at ~{candle_mid:.2f}"
                        break

        if no_ob_wall:
            score += 5
            info["ob_clear"] = True

        info["total"] = score
        return score, info

    def _score_volume(
        self,
        vol_current: float,
        vol_prev: float,
        vol_avg: float,
        close: pd.Series,
        vol: pd.Series | None,
        is_buy: bool,
    ) -> tuple[int, dict]:
        """
        Layer 2: Volume Confirm — 20 pts max.

        +10: Volume > 20-bar average
        +5:  Break bar volume > previous bar volume
        +5:  No Volume Divergence (price up + vol up = aligned)
        """
        score = 0
        info = {}

        if vol is None or vol_avg <= 0:
            info["skip"] = "no volume data"
            return 0, info

        # Volume > average
        if vol_current > vol_avg * VOL_SPIKE_RATIO:
            score += 10
            info["above_avg"] = f"{vol_current:.0f} > avg {vol_avg:.0f}"

        # Break bar vol > prev bar vol
        if vol_current > vol_prev:
            score += 5
            info["stronger_than_prev"] = True

        # Volume divergence check
        # Buy: price going up, volume should also be rising
        price_rising = float(close.iloc[-1]) > float(close.iloc[-2])
        vol_rising = vol_current > vol_prev

        if is_buy:
            no_divergence = price_rising and vol_rising
        else:
            price_falling = float(close.iloc[-1]) < float(close.iloc[-2])
            no_divergence = price_falling and vol_rising

        if no_divergence:
            score += 5
            info["no_divergence"] = True
        else:
            info["divergence_warning"] = True

        info["total"] = score
        return score, info

    def _score_adx_di(
        self,
        adx_val: float,
        adx_slope: float,
        di_plus: float,
        di_minus: float,
        is_buy: bool,
    ) -> tuple[int, dict]:
        """
        Layer 3: ADX + DI — 15 pts max.

        +5: ADX > 25
        +5: ADX slope rising
        +5: DI+ > DI- (buy) or DI- > DI+ (sell)
        """
        score = 0
        info = {"adx": round(adx_val, 1), "di_plus": round(di_plus, 1), "di_minus": round(di_minus, 1)}

        if adx_val > ADX_STRONG:
            score += 5
            info["adx_strong"] = True

        if adx_slope > 0:
            score += 5
            info["adx_rising"] = True

        if is_buy and di_plus > di_minus:
            score += 5
            info["di_aligned"] = "DI+ > DI-"
        elif not is_buy and di_minus > di_plus:
            score += 5
            info["di_aligned"] = "DI- > DI+"

        if adx_val < ADX_WEAK:
            info["warning"] = "ADX < 20 — no trend"

        info["total"] = score
        return score, info

    def _score_vwma(
        self,
        current_close: float,
        vwma_val: float,
        vwma_slope: float,
        is_buy: bool,
    ) -> tuple[int, dict]:
        """
        Layer 4: VWMA Confirm — 10 pts max.

        +5: Price above VWMA (buy) / below VWMA (sell)
        +5: VWMA slope up (buy) / down (sell)
        """
        score = 0
        info = {"vwma": round(vwma_val, 2)}

        if is_buy:
            if current_close > vwma_val:
                score += 5
                info["above_vwma"] = True
            if vwma_slope > 0:
                score += 5
                info["vwma_rising"] = True
        else:
            if current_close < vwma_val:
                score += 5
                info["below_vwma"] = True
            if vwma_slope < 0:
                score += 5
                info["vwma_falling"] = True

        info["total"] = score
        return score, info

    def _score_volume_profile(
        self,
        candles: pd.DataFrame,
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        current_close: float,
        atr_val: float,
        is_buy: bool,
    ) -> tuple[int, dict]:
        """
        Layer 5: Volume Profile (HVN/LVN approximation) — 10 pts max.

        +5: Break ออกจาก HVN ไป LVN (volume cluster → open space)
        +5: ไม่มี HVN ใหญ่ใกล้ด้านบน/ล่าง
        -5: ถ้า break แล้วชน POC (Point of Control) — penalty

        Uses simplified volume profile: bucket price range, sum volume per bucket.
        """
        score = 0
        info = {}

        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        if vol_col not in candles.columns:
            info["skip"] = "no volume data"
            return 0, info

        # Use last 100 bars for volume profile
        vp_data = candles.iloc[-100:]
        vp_high = float(vp_data["high"].max())
        vp_low = float(vp_data["low"].min())
        vp_range = vp_high - vp_low

        if vp_range <= 0:
            return 0, {"skip": "zero range"}

        # Create price buckets (20 buckets)
        n_buckets = 20
        bucket_size = vp_range / n_buckets
        bucket_volumes = np.zeros(n_buckets)

        for _, row in vp_data.iterrows():
            bar_mid = (float(row["high"]) + float(row["low"])) / 2
            bucket_idx = min(int((bar_mid - vp_low) / bucket_size), n_buckets - 1)
            bucket_volumes[bucket_idx] += float(row[vol_col])

        # Find POC (highest volume bucket)
        poc_idx = int(np.argmax(bucket_volumes))
        poc_price = vp_low + (poc_idx + 0.5) * bucket_size
        avg_vol = float(np.mean(bucket_volumes))

        # Current price bucket
        current_bucket = min(int((current_close - vp_low) / bucket_size), n_buckets - 1)
        current_bucket = max(0, current_bucket)

        info["poc"] = round(poc_price, 2)

        # Check: break from HVN to LVN
        # HVN = bucket with volume > avg, LVN = bucket with volume < avg
        prev_bucket = max(0, current_bucket - 1) if is_buy else min(n_buckets - 1, current_bucket + 1)
        from_hvn = bucket_volumes[prev_bucket] > avg_vol
        to_lvn = bucket_volumes[current_bucket] < avg_vol

        if from_hvn and to_lvn:
            score += 5
            info["hvn_to_lvn"] = True

        # Check: no HVN wall ahead
        if is_buy:
            ahead_buckets = bucket_volumes[current_bucket + 1:min(current_bucket + 4, n_buckets)]
        else:
            ahead_buckets = bucket_volumes[max(0, current_bucket - 3):current_bucket]

        if len(ahead_buckets) > 0:
            max_ahead = float(np.max(ahead_buckets))
            if max_ahead < avg_vol * 1.5:
                score += 5
                info["no_hvn_ahead"] = True
            else:
                info["hvn_wall_ahead"] = True

        # Penalty: break into POC
        poc_dist = abs(current_close - poc_price)
        if poc_dist < atr_val * 1.0:
            if (is_buy and poc_price > current_close) or (not is_buy and poc_price < current_close):
                score -= 5
                info["poc_penalty"] = f"POC at {poc_price:.2f} nearby"

        score = max(0, score)
        info["total"] = score
        return score, info

    def _score_donchian(
        self,
        current_close: float,
        dc_upper: float,
        dc_lower: float,
        dc_upper_slope: float,
        is_buy: bool,
    ) -> tuple[int, dict]:
        """
        Layer 6: Donchian Channel — 10 pts max.

        +5: Break Upper Band (buy) / Lower Band (sell)
        +5: Band slope rising (buy) / falling (sell)
        """
        score = 0
        info = {"upper": round(dc_upper, 2), "lower": round(dc_lower, 2)}

        if is_buy:
            if current_close >= dc_upper:
                score += 5
                info["break_upper"] = True
            if dc_upper_slope > 0:
                score += 5
                info["band_rising"] = True
        else:
            if current_close <= dc_lower:
                score += 5
                info["break_lower"] = True
            if dc_upper_slope < 0:  # overall compression / falling
                score += 5
                info["band_falling"] = True

        info["total"] = score
        return score, info

    def _score_rsi(
        self,
        rsi_val: float,
        rsi_prev: float,
        close: pd.Series,
        is_buy: bool,
    ) -> tuple[int, dict]:
        """
        Layer 7: RSI Momentum — 10 pts max.

        +5: RSI > 60 (buy) / RSI < 40 (sell)
        +5: No Bearish/Bullish Divergence

        RSI > 80 (buy) or RSI < 20 (sell) = caution warning
        """
        score = 0
        info = {"rsi": round(rsi_val, 1)}

        if is_buy:
            if rsi_val > RSI_BUY_MIN:
                score += 5
                info["momentum_ok"] = True

            # Divergence: price making new high but RSI not
            price_higher = float(close.iloc[-1]) > float(close.iloc[-5]) if len(close) >= 5 else True
            rsi_higher = rsi_val > rsi_prev
            no_divergence = not (price_higher and not rsi_higher)

            if no_divergence:
                score += 5
                info["no_divergence"] = True
            else:
                info["bearish_divergence"] = True

            if rsi_val > RSI_OVERBOUGHT:
                info["caution"] = "RSI > 80 — overbought, watch for pullback"

        else:
            if rsi_val < RSI_SELL_MAX:
                score += 5
                info["momentum_ok"] = True

            price_lower = float(close.iloc[-1]) < float(close.iloc[-5]) if len(close) >= 5 else True
            rsi_lower = rsi_val < rsi_prev
            no_divergence = not (price_lower and not rsi_lower)

            if no_divergence:
                score += 5
                info["no_divergence"] = True
            else:
                info["bullish_divergence"] = True

            if rsi_val < RSI_OVERSOLD:
                info["caution"] = "RSI < 20 — oversold, watch for bounce"

        info["total"] = score
        return score, info

    def _score_risk_setup(
        self,
        current_close: float,
        swing_high: float,
        swing_low: float,
        dc_lower: float,
        dc_upper: float,
        atr_val: float,
        is_buy: bool,
    ) -> tuple[int, dict]:
        """
        Layer 8: Risk/Reward Setup — 5 pts max.

        +5: SL ชัดเจนใต้โครงสร้าง (buy) / เหนือโครงสร้าง (sell)
        """
        score = 0
        info = {}

        if is_buy:
            # Good SL = below swing low or Donchian lower
            sl_level = max(dc_lower, swing_low) if dc_lower > 0 else swing_low
            sl_dist = current_close - sl_level
            # SL is clear if distance is reasonable (0.5-3x ATR)
            if 0.5 * atr_val <= sl_dist <= 3.0 * atr_val:
                score += 5
                info["sl_clear"] = f"SL at {sl_level:.2f} ({sl_dist:.2f} dist, {sl_dist/atr_val:.1f}x ATR)"
            else:
                info["sl_warning"] = f"SL distance {sl_dist:.2f} = {sl_dist/atr_val:.1f}x ATR (ideal 0.5-3x)"
        else:
            sl_level = min(dc_upper, swing_high) if dc_upper > 0 else swing_high
            sl_dist = sl_level - current_close
            if 0.5 * atr_val <= sl_dist <= 3.0 * atr_val:
                score += 5
                info["sl_clear"] = f"SL at {sl_level:.2f} ({sl_dist:.2f} dist, {sl_dist/atr_val:.1f}x ATR)"
            else:
                info["sl_warning"] = f"SL distance {sl_dist:.2f} = {sl_dist/atr_val:.1f}x ATR (ideal 0.5-3x)"

        info["total"] = score
        return score, info
