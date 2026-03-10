# -*- coding: utf-8 -*-
"""
Momentum Scalper Strategy (AI-Optimized)
========================================
GOAL: Trade short, sharp, fast profit with LOW RISK.
Follows buying/selling MOMENTUM (Force + Volume + ROC).

PHILOSOPHY:
    - Follow the FORCE: Volume Delta + Force Index confirm real pressure.
    - Quick in, quick out: TP at 0.5R - 1.0R.
    - Tight ATR-based SL: 0.3x - 0.5x ATR.
    - EMA alignment filter: Only trade WITH the trend.
    - Consecutive bar momentum: 2+ bars confirm direction.

SIGNALS:
    BUY:  vol_delta > 0 + force_ema > 0 + roc > 0 + close > ema_fast + consec_bull >= 2
    SELL: vol_delta < 0 + force_ema < 0 + roc < 0 + close < ema_fast + consec_bear >= 2
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("momentum_scalper")

# Default configs - will be overridden by optimizer
DEFAULT_PARAMS = {
    "sl_atr_mult": 0.6,          # SL = 0.6x ATR (room to breathe against BTC spread)
    "tp_rr": 1.5,                # TP at 1.5R (overcomes spread cost)
    "min_roc": 0.10,             # Minimum ROC% to confirm real momentum
    "min_force_ratio": 0.0,      # Force EMA must be > 0 (positive momentum)
    "min_consec_bars": 3,        # At least 3 consecutive bars same direction (STRONG)
    "require_ema_align": True,   # Price must align with EMA fast
    "require_vol_delta": True,   # Volume Delta must confirm direction
    "require_obv_align": True,   # OBV must confirm direction
    "require_htf_align": True,   # MUST align with EMA 200 (HTF)
}

# Per-symbol overrides
SYMBOL_PARAMS = {
    "BTCUSD": {
        "sl_atr_mult": 1.2,         # Widened from 0.6 → 1.2 to survive BTC spread + noise
        "tp_rr": 2.0,               # Raised from 1.5 → 2.0 to compensate wider SL
        "min_roc": 0.10,
        "min_consec_bars": 3,
    },
    "XAUUSD": {
        "sl_atr_mult": 0.7,
        "tp_rr": 1.5,
        "min_roc": 0.05,
        "min_consec_bars": 3,
    },
}


def _get_params(symbol: str, overrides: dict = None) -> dict:
    """Get parameters for a symbol, with optional overrides."""
    params = DEFAULT_PARAMS.copy()
    sym = symbol.upper()
    if "BTC" in sym:
        params.update(SYMBOL_PARAMS.get("BTCUSD", {}))
    elif "XAU" in sym:
        params.update(SYMBOL_PARAMS.get("XAUUSD", {}))
    if overrides:
        params.update(overrides)
    return params


def signal_momentum_scalper(df: pd.DataFrame, context: dict, params_override: dict = None) -> dict:
    """
    Momentum Scalper: Trades based on buying/selling pressure.
    Requires: vol_delta, force_ema, roc_5, ema_fast, consec_bull/bear, obv_bullish
    """
    if len(df) < 50:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    params = _get_params(symbol, params_override)

    latest = df.iloc[-1]
    close = float(latest['close'])
    atr = float(latest.get('atr', close * 0.005))
    if atr <= 0 or np.isnan(atr):
        atr = close * 0.005

    # ─── Extract momentum features ─────────────────────
    vol_delta = float(latest.get('vol_delta', 0))
    force_ema = float(latest.get('force_ema', 0))
    roc = float(latest.get('roc_5', 0))
    ema_fast = float(latest.get('ema_fast', close))
    ema_200 = float(latest.get('ema_200', close))
    consec_bull = int(latest.get('consec_bull', 0))
    consec_bear = int(latest.get('consec_bear', 0))
    obv_bullish = bool(latest.get('obv_bullish', False))

    # ─── BULLISH MOMENTUM CHECK ────────────────────────
    bull_score = 0
    bull_reasons = []

    if roc > params['min_roc']:
        bull_score += 1
        bull_reasons.append(f"⚡ROC +{roc:.2f}%")

    if params['require_vol_delta'] and vol_delta > 0:
        bull_score += 1
        bull_reasons.append("📈 Vol Delta +")
    elif not params['require_vol_delta']:
        bull_score += 1

    if force_ema > params['min_force_ratio']:
        bull_score += 1
        bull_reasons.append("💪 Force +")

    if params['require_ema_align'] and close > ema_fast:
        bull_score += 1
        bull_reasons.append("📊 EMA Aligned")
    elif not params['require_ema_align']:
        bull_score += 1

    if consec_bull >= params['min_consec_bars']:
        bull_score += 1
        bull_reasons.append(f"🟢 {consec_bull} bars ↑")

    if params['require_obv_align'] and obv_bullish:
        bull_score += 1
        bull_reasons.append("📊 OBV +")
    elif not params['require_obv_align']:
        bull_score += 1

    # HTF trend bonus
    htf_aligned = close > ema_200
    if htf_aligned:
        bull_score += 1
        bull_reasons.append("🔝 HTF ▲")

    # ─── BEARISH MOMENTUM CHECK ────────────────────────
    bear_score = 0
    bear_reasons = []

    if roc < -params['min_roc']:
        bear_score += 1
        bear_reasons.append(f"⚡ROC {roc:.2f}%")

    if params['require_vol_delta'] and vol_delta < 0:
        bear_score += 1
        bear_reasons.append("📉 Vol Delta -")
    elif not params['require_vol_delta']:
        bear_score += 1

    if force_ema < -params['min_force_ratio']:
        bear_score += 1
        bear_reasons.append("💪 Force -")

    if params['require_ema_align'] and close < ema_fast:
        bear_score += 1
        bear_reasons.append("📊 EMA Aligned")
    elif not params['require_ema_align']:
        bear_score += 1

    if consec_bear >= params['min_consec_bars']:
        bear_score += 1
        bear_reasons.append(f"🔴 {consec_bear} bars ↓")

    if params['require_obv_align'] and not obv_bullish:
        bear_score += 1
        bear_reasons.append("📊 OBV -")
    elif not params['require_obv_align']:
        bear_score += 1

    htf_bear = close < ema_200
    if htf_bear:
        bear_score += 1
        bear_reasons.append("🔽 HTF ▼")

    # ─── DECISION ──────────────────────────────────────
    min_score = 6  # Need at least 6/7 confluence points (STRICT)

    # Regime filter: ONLY trade during trending regimes
    regime_name = context.get('regime_result', {}).get('regime', '').lower()
    if 'trend' not in regime_name:
        return None  # Block everything except Strong/Weak Trend

    # HTF alignment mandatory check
    if params.get('require_htf_align', True):
        if bull_score >= min_score and not (close > ema_200):
            return None  # Don't buy against HTF
        if bear_score >= min_score and not (close < ema_200):
            return None  # Don't sell against HTF

    if bull_score >= min_score and bull_score > bear_score:
        # PULLBACK LOGIC: Wait for price to pull back to EMA Fast
        # If price is already at/below EMA Fast, enter at Market
        is_pullback_needed = close > ema_fast * 1.0005 # > 0.05% away
        entry_price = round(ema_fast, 2) if is_pullback_needed else close
        entry_type = "LIMIT" if is_pullback_needed else "MARKET"
        
        sl = entry_price - (atr * params['sl_atr_mult'])
        risk = entry_price - sl
        if risk <= 0:
            return None
        tp1 = entry_price + (risk * params['tp_rr'])
        conf = 0.55 + (bull_score / 14.0)  # Scale 0.55 to ~0.85
        if htf_aligned:
            conf += 0.05

        if is_pullback_needed:
            bull_reasons.append(f"⏱️ Waiting for Pullback to {entry_price:.2f} (EMA Fast)")

        return {
            "symbol": symbol, "side": "BUY", "entry_type": entry_type,
            "entry_price": entry_price, "sl": round(sl, 5),
            "tp1": round(tp1, 5),
            "tp2": round(entry_price + risk * 1.2, 5),
            "tp3": round(entry_price + risk * 1.8, 5),
            "rationale": bull_reasons,
            "confidence": min(0.95, conf),
            "model": "MOMENTUM_SCALPER",
        }

    if bear_score >= min_score and bear_score > bull_score:
        # PULLBACK LOGIC: Wait for price to pull back to EMA Fast
        is_pullback_needed = close < ema_fast * 0.9995 # < 0.05% away
        entry_price = round(ema_fast, 2) if is_pullback_needed else close
        entry_type = "LIMIT" if is_pullback_needed else "MARKET"

        sl = entry_price + (atr * params['sl_atr_mult'])
        risk = sl - entry_price
        if risk <= 0:
            return None
        tp1 = entry_price - (risk * params['tp_rr'])
        conf = 0.55 + (bear_score / 14.0)
        if htf_bear:
            conf += 0.05

        if is_pullback_needed:
            bear_reasons.append(f"⏱️ Waiting for Pullback to {entry_price:.2f} (EMA Fast)")

        return {
            "symbol": symbol, "side": "SELL", "entry_type": entry_type,
            "entry_price": entry_price, "sl": round(sl, 5),
            "tp1": round(tp1, 5),
            "tp2": round(entry_price - risk * 1.2, 5),
            "tp3": round(entry_price - risk * 1.8, 5),
            "rationale": bear_reasons,
            "confidence": min(0.95, conf),
            "model": "MOMENTUM_SCALPER",
        }

    return None
