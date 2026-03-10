# -*- coding: utf-8 -*-
"""
Momentum Scalper V2 — Bull/Bear Trend Filter Edition
=====================================================
V1 ใช้ EMA 200 + regime filter อย่างเดียว → ยังเข้าเทรด counter-trend ได้
V2 เพิ่ม **3-Layer Bull/Bear Indicator** ที่ต้อง vote ≥2/3 ถึงจะเทรด

BULL/BEAR INDICATOR (3-Layer Voting):
    Layer 1: EMA 200 — close > EMA 200 = BULL vote
    Layer 2: Supertrend — is_uptrend = BULL vote
    Layer 3: ADX + DI — ADX > 20 + plus_di > minus_di = BULL vote

    Classification:
        vote_bull >= 2 → BULL (BUY only)
        vote_bear >= 2 → BEAR (SELL only)
        else → NEUTRAL (NO TRADE — transition zone)

CHANGES FROM V1:
    - HARD directional filter: BUY iff BULL, SELL iff BEAR
    - NEUTRAL zone → zero trades (avoids chop/transition)
    - Confidence bonus +0.05 when all 3 layers aligned (3/3)
    - All other momentum logic (ROC, Force, OBV, Vol Delta, consec bars) unchanged
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("momentum_scalper_v2")

# Default configs - same as V1
DEFAULT_PARAMS = {
    "sl_atr_mult": 0.9,
    "tp_rr": 1.5,
    "min_roc": 0.10,
    "min_force_ratio": 0.0,
    "min_consec_bars": 3,
    "require_ema_align": True,
    "require_vol_delta": True,
    "require_obv_align": True,
    # V2: Bull/Bear filter params
    "adx_threshold": 20,       # ADX must be > this for DI vote to count
    # Entry quality filters (anti-chop / anti-stop-hunt)
    "min_adx_entry": 22,
    "min_di_gap": 3.0,
    "min_body_ratio": 0.25,
    "min_vol_ratio": 1.0,
    "min_ema_slope_atr": 0.015,
    "min_sl_atr": 1.0,
}

SYMBOL_PARAMS = {
    "BTCUSD": {
        "sl_atr_mult": 1.2,
        "tp_rr": 1.6,
        "min_roc": 0.12,
        "min_consec_bars": 3,
        "adx_threshold": 22,
        "min_adx_entry": 24,
        "min_di_gap": 4.0,
        "min_body_ratio": 0.30,
        "min_vol_ratio": 1.05,
        "min_ema_slope_atr": 0.02,
        "min_sl_atr": 1.5,
    },
    "XAUUSD": {
        "sl_atr_mult": 0.9,
        "tp_rr": 1.5,
        "min_roc": 0.06,
        "min_consec_bars": 3,
        "min_adx_entry": 21,
        "min_di_gap": 2.5,
        "min_sl_atr": 1.1,
    },
    "USOIL": {
        "sl_atr_mult": 1.0,
        "tp_rr": 1.8,
        "min_roc": 0.08,
        "min_consec_bars": 3,
        "min_adx_entry": 25,
        "min_di_gap": 4.0,
        "min_sl_atr": 1.2,
        "require_full_alignment": True,  # 3/3 vote required
        "min_score": 7,                  # Out of 7 for maximum confluence
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
    elif "USOIL" in sym:
        params.update(SYMBOL_PARAMS.get("USOIL", {}))
    if overrides:
        params.update(overrides)
    return params


def classify_bull_bear(latest: pd.Series, close: float, params: dict) -> tuple:
    """
    3-Layer Bull/Bear Indicator.

    Returns:
        (market_state, vote_bull, vote_bear, reasons)
        market_state: "BULL" | "BEAR" | "NEUTRAL"
    """
    vote_bull = 0
    vote_bear = 0
    reasons = []

    # ─── Layer 1: EMA 200 ─────────────────────────────
    ema_200 = float(latest.get('ema_200', close))
    if close > ema_200:
        vote_bull += 1
        reasons.append("🔵 EMA200 ▲")
    else:
        vote_bear += 1
        reasons.append("🔴 EMA200 ▼")

    # ─── Layer 2: Supertrend ──────────────────────────
    is_uptrend = bool(latest.get('is_uptrend', False))
    is_downtrend = bool(latest.get('is_downtrend', False))
    if is_uptrend:
        vote_bull += 1
        reasons.append("🔵 ST ▲")
    elif is_downtrend:
        vote_bear += 1
        reasons.append("🔴 ST ▼")

    # ─── Layer 3: ADX + DI Direction ──────────────────
    adx = float(latest.get('adx', 0))
    plus_di = float(latest.get('plus_di', 0))
    minus_di = float(latest.get('minus_di', 0))
    adx_thresh = params.get('adx_threshold', 20)

    if adx > adx_thresh:
        if plus_di > minus_di:
            vote_bull += 1
            reasons.append(f"🔵 ADX {adx:.0f} DI+")
        elif minus_di > plus_di:
            vote_bear += 1
            reasons.append(f"🔴 ADX {adx:.0f} DI-")
        # else equal → no vote
    else:
        reasons.append(f"⚪ ADX {adx:.0f} (weak)")

    # ─── Classification ──────────────────────────────
    if vote_bull >= 2:
        market_state = "BULL"
    elif vote_bear >= 2:
        market_state = "BEAR"
    else:
        market_state = "NEUTRAL"

    return market_state, vote_bull, vote_bear, reasons


def signal_momentum_scalper_v2(df: pd.DataFrame, context: dict, params_override: dict = None) -> dict:
    """
    Momentum Scalper V2: Same momentum logic as V1 + Bull/Bear Indicator filter.
    - BULL market → BUY signals only
    - BEAR market → SELL signals only
    - NEUTRAL → NO TRADE (transition zone protection)
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

    # ═══════════════════════════════════════════════════
    # V2 CORE: 3-Layer Bull/Bear Indicator
    # ═══════════════════════════════════════════════════
    market_state, vote_bull, vote_bear, bb_reasons = classify_bull_bear(latest, close, params)

    # NEUTRAL zone → NO TRADE (transition protection)
    if market_state == "NEUTRAL":
        return None

    # Regime filter (same as V1): ONLY trade during trending regimes
    regime_name = context.get('regime_result', {}).get('regime', '').lower()
    if 'trend' not in regime_name:
        return None

    # ─── Extract momentum features (same as V1) ──────
    vol_delta = float(latest.get('vol_delta', 0))
    force_ema = float(latest.get('force_ema', 0))
    roc = float(latest.get('roc_5', 0))
    ema_fast = float(latest.get('ema_fast', close))
    ema_fast_prev = float(df.iloc[-2].get('ema_fast', ema_fast))
    consec_bull = int(latest.get('consec_bull', 0))
    consec_bear = int(latest.get('consec_bear', 0))
    obv_bullish = bool(latest.get('obv_bullish', False))
    adx = float(latest.get('adx', 0))
    plus_di = float(latest.get('plus_di', 0))
    minus_di = float(latest.get('minus_di', 0))
    body_ratio = float(latest.get('body_ratio', 0))
    vol_ratio = float(latest.get('vol_ratio', 1.0))

    # 3/3 aligned bonus
    full_alignment = (vote_bull == 3) or (vote_bear == 3)
    min_adx_entry = float(params.get("min_adx_entry", 22))
    min_body_ratio = float(params.get("min_body_ratio", 0.25))
    min_vol_ratio = float(params.get("min_vol_ratio", 1.0))
    min_ema_slope_atr = float(params.get("min_ema_slope_atr", 0.015))

    if adx < min_adx_entry:
        return None
    if body_ratio < min_body_ratio:
        return None
    if vol_ratio < min_vol_ratio:
        return None

    # ─── BULLISH MOMENTUM CHECK (only if BULL market) ──
    if market_state == "BULL":
        bull_score = 0
        bull_reasons = list(bb_reasons)  # start with indicator state
        di_gap = plus_di - minus_di
        min_di_gap = float(params.get("min_di_gap", 3.0))
        if di_gap < min_di_gap:
            return None
        ema_slope = ema_fast - ema_fast_prev
        if ema_slope < (atr * min_ema_slope_atr):
            return None

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

        min_score = 6  # Require stronger confluence to avoid low-quality entries
        if bull_score < min_score:
            return None

        # PULLBACK LOGIC
        is_pullback_needed = close > ema_fast * 1.0005
        entry_price = round(ema_fast, 2) if is_pullback_needed else close
        entry_type = "LIMIT" if is_pullback_needed else "MARKET"

        sl_dist = max(atr * params['sl_atr_mult'], atr * float(params.get("min_sl_atr", 1.0)))
        sl = entry_price - sl_dist
        risk = sl_dist
        if risk <= 0:
            return None
        tp1 = entry_price + (risk * params['tp_rr'])
        conf = 0.55 + (bull_score / 12.0)
        if full_alignment:
            conf += 0.05  # V2 bonus: all 3 layers agree
            bull_reasons.append("✨ Full 3/3 Aligned")

        if is_pullback_needed:
            bull_reasons.append(f"⏱️ Pullback to {entry_price:.2f}")

        tv = int(latest.get('tick_volume', 0))
        bull_reasons.append(f"📊 Vol:{tv} (x{vol_ratio:.1f})")

        return {
            "symbol": symbol, "side": "BUY", "entry_type": entry_type,
            "entry_price": entry_price, "sl": round(sl, 5),
            "tp1": round(tp1, 5),
            "tp2": round(entry_price + risk * 1.2, 5),
            "tp3": round(entry_price + risk * 1.8, 5),
            "rationale": bull_reasons,
            "confidence": min(0.95, conf),
            "model": "MOMENTUM_SCALPER_V2",
        }

    # ─── BEARISH MOMENTUM CHECK (only if BEAR market) ──
    if market_state == "BEAR":
        bear_score = 0
        bear_reasons = list(bb_reasons)
        di_gap = minus_di - plus_di
        min_di_gap = float(params.get("min_di_gap", 3.0))
        if di_gap < min_di_gap:
            return None
        ema_slope = ema_fast_prev - ema_fast
        if ema_slope < (atr * min_ema_slope_atr):
            return None

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

        min_score = 6
        if bear_score < min_score:
            return None

        # PULLBACK LOGIC
        is_pullback_needed = close < ema_fast * 0.9995
        entry_price = round(ema_fast, 2) if is_pullback_needed else close
        entry_type = "LIMIT" if is_pullback_needed else "MARKET"

        sl_dist = max(atr * params['sl_atr_mult'], atr * float(params.get("min_sl_atr", 1.0)))
        sl = entry_price + sl_dist
        risk = sl_dist
        if risk <= 0:
            return None

        tp1 = entry_price - (risk * params['tp_rr'])
        conf = 0.55 + (bear_score / 12.0)
        if full_alignment:
            conf += 0.05
            bear_reasons.append("✨ Full 3/3 Aligned")

        if is_pullback_needed:
            bear_reasons.append(f"⏱️ Pullback to {entry_price:.2f}")

        tv = int(latest.get('tick_volume', 0))
        vol_ratio = float(latest.get('vol_ratio', 1.0))
        bear_reasons.append(f"📊 Vol:{tv} (x{vol_ratio:.1f})")

        return {
            "symbol": symbol, "side": "SELL", "entry_type": entry_type,
            "entry_price": entry_price, "sl": round(sl, 5),
            "tp1": round(tp1, 5),
            "tp2": round(entry_price - risk * 1.2, 5),
            "tp3": round(entry_price - risk * 1.8, 5),
            "rationale": bear_reasons,
            "confidence": min(0.95, conf),
            "model": "MOMENTUM_SCALPER_V2",
        }

    return None
