"""
Gold Smart Money Strategy — เกาะราคาทองไปกับเจ้ามือ (ICT/SMC).

ปรัชญา:
    - ตามรอยเจ้ามือ ไม่ใช่ตามราคา
    - เข้าหลังจากเจ้ามือกวาด SL (Liquidity Sweep)
    - เข้าที่ Order Block / Fair Value Gap
    - Session-aware: London/NY เท่านั้น (ช่วงเจ้ามือเล่น)

8-Layer Confluence:
    1. H4 Trend (EMA 50/200 alignment)
    2. Session Filter (London/NY only)
    3. Liquidity Sweep Detection (+2 pts — หัวใจของ SMC)
    4. Order Block Identification (+2 pts)
    5. Fair Value Gap (+1 pt)
    6. Volume Spike Confirmation (+1 pt)
    7. Candle Pattern Confirmation (+1 pt)
    8. RSI filter (+1 pt)

    Min confluence: 5/9 points (excl. session gate)

SL/TP:
    - SL: Below sweep wick + ATR buffer (structure-based)
    - TP: ATR × 3.0 or next OB zone (RR ≥ 1:2)

Target:
    - Win Rate: 50-60%
    - Risk:Reward: 1:2.5+
    - Profit Factor: > 1.5
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

# ═════════════════════════════════════════════
# DEFAULT PARAMETERS — fallback when DB has no entry
# All values can be overridden via strategy_params table in SQLite
# ═════════════════════════════════════════════

GOLD_SMART_MONEY_DEFAULTS = {
    # Session filter — Gold เคลื่อนตัวที่สุดช่วง London/NY
    "london_start": 7,       # 07:00 UTC = 14:00 TH
    "london_end": 16,        # 16:00 UTC = 23:00 TH
    "ny_start": 12,          # 12:00 UTC = 19:00 TH
    "ny_end": 21,            # 21:00 UTC = 04:00 TH

    # Trend (H4 approximated via M15)
    "ema_slow": 200,         # ~50 bars H4 ≈ 200 bars M15 → H4 EMA50
    "ema_trend": 800,        # ~200 bars H4 ≈ 800 bars M15 → H4 EMA200 (capped at data available)
    "ema_fast_fallback": 50, # Fallback if not enough data for 800

    # Liquidity Sweep
    "swing_lookback": 20,    # 20 bars M15 = 5 ชม. (หา swing high/low)
    "sweep_wick_min": 0.5,   # wick ต้อง sweep เลย swing อย่างน้อย $0.50
    "sweep_close_inside": True, # close ต้องกลับมาอยู่ใน range (wick rejection)

    # Order Block
    "ob_lookback": 50,       # หา OB ย้อนหลัง 50 bars (~12.5 ชม.)
    "ob_impulse_atr": 1.5,   # impulse move ต้อง > 1.5x ATR
    "ob_zone_atr_mult": 0.5, # OB zone width = candle body + ATR×0.5

    # Fair Value Gap
    "fvg_min_gap_atr": 0.3,  # gap ต้อง > 0.3x ATR ถึงนับ

    # Volume
    "vol_ma": 20,
    "vol_spike_ratio": 1.5,  # volume > 1.5x avg = spike

    # Risk
    "atr_period": 14,
    "sl_buffer_atr": 0.3,    # SL = sweep wick + ATR × 0.3
    "rr_target": 2.5,        # TP = SL × 2.5
    "min_confluence": 5,     # ต้อง ≥ 5/9 points

    # RSI
    "rsi_period": 14,
    "rsi_ob": 75,
    "rsi_os": 25,
}


class GoldSmartMoneyStrategy(BaseStrategy):
    """
    Gold Smart Money — เกาะเจ้ามือด้วย ICT/SMC concepts.

    จับ Liquidity Sweep + Order Block + FVG + Volume Spike
    ออกแบบสำหรับ M15 chart + H4 trend confirmation.
    """

    name = "gold_smart_money"
    timeframe = "M15"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
    ]

    def __init__(self) -> None:
        """Load params from DB, fallback to GOLD_SMART_MONEY_DEFAULTS."""
        super().__init__()
        from app.strategy.param_loader import get_param_loader
        loader = get_param_loader()
        if loader:
            self.p = loader.get_params(self.name, "XAUUSDc", GOLD_SMART_MONEY_DEFAULTS)
        else:
            self.p = {**GOLD_SMART_MONEY_DEFAULTS}

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "XAUUSDc"

        # ─── Data check ───
        min_bars = max(self.p["ema_slow"] + 10, self.p["ob_lookback"] + 10)
        if candles is None or len(candles) < min_bars:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {min_bars})",
            )

        # ─── Session filter: London/NY only ───
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

        # ─── Compute indicators ───
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

        # EMA for H4 trend (approximated on M15 data)
        ema_slow = close.ewm(span=self.p["ema_slow"], adjust=False).mean()
        # Use shorter EMA if not enough data for 800-bar
        trend_span = min(self.p["ema_trend"], len(candles) - 1) if len(candles) > self.p["ema_slow"] else self.p["ema_fast_fallback"]
        ema_trend = close.ewm(span=trend_span, adjust=False).mean()

        ema_s = float(ema_slow.iloc[-1])
        ema_t = float(ema_trend.iloc[-1])

        if pd.isna(ema_s) or pd.isna(ema_t):
            return self.create_hold(symbol=symbol, reason="EMA NaN")

        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(self.p["rsi_period"]).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(self.p["rsi_period"]).mean()
        rs = gain / loss
        rsi_series = 100 - (100 / (1 + rs))
        rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0

        # Volume
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        vol = candles[vol_col].astype(float) if vol_col in candles.columns else None
        vol_current = float(vol.iloc[-1]) if vol is not None else None
        vol_avg = float(vol.rolling(self.p["vol_ma"]).mean().iloc[-1]) if vol is not None else None

        # ─── Chop regime block ───
        if regime in (RegimeType.RANGING, RegimeType.LOW_VOLATILITY):
            return self.create_hold(
                symbol=symbol,
                reason=f"Regime {regime.value} — chop/sideways, SMC needs trend",
            )

        # ═════════════════════════════════════════
        # 8-LAYER CONFLUENCE CHECK
        # ═════════════════════════════════════════

        buy_score = 0
        sell_score = 0
        buy_reasons = []
        sell_reasons = []

        # ── Layer 1: H4 Trend (EMA alignment) ──
        h4_bullish = ema_s > ema_t
        h4_bearish = ema_s < ema_t

        if h4_bullish:
            buy_score += 1
            buy_reasons.append("L1:H4↑ trend")
        if h4_bearish:
            sell_score += 1
            sell_reasons.append("L1:H4↓ trend")

        # ── Layer 2: Session (already filtered above — passed = OK) ──
        # Session is a gate, not a score layer

        # ── Layer 3: Liquidity Sweep Detection (+2 pts) ──
        # Look for price taking out swing H/L then closing back inside
        swing_data = candles.iloc[-(self.p["swing_lookback"] + 1):-1]  # exclude current bar
        swing_high = float(swing_data["high"].max())
        swing_low = float(swing_data["low"].min())

        # Bullish sweep: wick goes below swing low, closes back above
        bull_sweep = (
            current_low < swing_low - self.p["sweep_wick_min"] and  # wick sweeps below
            current_close > swing_low and                  # closes back above
            current_close > current_open                   # bullish close
        )

        # Bearish sweep: wick goes above swing high, closes back below
        bear_sweep = (
            current_high > swing_high + self.p["sweep_wick_min"] and
            current_close < swing_high and
            current_close < current_open
        )

        # Also check previous bar for 2-bar sweep pattern
        prev_bull_sweep = (
            prev_low < swing_low - self.p["sweep_wick_min"] and
            current_close > swing_low and
            current_close > current_open
        )
        prev_bear_sweep = (
            prev_high > swing_high + self.p["sweep_wick_min"] and
            current_close < swing_high and
            current_close < current_open
        )

        sweep_low_price = None
        sweep_high_price = None

        if bull_sweep or prev_bull_sweep:
            buy_score += 2
            sweep_low_price = min(current_low, prev_low) if prev_bull_sweep else current_low
            buy_reasons.append(f"L3:LiqSweep↓ {sweep_low_price:.2f} < swing {swing_low:.2f}")

        if bear_sweep or prev_bear_sweep:
            sell_score += 2
            sweep_high_price = max(current_high, prev_high) if prev_bear_sweep else current_high
            sell_reasons.append(f"L3:LiqSweep↑ {sweep_high_price:.2f} > swing {swing_high:.2f}")

        # ── Layer 4: Order Block Identification (+2 pts) ──
        ob_buy_zone = self._find_order_block(
            candles, "bullish", self.p["ob_lookback"], atr_val
        )
        ob_sell_zone = self._find_order_block(
            candles, "bearish", self.p["ob_lookback"], atr_val
        )

        if ob_buy_zone and ob_buy_zone["low"] <= current_close <= ob_buy_zone["high"]:
            buy_score += 2
            buy_reasons.append(f"L4:OB↑ zone [{ob_buy_zone['low']:.2f}-{ob_buy_zone['high']:.2f}]")

        if ob_sell_zone and ob_sell_zone["low"] <= current_close <= ob_sell_zone["high"]:
            sell_score += 2
            sell_reasons.append(f"L4:OB↓ zone [{ob_sell_zone['low']:.2f}-{ob_sell_zone['high']:.2f}]")

        # ── Layer 5: Fair Value Gap (FVG) ──
        fvg_bull, fvg_bear = self._find_fvg(candles, atr_val)

        if fvg_bull and fvg_bull["low"] <= current_close <= fvg_bull["high"]:
            buy_score += 1
            buy_reasons.append(f"L5:FVG↑ [{fvg_bull['low']:.2f}-{fvg_bull['high']:.2f}]")

        if fvg_bear and fvg_bear["low"] <= current_close <= fvg_bear["high"]:
            sell_score += 1
            sell_reasons.append(f"L5:FVG↓ [{fvg_bear['low']:.2f}-{fvg_bear['high']:.2f}]")

        # ── Layer 6: Volume Spike ──
        if vol_current and vol_avg and vol_avg > 0:
            vol_ratio = vol_current / vol_avg
            if vol_ratio >= self.p["vol_spike_ratio"]:
                buy_score += 1
                sell_score += 1
                buy_reasons.append(f"L6:VolSpike {vol_ratio:.1f}x")
                sell_reasons.append(f"L6:VolSpike {vol_ratio:.1f}x")

        # ── Layer 7: Candle Pattern ──
        body = abs(current_close - current_open)
        total_range = current_high - current_low
        body_ratio = body / total_range if total_range > 0 else 0

        # Bullish patterns
        bullish_engulfing = (
            current_close > current_open and
            prev_close < prev_open and
            current_close > prev_open and
            current_open < prev_close
        )
        bullish_pin = (
            body_ratio > 0.2 and
            (current_open - current_low) > body * 2.0 and
            current_close > current_open
        )
        bullish_momentum = (
            current_close > current_open and
            body_ratio > 0.5 and
            current_close > prev_high
        )

        if bullish_engulfing or bullish_pin or bullish_momentum:
            buy_score += 1
            pattern = "engulf" if bullish_engulfing else ("pin" if bullish_pin else "momentum")
            buy_reasons.append(f"L7:{pattern}↑ candle")

        # Bearish patterns
        bearish_engulfing = (
            current_close < current_open and
            prev_close > prev_open and
            current_close < prev_open and
            current_open > prev_close
        )
        bearish_pin = (
            body_ratio > 0.2 and
            (current_high - current_open) > body * 2.0 and
            current_close < current_open
        )
        bearish_momentum = (
            current_close < current_open and
            body_ratio > 0.5 and
            current_close < prev_low
        )

        if bearish_engulfing or bearish_pin or bearish_momentum:
            sell_score += 1
            pattern = "engulf" if bearish_engulfing else ("pin" if bearish_pin else "momentum")
            sell_reasons.append(f"L7:{pattern}↓ candle")

        # ── Layer 8: RSI Filter ──
        if self.p["rsi_os"] < rsi_val < 65:
            buy_score += 1
            buy_reasons.append(f"L8:RSI {rsi_val:.0f} in zone")
        if 35 < rsi_val < self.p["rsi_ob"]:
            sell_score += 1
            sell_reasons.append(f"L8:RSI {rsi_val:.0f} in zone")

        # ═════════════════════════════════════════
        # DECISION
        # ═════════════════════════════════════════

        action = Action.HOLD
        confidence = 0.0
        reasons = []
        sl_price = None
        tp_price = None

        # ── BUY signal ──
        if buy_score >= self.p["min_confluence"] and h4_bullish:
            action = Action.BUY
            confidence = min(buy_score / 9.0, 1.0)

            # SL: below sweep wick (structure) or swing low + ATR buffer
            if sweep_low_price:
                sl_price = sweep_low_price - (atr_val * self.p["sl_buffer_atr"])
            else:
                sl_price = swing_low - (atr_val * self.p["sl_buffer_atr"])

            sl_dist = current_close - sl_price
            tp_price = current_close + (sl_dist * self.p["rr_target"])

            reasons = buy_reasons + [
                f"SL:{sl_price:.2f} ({sl_dist:.2f}dist)",
                f"TP:{tp_price:.2f} (RR={self.p['rr_target']})",
            ]

        # ── SELL signal ──
        elif sell_score >= self.p["min_confluence"] and h4_bearish:
            action = Action.SELL
            confidence = min(sell_score / 9.0, 1.0)

            if sweep_high_price:
                sl_price = sweep_high_price + (atr_val * self.p["sl_buffer_atr"])
            else:
                sl_price = swing_high + (atr_val * self.p["sl_buffer_atr"])

            sl_dist = sl_price - current_close
            tp_price = current_close - (sl_dist * self.p["rr_target"])

            reasons = sell_reasons + [
                f"SL:{sl_price:.2f} ({sl_dist:.2f}dist)",
                f"TP:{tp_price:.2f} (RR={self.p['rr_target']})",
            ]

        # ── HOLD ──
        else:
            max_score = max(buy_score, sell_score)
            side = "BUY" if buy_score > sell_score else "SELL"
            hold_reasons = buy_reasons if buy_score > sell_score else sell_reasons
            return self.create_hold(
                symbol=symbol,
                reason=f"SMC score {max_score}/9 ({side}) < {self.p['min_confluence']} | {'; '.join(hold_reasons[:3])}",
            )

        # Log signal
        logger.debug("gold_smart_money_signal", extra={
            "symbol": symbol,
            "action": action.value,
            "confidence": round(confidence, 2),
            "buy_score": buy_score,
            "sell_score": sell_score,
            "sweep": bool(sweep_low_price or sweep_high_price),
            "ob": bool(ob_buy_zone or ob_sell_zone),
            "atr": round(atr_val, 2),
            "rsi": round(rsi_val, 1),
            "rr": self.p["rr_target"],
            "stage": "signal",
            "result": "ok",
        })

        return Decision(
            symbol=symbol,
            action=action,
            confidence=confidence,
            reason="; ".join(reasons),
            stop_loss=sl_price,
            take_profit=tp_price,
            risk_reward_ratio=self.p["rr_target"],
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["gold_smart_money", "smc", f"score_{buy_score if action == Action.BUY else sell_score}"],
            debug={
                "buy_score": buy_score,
                "sell_score": sell_score,
                "sweep_low": sweep_low_price,
                "sweep_high": sweep_high_price,
                "swing_high": round(swing_high, 2),
                "swing_low": round(swing_low, 2),
                "atr": round(atr_val, 2),
                "rsi": round(rsi_val, 1),
                "h4_trend": "bull" if h4_bullish else ("bear" if h4_bearish else "neutral"),
                "regime": regime.value if hasattr(regime, 'value') else str(regime),
            },
        )

    # ═════════════════════════════════════════════
    # Helper: Find Order Block
    # ═════════════════════════════════════════════

    def _find_order_block(
        self,
        candles: pd.DataFrame,
        direction: str,
        lookback: int,
        atr: float,
    ) -> dict | None:
        """
        หา Order Block — แท่งเทียนสุดท้ายฝั่งตรงข้ามก่อน impulse move.

        Bullish OB:
            - หาแท่ง bearish สุดท้ายก่อนราคาวิ่งขึ้นแรง (> 1.5x ATR)
            - OB zone = [low, high] ของแท่ง bearish นั้น

        Bearish OB:
            - หาแท่ง bullish สุดท้ายก่อนราคาวิ่งลงแรง
            - OB zone = [low, high] ของแท่ง bullish นั้น
        """
        data = candles.iloc[-lookback:]
        if len(data) < 5:
            return None

        close = data["close"].values
        open_ = data["open"].values
        high = data["high"].values
        low = data["low"].values

        impulse_threshold = atr * self.p["ob_impulse_atr"]

        # Scan from recent to old (find most recent OB)
        for i in range(len(data) - 3, 1, -1):
            if direction == "bullish":
                # Look for bearish candle followed by strong bullish impulse
                is_opposing = close[i] < open_[i]  # bearish candle
                impulse_move = close[i + 1] - close[i]  # next candle goes up
                if is_opposing and impulse_move > impulse_threshold:
                    return {
                        "low": float(low[i]),
                        "high": float(high[i]),
                        "index": i,
                    }
            else:
                # Look for bullish candle followed by strong bearish impulse
                is_opposing = close[i] > open_[i]  # bullish candle
                impulse_move = close[i] - close[i + 1]  # next candle goes down
                if is_opposing and impulse_move > impulse_threshold:
                    return {
                        "low": float(low[i]),
                        "high": float(high[i]),
                        "index": i,
                    }

        return None

    # ═════════════════════════════════════════════
    # Helper: Find Fair Value Gap
    # ═════════════════════════════════════════════

    def _find_fvg(
        self,
        candles: pd.DataFrame,
        atr: float,
    ) -> tuple[dict | None, dict | None]:
        """
        หา Fair Value Gap (FVG) — ช่องว่าง 3-candle imbalance.

        Bullish FVG:
            - candle[2].high < candle[0].low → gap ขึ้น
            - price returns to fill gap = entry zone

        Bearish FVG:
            - candle[2].low > candle[0].high → gap ลง
        """
        min_gap = atr * self.p["fvg_min_gap_atr"]
        bull_fvg = None
        bear_fvg = None

        # Scan last 30 bars for FVG
        data = candles.iloc[-30:]
        if len(data) < 3:
            return None, None

        high_vals = data["high"].values
        low_vals = data["low"].values

        for i in range(len(data) - 3, 0, -1):
            # Bullish FVG: gap between candle[i] high and candle[i+2] low
            if low_vals[i + 2] > high_vals[i] + min_gap:
                if bull_fvg is None:
                    bull_fvg = {
                        "low": float(high_vals[i]),
                        "high": float(low_vals[i + 2]),
                    }

            # Bearish FVG: gap between candle[i+2] high and candle[i] low
            if high_vals[i + 2] < low_vals[i] - min_gap:
                if bear_fvg is None:
                    bear_fvg = {
                        "low": float(high_vals[i + 2]),
                        "high": float(low_vals[i]),
                    }

            if bull_fvg and bear_fvg:
                break

        return bull_fvg, bear_fvg
