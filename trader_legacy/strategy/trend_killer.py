# -*- coding: utf-8 -*-
import pandas as pd
from trader.features.candle_patterns import get_pattern_signal

TP1_R = 0.8
TP2_R = 2.0
TP3_R = 3.0


def signal_trend_killer(df: pd.DataFrame, context: dict) -> dict:
    """
    Smart Money Trend Killer:
    Trigger 1: Structure (HL/LH) + Displacement (proven)
    Trigger 2: Candlestick patterns in trend direction
    Both triggers fire only in trend regimes.
    """
    latest = df.iloc[-1]
    regime = context.get("regime_result", {})
    regime_name = regime.get("regime", "")

    atr = latest.get("atr", df["high"].iloc[-1] - df["low"].iloc[-1])
    if atr <= 0:
        return None

    if "Trend (Up)" in regime_name:
        if latest.get("structure") == "HL" or latest.get("displacement_up", False):
            return _build("BUY", latest, atr, context, ["Structure HL / Displacement Up"], regime)

        pat = get_pattern_signal(latest)
        if pat["direction"] == "BUY" and pat["strength"] >= 0.3:
            return _build("BUY", latest, atr, context, ["Candle: " + " + ".join(pat["patterns"])], regime)

    if "Trend (Down)" in regime_name:
        if latest.get("structure") == "LH" or latest.get("displacement_down", False):
            return _build("SELL", latest, atr, context, ["Structure LH / Displacement Down"], regime)

        pat = get_pattern_signal(latest)
        if pat["direction"] == "SELL" and pat["strength"] >= 0.3:
            return _build("SELL", latest, atr, context, ["Candle: " + " + ".join(pat["patterns"])], regime)

    return None


def _build(side, latest, atr, context, rationale, regime):
    entry = latest["close"]
    sl_mult = 1.2
    conf = float(regime.get("confidence", 0.5))
    if side == "BUY":
        sl = entry - (atr * sl_mult)
        risk = entry - sl
        tp1, tp2, tp3 = entry + risk * TP1_R, entry + risk * TP2_R, entry + risk * TP3_R
    else:
        sl = entry + (atr * sl_mult)
        risk = sl - entry
        tp1, tp2, tp3 = entry - risk * TP1_R, entry - risk * TP2_R, entry - risk * TP3_R
    return {
        "symbol": context.get("symbol", "UNKNOWN"),
        "side": side,
        "entry_type": "MARKET",
        "entry_price": float(entry),
        "sl": float(sl),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3),
        "rationale": rationale,
        "confidence": conf,
        "model": "TREND",
    }
