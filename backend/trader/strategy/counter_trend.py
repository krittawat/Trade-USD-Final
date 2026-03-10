# -*- coding: utf-8 -*-
"""
Counter Trend Scalper — Mean Reversion at Extremes
Ported จาก gold-risk-engine counter_trend_scalper.py

LOGIC:
- BUY: RSI < 25 AND Close < BB Lower (extreme oversold)
- SELL: RSI > 75 AND Close > BB Upper (extreme overbought)
- ADX must be ≤ threshold (range market only)
- Asset-specific ADX/SL/TP multipliers
- Cooldown timer to prevent overtrading

DANGER ZONE: This strategy trades AGAINST the trend!
Only fires in extreme conditions with tight stops.
"""
import json
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("counter_trend")

with open("d:/VibeCode/Trade/backend/trader/config/settings.json") as _f:
    _CT_CFG = json.load(_f).get("strategy", {}).get("counter_trend", {})

RSI_OVERBOUGHT = 65       # V2: Widened from 75 for more trades
RSI_OVERSOLD = 35          # V2: Widened from 25 for more trades
MIN_CONFIDENCE = 0.70      # V2: Lowered from 0.8 — still high for counter-trend
COOLDOWN_BARS = _CT_CFG.get("cooldown_bars", 5)

# Module-level cooldown tracking
_last_signal_bar = {"BUY": -999, "SELL": -999}


def reset_cooldown():
    """Reset cooldown state — call at start of each backtest/session."""
    global _last_signal_bar
    _last_signal_bar = {"BUY": -999, "SELL": -999}


def _get_asset_params(symbol: str) -> dict:
    """Asset-specific tuning."""
    sym = symbol.upper()
    if "BTC" in sym:
        return {"max_adx": 40, "sl_mult": 1.2, "tp_mult": 3.5}
    elif "XAG" in sym:
        return {"max_adx": 35, "sl_mult": 1.0, "tp_mult": 3.0}
    # Gold default — V2: wider SL/TP for spread survival
    return {"max_adx": 40, "sl_mult": 1.5, "tp_mult": 4.0}


def signal_counter_trend(df: pd.DataFrame, context: dict,
                          current_bar: int = -1) -> dict:
    """
    Counter Trend: RSI + BB extreme reversal + Volume exhaustion confirmation.
    Returns signal dict or None.
    """
    if len(df) < 60:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    params = _get_asset_params(symbol)
    latest = df.iloc[-1]

    rsi = float(latest.get('rsi', 50))
    adx_val = float(latest.get('adx', 0))
    bb_upper = float(latest.get('bb_upper', 0))
    bb_lower = float(latest.get('bb_lower', 0))
    atr = float(latest.get('atr', 0))
    close = float(latest['close'])

    if atr <= 0 or np.isnan(atr):
        return None

    if np.isnan(adx_val):
        adx_val = 0
    if np.isnan(rsi):
        return None

    # ─── Volume Features ─────────────────────────
    vol_ratio = float(latest.get('vol_ratio', 0.0))
    vol_spike = bool(latest.get('vol_spike', False))
    vol_dryup = bool(latest.get('vol_dryup', False))

    # Counter-trend REQUIRES volume to confirm exhaustion
    # Dry volume = no conviction → skip
    if vol_dryup:
        return None

    # ─── ADX Range Filter — NO counter-trend in strong trends ────
    if adx_val > params['max_adx']:
        return None

    # ─── BUY — Extreme Oversold ───────────────────
    if rsi < RSI_OVERSOLD and close < bb_lower:
        # Cooldown check
        if current_bar >= 0 and (current_bar - _last_signal_bar["BUY"]) < COOLDOWN_BARS:
            return None

        sl_dist = params['sl_mult'] * atr
        tp_dist = params['tp_mult'] * atr

        reasons = [
            f"RSI Oversold ({rsi:.1f}<{RSI_OVERSOLD})",
            f"Below BB Lower",
            f"ADX rangebound ({adx_val:.0f})",
        ]

        # Volume exhaustion confirmation
        if vol_spike:
            reasons.append("🔊 Vol Exhaustion Spike")
        if vol_ratio >= 1.5:
            reasons.append(f"Vol Elevated ({vol_ratio:.1f}x)")

        if current_bar >= 0:
            _last_signal_bar["BUY"] = current_bar

        return {
            "symbol": symbol, "side": "BUY", "entry_type": "MARKET",
            "entry_price": close,
            "sl": close - sl_dist,
            "tp1": close + tp_dist * 0.4,
            "tp2": close + tp_dist * 0.7,
            "tp3": close + tp_dist,
            "rationale": reasons,
            "confidence": MIN_CONFIDENCE,
            "model": "COUNTER_TREND",
        }

    # ─── SELL — Extreme Overbought ───────────────
    if rsi > RSI_OVERBOUGHT and close > bb_upper:
        if current_bar >= 0 and (current_bar - _last_signal_bar["SELL"]) < COOLDOWN_BARS:
            return None

        sl_dist = params['sl_mult'] * atr
        tp_dist = params['tp_mult'] * atr

        reasons = [
            f"RSI Overbought ({rsi:.1f}>{RSI_OVERBOUGHT})",
            f"Above BB Upper",
            f"ADX rangebound ({adx_val:.0f})",
        ]

        # Volume exhaustion confirmation
        if vol_spike:
            reasons.append("🔊 Vol Exhaustion Spike")
        if vol_ratio >= 1.5:
            reasons.append(f"Vol Elevated ({vol_ratio:.1f}x)")

        if current_bar >= 0:
            _last_signal_bar["SELL"] = current_bar

        return {
            "symbol": symbol, "side": "SELL", "entry_type": "MARKET",
            "entry_price": close,
            "sl": close + sl_dist,
            "tp1": close - tp_dist * 0.4,
            "tp2": close - tp_dist * 0.7,
            "tp3": close - tp_dist,
            "rationale": reasons,
            "confidence": MIN_CONFIDENCE,
            "model": "COUNTER_TREND",
        }

    return None
