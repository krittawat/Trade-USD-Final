# -*- coding: utf-8 -*-
"""
SMT Divergence — Smart Money Technique (Intermarket Analysis)
==============================================================
PURPOSE:
    Compares XAUUSD (Gold) and XAGUSD (Silver) swing structures to detect
    "Smart Money" divergence, a powerful institutional reversal signal.

THEORY:
    Gold and Silver are 85-95% correlated. When they diverge at swing points,
    it signals institutional manipulation (Stop Hunt / Liquidity Grab).

    Bullish SMT: Gold makes a LOWER LOW but Silver makes a HIGHER LOW.
                 → Smart Money accumulated at the fake breakdown.

    Bearish SMT: Gold makes a HIGHER HIGH but Silver makes a LOWER HIGH.
                 → Smart Money distributed at the fake breakout.

USAGE:
    Called from antigravity_alpha.py to boost confidence when SMT confirms a sweep.
    Can also be used as a standalone confirmation filter.

SAFETY:
    - Read-only analysis (no trade execution)
    - Returns None if insufficient data or no divergence detected
    - Requires both Gold and Silver data to function
"""

import logging
from typing import Optional, Dict
import pandas as pd
import numpy as np

logger = logging.getLogger("opus_logger")

# ─── CONFIGURATION ──────────────────────────────────
SWING_LOOKBACK = 20          # Bars to look for swing highs/lows
MIN_BARS_REQUIRED = 50       # Minimum bars needed for analysis
DIVERGENCE_TOLERANCE = 0.001 # 0.1% tolerance for "flat" swings


def find_swing_low(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> tuple:
    """
    Finds the two most recent swing lows in the dataframe.
    Returns (prev_swing_low, current_swing_low) or (None, None).
    """
    if len(df) < lookback * 2:
        return None, None

    lows = df['low'].values
    swing_lows = []

    # Find local minima using a simple pivot detection
    for i in range(lookback, len(lows) - 2):
        window = lows[max(0, i - lookback):i + lookback + 1]
        if lows[i] == min(window):
            swing_lows.append((i, lows[i]))

    if len(swing_lows) < 2:
        return None, None

    # Return the two most recent
    return swing_lows[-2][1], swing_lows[-1][1]


def find_swing_high(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> tuple:
    """
    Finds the two most recent swing highs in the dataframe.
    Returns (prev_swing_high, current_swing_high) or (None, None).
    """
    if len(df) < lookback * 2:
        return None, None

    highs = df['high'].values
    swing_highs = []

    for i in range(lookback, len(highs) - 2):
        window = highs[max(0, i - lookback):i + lookback + 1]
        if highs[i] == max(window):
            swing_highs.append((i, highs[i]))

    if len(swing_highs) < 2:
        return None, None

    return swing_highs[-2][1], swing_highs[-1][1]


def detect_smt_divergence(
    gold_df: pd.DataFrame,
    silver_df: pd.DataFrame,
) -> Optional[Dict]:
    """
    Detects Smart Money Technique (SMT) Divergence between Gold and Silver.

    Returns:
        dict with keys:
            - type: "BULLISH" or "BEARISH"
            - confidence_boost: float (0.03 to 0.08)
            - reason: str (human-readable explanation)
        or None if no divergence detected.
    """
    if gold_df is None or silver_df is None:
        return None

    if len(gold_df) < MIN_BARS_REQUIRED or len(silver_df) < MIN_BARS_REQUIRED:
        return None

    # ─── BULLISH SMT: Gold LL + Silver HL ─────────────
    gold_prev_low, gold_curr_low = find_swing_low(gold_df)
    silver_prev_low, silver_curr_low = find_swing_low(silver_df)

    if all(v is not None for v in [gold_prev_low, gold_curr_low, silver_prev_low, silver_curr_low]):
        gold_made_ll = gold_curr_low < gold_prev_low * (1 - DIVERGENCE_TOLERANCE)
        silver_made_hl = silver_curr_low > silver_prev_low * (1 + DIVERGENCE_TOLERANCE)

        if gold_made_ll and silver_made_hl:
            # Confidence boost based on the magnitude of divergence
            gold_drop_pct = abs(gold_curr_low - gold_prev_low) / gold_prev_low
            silver_rise_pct = abs(silver_curr_low - silver_prev_low) / silver_prev_low
            magnitude = gold_drop_pct + silver_rise_pct

            boost = min(0.08, 0.03 + magnitude * 10)  # 3-8% boost

            reason = (
                f"SMT Bullish: Gold LL ({gold_prev_low:.2f}→{gold_curr_low:.2f}) "
                f"+ Silver HL ({silver_prev_low:.4f}→{silver_curr_low:.4f})"
            )
            logger.info(f"  💎 {reason} | Boost: +{boost:.1%}")

            return {
                "type": "BULLISH",
                "confidence_boost": boost,
                "reason": reason,
            }

    # ─── BEARISH SMT: Gold HH + Silver LH ─────────────
    gold_prev_high, gold_curr_high = find_swing_high(gold_df)
    silver_prev_high, silver_curr_high = find_swing_high(silver_df)

    if all(v is not None for v in [gold_prev_high, gold_curr_high, silver_prev_high, silver_curr_high]):
        gold_made_hh = gold_curr_high > gold_prev_high * (1 + DIVERGENCE_TOLERANCE)
        silver_made_lh = silver_curr_high < silver_prev_high * (1 - DIVERGENCE_TOLERANCE)

        if gold_made_hh and silver_made_lh:
            gold_rise_pct = abs(gold_curr_high - gold_prev_high) / gold_prev_high
            silver_drop_pct = abs(silver_prev_high - silver_curr_high) / silver_prev_high
            magnitude = gold_rise_pct + silver_drop_pct

            boost = min(0.08, 0.03 + magnitude * 10)

            reason = (
                f"SMT Bearish: Gold HH ({gold_prev_high:.2f}→{gold_curr_high:.2f}) "
                f"+ Silver LH ({silver_prev_high:.4f}→{silver_curr_high:.4f})"
            )
            logger.info(f"  💎 {reason} | Boost: +{boost:.1%}")

            return {
                "type": "BEARISH",
                "confidence_boost": boost,
                "reason": reason,
            }

    return None


class SMTDivergenceTracker:
    """
    Caches the latest SMT divergence result to avoid recalculating every bar.
    Refreshes every N cycles.
    """

    def __init__(self):
        self._cache: Optional[Dict] = None
        self._last_refresh_cycle: int = -1
        self._refresh_interval: int = 3  # Refresh every 3 cycles

    def get_divergence(self) -> Optional[Dict]:
        """Returns the cached SMT divergence result."""
        return self._cache

    def refresh(self, gold_df: pd.DataFrame, silver_df: pd.DataFrame,
                current_cycle: int = 0) -> Optional[Dict]:
        """
        Recalculates SMT divergence if enough cycles have passed.
        """
        if current_cycle > 0 and (current_cycle - self._last_refresh_cycle) < self._refresh_interval:
            return self._cache

        self._last_refresh_cycle = current_cycle
        self._cache = detect_smt_divergence(gold_df, silver_df)
        return self._cache


# Singleton
smt_tracker = SMTDivergenceTracker()
