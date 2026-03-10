# -*- coding: utf-8 -*-
"""
Momentum Rider V2 — Impulse-Driven Force Trading
เทรดตามแรงกราฟ เหมือนรู้ว่าจะวิ่งไปทางไหน

PHILOSOPHY:
    ราคาที่วิ่งด้วย "impulse" จริง (หลายแท่งเทียนวิ่งทิศเดียว + body ใหญ่ + volume สูง)
    มักจะวิ่งต่อ — เพราะ Smart Money กำลังดันราคา

V2 CHANGES (Spread-Aware Design):
    - ต้องมี NET DISPLACEMENT ≥ 1.5× ATR ใน 5 bars ล่าสุด (ขั้นต่ำ)
    - ยืนยันด้วย Force Index, ROC, Volume
    - SL ที่ Swing Low/High ล่าสุด (structure-based, ไม่ใช่ fixed ATR)
    - TP ที่ momentum target (1.5×, 2.5×, 4× ของ impulse distance)

ASSET-SPECIFIC:
    XAU: impulse_min=1.5×ATR, smooth entry
    XAG: impulse_min=2.0×ATR, wider due to whipsaw
    BTC: impulse_min=1.5×ATR, wide TP
"""
import json
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("momentum_rider")

with open("d:/VibeCode/Trade/backend/trader/config/settings.json") as _f:
    _MR_CFG = json.load(_f).get("strategy", {}).get("momentum_rider", {})

MIN_MOM_CONFIDENCE = _MR_CFG.get("min_confidence", 65)

# ─── Asset-Specific Parameters ─────────────────────────────────
CONFIGS = {
    "DEFAULT": {
        "impulse_atr_min": 1.5,     # Min net displacement in ATR units (5-bar)
        "body_dom_min": 0.40,       # Min avg body ratio
        "vol_ratio_min": 0.9,       # Volume at or above average
        "consec_min": 2,            # Min consecutive direction bars
        "sl_swing_lookback": 10,    # Bars to look back for swing SL
        "sl_atr_max": 2.0,          # Max ATR distance for fixed SL fallback
        "sl_mult": 2.0,             # SL = ATR × 2.0 (survive spread + noise)
        "tp1_rr": 1.0,              # TP1 = 1.0R (Micro-scalp mode)
        "tp2_mult": 2.0,            # TP2 = ATR × 2
        "tp3_mult": 3.0,            # TP3 = ATR × 3 (runners)
    },
    "XAU": {  # Gold moves smoothly — catch early, survive real spread (396pts)
        "impulse_atr_min": 2.5,     # V3: Only trade truly strong impulse moves
        "body_dom_min": 0.40,
        "vol_ratio_min": 0.9,
        "consec_min": 2,
        "sl_swing_lookback": 10,
        "sl_atr_max": 2.5,          # Wider to survive 396-pt spread
        "tp1_rr": 1.0,              # Micro-scalp
        "tp2_mult": 1.5,
        "tp3_mult": 2.0,
    },
    "XAG": {
        "impulse_atr_min": 2.0,
        "body_dom_min": 0.40,
        "vol_ratio_min": 0.9,
        "consec_min": 2,
        "sl_swing_lookback": 12,
        "sl_atr_max": 3.0,
        "tp1_rr": 1.0,
        "tp2_mult": 1.5,
        "tp3_mult": 2.0,
    },
    "BTC": {
        "impulse_atr_min": 1.5,
        "body_dom_min": 0.35,
        "vol_ratio_min": 0.8,
        "consec_min": 2,
        "sl_swing_lookback": 10,
        "sl_atr_max": 3.0,
        "tp1_rr": 1.0,
        "tp2_mult": 1.5,
        "tp3_mult": 2.0,
    },
}


def _get_cfg(symbol: str) -> dict:
    sym = symbol.upper()
    if "XAU" in sym:
        return CONFIGS["XAU"]
    elif "XAG" in sym:
        return CONFIGS["XAG"]
    elif "BTC" in sym:
        return CONFIGS["BTC"]
    return CONFIGS["DEFAULT"]


def signal_momentum_rider(df: pd.DataFrame, context: dict) -> dict:
    """
    Momentum Rider V2: Impulse-driven force trading.

    Core logic: Detect when price has made a SIGNIFICANT net displacement
    in one direction over the last 5 bars. If the move is large (> 1.5× ATR),
    consecutive, with volume — ride the continuation.

    Key difference from other strategies:
    - No EMA/RSI/MACD required (those are lagging)
    - Uses raw price displacement + body acceleration + force index
    - Structure-based SL (swing low/high, not fixed ATR)
    """
    if len(df) < 50:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    cfg = _get_cfg(symbol)

    # ─── Extract data ──────────────────────────────────────
    latest = df.iloc[-1]
    close = float(latest['close'])
    atr = float(latest.get('atr', 0))
    if atr <= 0 or np.isnan(atr):
        atr = close * 0.005

    # ─── CORE: Net Displacement (5-bar impulse) ────────────
    # How far has price moved NET in the last 5 bars?
    close_5ago = float(df.iloc[-6]['close']) if len(df) > 6 else close
    net_displacement = close - close_5ago
    displacement_atr = abs(net_displacement) / atr if atr > 0 else 0

    # Not enough impulse → skip
    if displacement_atr < cfg['impulse_atr_min']:
        return None

    # ─── Regime alignment check ────────────────────────
    # Don't trade momentum against the regime classifier
    regime = context.get('regime_result', {})
    regime_name = regime.get('regime', '').lower()
    if net_displacement > 0 and 'trend (down)' in regime_name:
        return None  # Don't BUY momentum against downtrend regime
    if net_displacement < 0 and 'trend (up)' in regime_name:
        return None  # Don't SELL momentum against uptrend regime

    # ─── Determine direction from displacement ─────────────
    is_bull_impulse = net_displacement > 0
    is_bear_impulse = net_displacement < 0

    # ─── Supertrend confirmation ───────────────────────────
    is_uptrend = bool(latest.get('is_uptrend', False))
    is_downtrend = bool(latest.get('is_downtrend', False))

    # ─── Momentum features ────────────────────────────────
    roc = float(latest.get('roc_5', 0))
    if np.isnan(roc):
        roc = 0
    force_idx = float(latest.get('force_index', 0))
    if np.isnan(force_idx):
        force_idx = 0
    force_ema = float(latest.get('force_ema', 0))
    if np.isnan(force_ema):
        force_ema = 0
    vol_ratio = float(latest.get('vol_ratio', 0))
    consec_bull = int(latest.get('consec_bull', 0))
    consec_bear = int(latest.get('consec_bear', 0))

    # ─── Body Analysis (last 3 bars) ─────────────────────
    bodies = []
    doms = []
    for i in range(-3, 0):
        bar = df.iloc[i]
        body = abs(float(bar['close']) - float(bar['open']))
        rng = float(bar['high']) - float(bar['low'])
        bodies.append(body)
        doms.append(body / rng if rng > 0 else 0)
    avg_body_dom = np.mean(doms)
    body_accel = (bodies[2] > bodies[1] > bodies[0] * 0.7)  # Growing bodies

    # ─── Swing-based SL ───────────────────────────────────
    lookback = cfg['sl_swing_lookback']
    recent_bars = df.iloc[-lookback-1:-1]

    # ═══════════════════════════════════════════════════════
    # BUY SIGNAL
    # ═══════════════════════════════════════════════════════
    if is_bull_impulse:
        confidence = 0
        reasons = []

        # Factor 1: Net impulse (MANDATORY — already filtered above)
        confidence += 20
        reasons.append(f"🚀 Impulse +{displacement_atr:.1f}×ATR")

        # Factor 2: Supertrend alignment (MANDATORY for V2)
        if is_uptrend:
            confidence += 15
            reasons.append("⬆️ Supertrend UP")
        else:
            return None  # Don't trade impulse against Supertrend

        # Factor 3: ROC positive
        if roc > 0:
            confidence += 10
            reasons.append(f"ROC +{roc:.3f}%")

        # Factor 4: Body analysis
        if body_accel:
            confidence += 15
            reasons.append("📈 Body Acceleration")
        if avg_body_dom >= cfg['body_dom_min']:
            confidence += 10
            reasons.append(f"💪 Dom {avg_body_dom:.0%}")

        # Factor 5: Volume
        if vol_ratio >= cfg['vol_ratio_min'] * 1.5:
            confidence += 15
            reasons.append(f"🔊 Vol Surge {vol_ratio:.1f}x")
        elif vol_ratio >= cfg['vol_ratio_min']:
            confidence += 8
            reasons.append(f"Vol {vol_ratio:.1f}x")

        # Factor 6: Force Index
        if force_idx > 0 and force_idx > force_ema:
            confidence += 10
            reasons.append("⚡ Force +")

        # Factor 7: Consecutive bars
        if consec_bull >= cfg['consec_min']:
            confidence += 5
            reasons.append(f"🟩 {consec_bull}x Bull")

        # ─── Confidence Gate ──────────────────────────────
        if confidence < MIN_MOM_CONFIDENCE:
            return None

        # ─── SL: Recent swing low (structure-based) ───────
        swing_low = float(recent_bars['low'].min())
        sl_structure = swing_low - atr * 0.3  # Small buffer below swing
        sl_fixed = close - atr * cfg['sl_atr_max']
        sl = max(sl_structure, sl_fixed)  # Use tighter of the two

        risk = close - sl
        if risk <= 0:
            return None

        # ─── TP: Based on impulse distance ────────────────
        impulse_dist = abs(net_displacement)
        tp1 = close + risk * cfg['tp1_rr']
        tp2 = close + impulse_dist * cfg['tp2_mult']
        tp3 = close + impulse_dist * cfg['tp3_mult']

        return {
            "symbol": symbol, "side": "BUY", "entry_type": "MARKET",
            "entry_price": close, "sl": round(sl, 5),
            "tp1": round(tp1, 5), "tp2": round(tp2, 5), "tp3": round(tp3, 5),
            "rationale": reasons,
            "confidence": min(1.0, confidence / 100.0),
            "model": "MOMENTUM_RIDER",
        }

    # ═══════════════════════════════════════════════════════
    # SELL SIGNAL
    # ═══════════════════════════════════════════════════════
    if is_bear_impulse:
        confidence = 0
        reasons = []

        # Factor 1: Net impulse (MANDATORY)
        confidence += 20
        reasons.append(f"🚀 Impulse {displacement_atr:.1f}×ATR ⬇️")

        # Factor 2: Supertrend alignment (MANDATORY for V2)
        if is_downtrend:
            confidence += 15
            reasons.append("⬇️ Supertrend DOWN")
        else:
            return None  # Don't trade impulse against Supertrend

        # Factor 3: ROC negative
        if roc < 0:
            confidence += 10
            reasons.append(f"ROC {roc:.3f}%")

        # Factor 4: Body analysis
        if body_accel:
            confidence += 15
            reasons.append("📉 Body Acceleration")
        if avg_body_dom >= cfg['body_dom_min']:
            confidence += 10
            reasons.append(f"💪 Dom {avg_body_dom:.0%}")

        # Factor 5: Volume
        if vol_ratio >= cfg['vol_ratio_min'] * 1.5:
            confidence += 15
            reasons.append(f"🔊 Vol Surge {vol_ratio:.1f}x")
        elif vol_ratio >= cfg['vol_ratio_min']:
            confidence += 8
            reasons.append(f"Vol {vol_ratio:.1f}x")

        # Factor 6: Force Index
        if force_idx < 0 and force_idx < force_ema:
            confidence += 10
            reasons.append("⚡ Force −")

        # Factor 7: Consecutive bars
        if consec_bear >= cfg['consec_min']:
            confidence += 5
            reasons.append(f"🟥 {consec_bear}x Bear")

        # ─── Confidence Gate ──────────────────────────────
        if confidence < MIN_MOM_CONFIDENCE:
            return None

        # ─── SL: Recent swing high (structure-based) ──────
        swing_high = float(recent_bars['high'].max())
        sl_structure = swing_high + atr * 0.3
        sl_fixed = close + atr * cfg['sl_atr_max']
        sl = min(sl_structure, sl_fixed)

        risk = sl - close
        if risk <= 0:
            return None

        # ─── TP: Based on impulse distance ────────────────
        impulse_dist = abs(net_displacement)
        tp1 = close - risk * cfg['tp1_rr']
        tp2 = close - impulse_dist * cfg['tp2_mult']
        tp3 = close - impulse_dist * cfg['tp3_mult']

        return {
            "symbol": symbol, "side": "SELL", "entry_type": "MARKET",
            "entry_price": close, "sl": round(sl, 5),
            "tp1": round(tp1, 5), "tp2": round(tp2, 5), "tp3": round(tp3, 5),
            "rationale": reasons,
            "confidence": min(1.0, confidence / 100.0),
            "model": "MOMENTUM_RIDER",
        }

    return None
