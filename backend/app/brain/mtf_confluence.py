"""
MTF Confluence Engine — Multi-Timeframe Scoring for Super-Human Trading.

Scores 0-100 confluence across M1, M5, M15, H1 timeframes:
    Layer 1: Trend Alignment (EMA direction same on all TFs) → 30pts
    Layer 2: Momentum Coherence (RSI zones aligned) → 25pts
    Layer 3: Structure (key level proximity on higher TF) → 25pts
    Layer 4: Volume Confirmation (tick vol agrees with direction) → 20pts

Rules:
    - Advisory only — never overrides Risk Engine
    - If score < 30 → recommend HOLD (conflicting TFs)
    - If score > 75 → high confluence → boost confidence
    - RAM safe: pure numpy, no large buffers

Usage:
    engine = MTFConfluenceEngine()
    score = engine.score(candles_by_tf, direction="BUY")
    # score.total = 82, score.confidence_boost = 0.08, etc.
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)


# ====================================================================
# Data Containers
# ====================================================================

@dataclass
class MTFScore:
    """Result of multi-timeframe confluence analysis."""
    total: float = 0.0              # 0-100 total score
    direction: str = "NEUTRAL"      # BUY / SELL / NEUTRAL
    confidence_boost: float = 0.0   # -0.15 to +0.15 confidence adjustment
    should_block: bool = False      # True if TFs severely conflict

    # Layer breakdown
    trend_score: float = 0.0        # 0-30
    momentum_score: float = 0.0     # 0-25
    structure_score: float = 0.0    # 0-25
    volume_score: float = 0.0       # 0-20

    # Details for observability
    trend_direction: dict = field(default_factory=dict)   # {tf: "UP"/"DOWN"/"FLAT"}
    momentum_zones: dict = field(default_factory=dict)    # {tf: "OVERSOLD"/"NEUTRAL"/"OVERBOUGHT"}
    details: dict = field(default_factory=dict)
    reason: str = ""


# ====================================================================
# Helpers — lightweight numpy computations
# ====================================================================

def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Compute EMA using numpy (Wilder smoothing)."""
    if len(data) < period:
        return np.full_like(data, np.nan, dtype=float)
    alpha = 2.0 / (period + 1)
    result = np.empty_like(data, dtype=float)
    result[:period - 1] = np.nan
    result[period - 1] = np.mean(data[:period])
    for i in range(period, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def _rsi(close: np.ndarray, period: int = 14) -> float:
    """Compute last RSI value."""
    if len(close) < period + 1:
        return 50.0
    delta = np.diff(close)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _macd_hist(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> float:
    """Compute last MACD histogram value."""
    if len(close) < slow + signal:
        return 0.0
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    # Remove NaN for signal calculation
    valid = macd_line[~np.isnan(macd_line)]
    if len(valid) < signal:
        return 0.0
    signal_line = _ema(valid, signal)
    return float(valid[-1] - signal_line[-1])


def _ema_slope(close: np.ndarray, period: int, lookback: int = 5) -> str:
    """Determine EMA direction: UP, DOWN, or FLAT."""
    ema = _ema(close, period)
    valid = ema[~np.isnan(ema)]
    if len(valid) < lookback + 1:
        return "FLAT"
    recent = valid[-lookback:]
    slope = (recent[-1] - recent[0]) / max(abs(recent[0]), 1e-10)
    if slope > 0.0005:
        return "UP"
    elif slope < -0.0005:
        return "DOWN"
    return "FLAT"


def _rsi_zone(rsi_val: float) -> str:
    """Classify RSI into zones."""
    if rsi_val < 35:
        return "OVERSOLD"
    elif rsi_val > 65:
        return "OVERBOUGHT"
    return "NEUTRAL"


def _find_swing_levels(high: np.ndarray, low: np.ndarray, lookback: int = 20) -> tuple[float, float]:
    """Find recent swing high and swing low."""
    if len(high) < lookback:
        lookback = len(high)
    swing_high = float(np.max(high[-lookback:]))
    swing_low = float(np.min(low[-lookback:]))
    return swing_high, swing_low


# ====================================================================
# MTF Confluence Engine
# ====================================================================

class MTFConfluenceEngine:
    """
    Multi-Timeframe Confluence Scorer.

    Analyzes trend, momentum, structure, and volume across M1/M5/M15/H1
    to produce a 0-100 confluence score.

    Human traders struggle to monitor 3+ timeframes simultaneously.
    This engine does it in microseconds every cycle.
    """

    # Timeframes in priority order (lower TF → higher TF)
    TIMEFRAMES = ["M1", "M5", "M15", "H1"]

    # EMA periods for trend detection
    EMA_FAST = 9
    EMA_MID = 21
    EMA_SLOW = 50

    # Weights for higher timeframes (H1 matters more than M1)
    TF_WEIGHTS = {
        "M1": 0.10,
        "M5": 0.25,
        "M15": 0.30,
        "H1": 0.35,
    }

    def score(
        self,
        candles_by_tf: dict[str, pd.DataFrame],
        direction: str = "",  # Optional: strategy's preferred direction
        is_reversal: bool = False, # Optional: is this a counter-trend reversal setup?
    ) -> MTFScore:
        """
        Score multi-timeframe confluence.

        Args:
            candles_by_tf: {timeframe: DataFrame} with [open, high, low, close, tick_volume]
            direction: "BUY" or "SELL" — the strategy's signal direction
            is_reversal: If True, do not penalize for trading against the MTF trend

        Returns:
            MTFScore with 0-100 total and per-layer breakdown
        """
        result = MTFScore()

        # Validate: need at least M5 + one other TF
        available_tfs = [
            tf for tf in self.TIMEFRAMES
            if tf in candles_by_tf and candles_by_tf[tf] is not None
            and len(candles_by_tf[tf]) >= 30
        ]

        if len(available_tfs) < 2:
            result.reason = f"insufficient_tfs (have {len(available_tfs)}, need 2+)"
            return result

        # ── Layer 1: Trend Alignment (30pts max) ──
        result.trend_score, result.trend_direction = self._score_trend(
            candles_by_tf, available_tfs,
        )

        # ── Layer 2: Momentum Coherence (25pts max) ──
        result.momentum_score, result.momentum_zones = self._score_momentum(
            candles_by_tf, available_tfs,
        )

        # ── Layer 3: Structure — Key Level Analysis (25pts max) ──
        result.structure_score = self._score_structure(
            candles_by_tf, available_tfs,
        )

        # ── Layer 4: Volume Confirmation (20pts max) ──
        result.volume_score = self._score_volume(
            candles_by_tf, available_tfs,
        )

        # ── Aggregate ──
        result.total = (
            result.trend_score
            + result.momentum_score
            + result.structure_score
            + result.volume_score
        )

        # ── Determine direction consensus ──
        result.direction = self._determine_direction(
            result.trend_direction, available_tfs,
        )

        # ── Direction agreement check ──
        if direction and result.direction != "NEUTRAL":
            if direction.upper() != result.direction:
                if is_reversal:
                    # Smart Omni-Directional: Reversals are expected to fight the trend.
                    # As long as momentum or structure supports it, we don't penalize heavily.
                    if result.momentum_score >= 8.0 or result.structure_score >= 10.0:
                        result.reason = f"reversal_accepted (strategy={direction}, mtf={result.direction})"
                    else:
                        result.total *= 0.7  # Soft penalty if lacking momentum/structure support
                        result.reason = f"weak_reversal: lacking support"
                else:
                    # Standard strategy direction conflicts with MTF consensus
                    result.total *= 0.5  # Halve the score
                    result.reason = f"direction_conflict: strategy={direction}, mtf={result.direction}"

        # ── Confidence boost / block ──
        if result.total >= 75:
            result.confidence_boost = 0.10
            result.reason = result.reason or "high_confluence"
        elif result.total >= 60:
            result.confidence_boost = 0.05
            result.reason = result.reason or "moderate_confluence"
        elif result.total >= 40:
            result.confidence_boost = 0.0
            result.reason = result.reason or "neutral_confluence"
        elif result.total >= 30:
            result.confidence_boost = -0.05
            result.reason = result.reason or "weak_confluence"
        else:
            result.confidence_boost = -0.10
            result.should_block = True
            result.reason = result.reason or "conflicting_timeframes"

        result.details = {
            "available_tfs": available_tfs,
            "trend": result.trend_score,
            "momentum": result.momentum_score,
            "structure": result.structure_score,
            "volume": result.volume_score,
        }

        return result

    # ────────────────────────────────────────────────────────────────
    # Layer 1: Trend Alignment (30pts)
    # ────────────────────────────────────────────────────────────────

    def _score_trend(
        self,
        candles_by_tf: dict[str, pd.DataFrame],
        available_tfs: list[str],
    ) -> tuple[float, dict]:
        """
        Score trend alignment across timeframes.

        Perfect: all TFs show same EMA direction → 30pts.
        Partial: most agree → proportional score.
        Conflict: mixed directions → low score.
        """
        directions = {}
        weighted_buy = 0.0
        weighted_sell = 0.0
        total_weight = 0.0

        for tf in available_tfs:
            close = candles_by_tf[tf]["close"].values.astype(float)

            # Check EMA slopes: fast, mid, slow
            fast_dir = _ema_slope(close, self.EMA_FAST)
            mid_dir = _ema_slope(close, self.EMA_MID)
            slow_dir = _ema_slope(close, self.EMA_SLOW)

            # Majority vote for this TF
            ups = sum(1 for d in [fast_dir, mid_dir, slow_dir] if d == "UP")
            downs = sum(1 for d in [fast_dir, mid_dir, slow_dir] if d == "DOWN")

            if ups >= 2:
                tf_dir = "UP"
            elif downs >= 2:
                tf_dir = "DOWN"
            else:
                tf_dir = "FLAT"

            directions[tf] = tf_dir
            w = self.TF_WEIGHTS.get(tf, 0.2)
            total_weight += w

            if tf_dir == "UP":
                weighted_buy += w
            elif tf_dir == "DOWN":
                weighted_sell += w

        if total_weight < 0.01:
            return 0.0, directions

        # Calculate alignment score
        dominant = max(weighted_buy, weighted_sell)
        alignment = dominant / total_weight  # 0-1

        # EMA stack check on M5 (bonus for perfectly stacked EMAs)
        bonus = 0.0
        if "M5" in candles_by_tf:
            close_m5 = candles_by_tf["M5"]["close"].values.astype(float)
            ema_f = _ema(close_m5, self.EMA_FAST)
            ema_m = _ema(close_m5, self.EMA_MID)
            ema_s = _ema(close_m5, self.EMA_SLOW)
            if not np.isnan(ema_f[-1]) and not np.isnan(ema_m[-1]) and not np.isnan(ema_s[-1]):
                if ema_f[-1] > ema_m[-1] > ema_s[-1]:
                    bonus = 5.0  # Perfect bullish stack
                elif ema_f[-1] < ema_m[-1] < ema_s[-1]:
                    bonus = 5.0  # Perfect bearish stack

        score = alignment * 25.0 + bonus
        return min(30.0, score), directions

    # ────────────────────────────────────────────────────────────────
    # Layer 2: Momentum Coherence (25pts)
    # ────────────────────────────────────────────────────────────────

    def _score_momentum(
        self,
        candles_by_tf: dict[str, pd.DataFrame],
        available_tfs: list[str],
    ) -> tuple[float, dict]:
        """
        Score momentum indicator agreement.

        Checks RSI zones + MACD histogram direction across TFs.
        """
        zones = {}
        rsi_scores = []
        macd_agreements = 0
        macd_total = 0

        for tf in available_tfs:
            close = candles_by_tf[tf]["close"].values.astype(float)

            # RSI
            rsi_val = _rsi(close)
            zone = _rsi_zone(rsi_val)
            zones[tf] = zone

            # MACD histogram sign
            macd_h = _macd_hist(close)
            rsi_scores.append((tf, rsi_val, zone, macd_h))

        if not rsi_scores:
            return 0.0, zones

        # RSI Coherence: all in same zone or close
        zone_values = [z for _, _, z, _ in rsi_scores]
        unique_zones = set(zone_values)

        rsi_coherence = 0.0
        if len(unique_zones) == 1:
            rsi_coherence = 15.0  # Perfect RSI alignment
        elif len(unique_zones) == 2 and "NEUTRAL" in unique_zones:
            rsi_coherence = 10.0  # Mostly aligned (some neutral)
        else:
            rsi_coherence = 3.0   # Mixed signals

        # MACD agreement: all positive or all negative
        macd_signs = [1 if h > 0 else -1 for _, _, _, h in rsi_scores]
        if len(set(macd_signs)) == 1:
            macd_coherence = 10.0  # Perfect MACD alignment
        elif abs(sum(macd_signs)) >= len(macd_signs) - 1:
            macd_coherence = 6.0   # Mostly aligned
        else:
            macd_coherence = 2.0   # Mixed

        score = rsi_coherence + macd_coherence
        return min(25.0, score), zones

    # ────────────────────────────────────────────────────────────────
    # Layer 3: Structure — Key Levels (25pts)
    # ────────────────────────────────────────────────────────────────

    def _score_structure(
        self,
        candles_by_tf: dict[str, pd.DataFrame],
        available_tfs: list[str],
    ) -> float:
        """
        Score proximity to key structural levels.

        Higher score when:
        - Price is breaking above H1 swing high (bullish structure break)
        - Price is at support on H1 with M5 showing reversal
        - Price respects higher TF levels
        """
        score = 0.0

        # Use highest available TF for structure
        htf = None
        for tf in reversed(available_tfs):
            if tf in ["H1", "M15"]:
                htf = tf
                break

        if htf is None:
            return 8.0  # Default mid-score when no HTF available

        htf_candles = candles_by_tf[htf]
        high = htf_candles["high"].values.astype(float)
        low = htf_candles["low"].values.astype(float)
        close = htf_candles["close"].values.astype(float)

        swing_high, swing_low = _find_swing_levels(high, low, lookback=20)
        current_price = float(close[-1])
        price_range = swing_high - swing_low

        if price_range < 1e-10:
            return 8.0

        # Distance from key levels (normalized)
        dist_from_high = abs(current_price - swing_high) / price_range
        dist_from_low = abs(current_price - swing_low) / price_range

        # Near a key level = higher structure score
        min_dist = min(dist_from_high, dist_from_low)

        if min_dist < 0.05:
            # Very close to key level — high significance
            score += 18.0
        elif min_dist < 0.15:
            score += 14.0
        elif min_dist < 0.30:
            score += 10.0
        else:
            score += 5.0  # In no-man's-land

        # Structure break detection: is M5 close above/below HTF swing?
        if "M5" in candles_by_tf:
            m5_close = float(candles_by_tf["M5"]["close"].values[-1])
            m5_prev_close = float(candles_by_tf["M5"]["close"].values[-2]) if len(candles_by_tf["M5"]) > 1 else m5_close

            # Bullish breakout: M5 close broke above HTF swing high
            if m5_close > swing_high and m5_prev_close <= swing_high:
                score += 7.0
            # Bearish breakout
            elif m5_close < swing_low and m5_prev_close >= swing_low:
                score += 7.0
            # Bounce off support/resistance
            elif dist_from_low < 0.1 and m5_close > m5_prev_close:
                score += 5.0  # Bouncing off support
            elif dist_from_high < 0.1 and m5_close < m5_prev_close:
                score += 5.0  # Rejecting from resistance

        return min(25.0, score)

    # ────────────────────────────────────────────────────────────────
    # Layer 4: Volume Confirmation (20pts)
    # ────────────────────────────────────────────────────────────────

    def _score_volume(
        self,
        candles_by_tf: dict[str, pd.DataFrame],
        available_tfs: list[str],
    ) -> float:
        """
        Score volume behavior across timeframes.

        Higher score when:
        - Volume is expanding on the active TF (M5)
        - Volume confirms trend direction (rising on up-bars, falling on down-bars)
        """
        score = 0.0

        # Primary analysis on M5
        tf = "M5" if "M5" in available_tfs else available_tfs[0]
        candles = candles_by_tf[tf]

        vol_col = None
        for col in ["tick_volume", "volume", "real_volume"]:
            if col in candles.columns:
                vol_col = col
                break

        if vol_col is None:
            return 10.0  # Default mid-score when no volume data

        vol = candles[vol_col].values.astype(float)
        close = candles["close"].values.astype(float)

        if len(vol) < 20:
            return 10.0

        # Volume trend: compare recent avg to longer avg
        vol_recent = np.mean(vol[-5:])
        vol_avg = np.mean(vol[-20:])

        if vol_avg < 1e-10:
            return 10.0

        vol_ratio = vol_recent / vol_avg

        # Expanding volume = confirmation
        if vol_ratio > 1.5:
            score += 12.0  # Strong volume expansion
        elif vol_ratio > 1.2:
            score += 8.0   # Moderate expansion
        elif vol_ratio > 0.8:
            score += 5.0   # Normal volume
        else:
            score += 2.0   # Contracting volume (weak)

        # Volume-price agreement: are up-bars on high volume?
        if len(close) >= 10:
            price_changes = np.diff(close[-10:])
            volumes = vol[-9:]  # same length as diff

            up_bars = price_changes > 0
            if np.sum(up_bars) > 0 and np.sum(~up_bars) > 0:
                avg_up_vol = np.mean(volumes[up_bars])
                avg_down_vol = np.mean(volumes[~up_bars])

                if avg_down_vol > 0:
                    vpa_ratio = avg_up_vol / avg_down_vol

                    if vpa_ratio > 1.3:
                        score += 8.0   # Buyers dominating volume
                    elif vpa_ratio > 0.7:
                        score += 5.0   # Balanced
                    else:
                        score += 2.0   # Sellers dominating volume

        return min(20.0, score)

    # ────────────────────────────────────────────────────────────────
    # Direction Consensus
    # ────────────────────────────────────────────────────────────────

    def _determine_direction(
        self,
        trend_directions: dict[str, str],
        available_tfs: list[str],
    ) -> str:
        """Determine overall MTF direction from trend analysis."""
        weighted_up = 0.0
        weighted_down = 0.0
        total_weight = 0.0

        for tf in available_tfs:
            d = trend_directions.get(tf, "FLAT")
            w = self.TF_WEIGHTS.get(tf, 0.2)
            total_weight += w
            if d == "UP":
                weighted_up += w
            elif d == "DOWN":
                weighted_down += w

        if total_weight < 0.01:
            return "NEUTRAL"

        up_pct = weighted_up / total_weight
        down_pct = weighted_down / total_weight

        if up_pct > 0.6:
            return "BUY"
        elif down_pct > 0.6:
            return "SELL"
        return "NEUTRAL"
