# -*- coding: utf-8 -*-
"""
Smart Money Trend Killer V2 — Confluence-Based
ต้องมีอย่างน้อย 2 factors ยืนยัน ถึงจะเปิดเทรด:
1. Structure (HL/LH) หรือ Displacement
2. EMA trend alignment (EMA21 > EMA50 for BUY, < for SELL)
3. Volume confirmation (vol_ratio >= 1.0)

Candle pattern เดียวไม่ trigger เทรดอีกต่อไป
ต้อง combine กับ structure/displacement + EMA alignment
"""
import json
import pandas as pd
from backend.trader.config.paths import SETTINGS_PATH
from backend.trader.features.candle_patterns import get_pattern_signal

# Load strategy params from config
with open(SETTINGS_PATH, encoding="utf-8") as _f:
    _STRATEGY_CFG = json.load(_f).get("strategy", {})

SL_ATR_MULT = _STRATEGY_CFG.get("sl_atr_multiplier", 2.5)   # V2: Wider to survive spread
TP1_MULT = _STRATEGY_CFG.get("tp1_risk_multiplier", 2.0)    # V2: Better R:R
TP2_MULT = _STRATEGY_CFG.get("tp2_risk_multiplier", 3.0)
TP3_MULT = _STRATEGY_CFG.get("tp3_risk_multiplier", 4.5)
MIN_CONFIDENCE = _STRATEGY_CFG.get("min_confidence_trade", 0.6)


def signal_trend_killer(df: pd.DataFrame, context: dict) -> dict:
    """
    Smart Money Trend Killer V2:
    - ONLY fires in Trend regimes
    - Requires EMA alignment as mandatory confluence
    - Structure (HL/LH) or Displacement as primary trigger
    - Candle patterns enhance confidence but NEVER trade alone
    """
    latest = df.iloc[-1]
    regime = context.get('regime_result', {})
    regime_name = regime.get('regime', '')

    atr = latest.get('atr', df['high'].iloc[-1] - df['low'].iloc[-1])
    if atr <= 0:
        return None

    # ─── Confluence checks ────────────────────────────
    ema_bullish = bool(latest.get('ema_bullish', False))
    vol_ratio = float(latest.get('vol_ratio', 0.0))
    has_vol = vol_ratio >= 1.0  # Volume at or above average

    # ─── BUY — Trend Up only + EMA bullish confirmation ──────
    if "Trend (Up)" in regime_name and ema_bullish:
        confluence_count = 0
        reasons = []

        # Factor 1: Structure HL or Displacement Up
        has_structure = latest.get('structure') == 'HL'
        has_displacement = bool(latest.get('displacement_up', False))
        if has_structure or has_displacement:
            confluence_count += 1
            reasons.append("Structure HL" if has_structure else "Displacement Up")

        # Factor 2: EMA alignment (already confirmed above = +1)
        confluence_count += 1
        reasons.append("EMA21 > EMA50")

        # Factor 3: Volume confirmation (bonus)
        if has_vol:
            confluence_count += 1
            reasons.append(f"Vol ratio {vol_ratio:.1f}")

        # Factor 4: Candle pattern (bonus — NEVER alone)
        pat = get_pattern_signal(latest)
        if pat['direction'] == 'BUY' and pat['strength'] >= 0.3:
            confluence_count += 1
            reasons.append("Candle: " + " + ".join(pat['patterns']))

        # Need at least 3 confluence factors (V2: relaxed from 4 for higher frequency)
        if confluence_count >= 3 and (has_structure or has_displacement):
            confidence = min(1.0, confluence_count * 0.2 + float(regime.get('confidence', 0.5)) * 0.3)
            if confidence >= MIN_CONFIDENCE:
                return _build("BUY", latest, atr, context, reasons, confidence)

    # ─── SELL — Trend Down only + EMA bearish confirmation ────
    if "Trend (Down)" in regime_name and not ema_bullish:
        confluence_count = 0
        reasons = []

        has_structure = latest.get('structure') == 'LH'
        has_displacement = bool(latest.get('displacement_down', False))
        if has_structure or has_displacement:
            confluence_count += 1
            reasons.append("Structure LH" if has_structure else "Displacement Down")

        confluence_count += 1
        reasons.append("EMA21 < EMA50")

        if has_vol:
            confluence_count += 1
            reasons.append(f"Vol ratio {vol_ratio:.1f}")

        pat = get_pattern_signal(latest)
        if pat['direction'] == 'SELL' and pat['strength'] >= 0.3:
            confluence_count += 1
            reasons.append("Candle: " + " + ".join(pat['patterns']))

        if confluence_count >= 3 and (has_structure or has_displacement):
            confidence = min(1.0, confluence_count * 0.2 + float(regime.get('confidence', 0.5)) * 0.3)
            if confidence >= MIN_CONFIDENCE:
                return _build("SELL", latest, atr, context, reasons, confidence)

    return None


def _build(side: str, latest, atr: float, context: dict,
           rationale: list, confidence: float) -> dict:
    entry = latest['close']
    sl_mult = context.get('sl_atr_multiplier', SL_ATR_MULT)
    if side == "BUY":
        sl = entry - (atr * sl_mult)
        risk = entry - sl
        tp1 = entry + risk * TP1_MULT
        tp2 = entry + risk * TP2_MULT
        tp3 = entry + risk * TP3_MULT
    else:
        sl = entry + (atr * SL_ATR_MULT)
        risk = sl - entry
        tp1 = entry - risk * TP1_MULT
        tp2 = entry - risk * TP2_MULT
        tp3 = entry - risk * TP3_MULT
    return {
        "symbol": context.get('symbol', 'UNKNOWN'),
        "side": side, "entry_type": "MARKET",
        "entry_price": float(entry), "sl": float(sl),
        "tp1": float(tp1), "tp2": float(tp2), "tp3": float(tp3),
        "rationale": rationale, "confidence": confidence, "model": "TREND"
    }
