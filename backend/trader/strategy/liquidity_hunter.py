"""
Liquidity Hunter V4 - Conservative sweep confirmation to reduce stop-loss churn.
"""
import json
import pandas as pd

# Load strategy params from config
with open("d:/VibeCode/Trade/backend/trader/config/settings.json") as _f:
    _STRATEGY_CFG = json.load(_f).get("strategy", {})

SL_ATR_MULT = _STRATEGY_CFG.get("sl_atr_multiplier", 1.8)
TP1_MULT = _STRATEGY_CFG.get("tp1_risk_multiplier", 1.2)
TP2_MULT = _STRATEGY_CFG.get("tp2_risk_multiplier", 2.5)
TP3_MULT = _STRATEGY_CFG.get("tp3_risk_multiplier", 4.0)
MIN_CONFIDENCE = _STRATEGY_CFG.get("min_confidence_trade", 0.6)
MIN_LIQ_EVENT_SCORE = _STRATEGY_CFG.get("min_liquidity_event_score", 0.70)
HARD_MIN_LIQ_EVENT_SCORE = 0.85
MIN_SWEEP_VOL_RATIO = 1.20
MIN_BODY_RATIO = 0.18


def signal_liquidity_hunter(df: pd.DataFrame, events: list, context: dict) -> dict:
    """
    Conservative liquidity entry model.
    - Requires stronger score and volume context.
    - Skips dry-up / indecision bars.
    - Enforces momentum alignment to reduce failed reversals.
    """
    if not events:
        return None

    latest = df.iloc[-1]
    atr = float(latest.get("atr", df["high"].iloc[-1] - df["low"].iloc[-1]))
    if atr <= 0:
        return None

    if bool(latest.get("vol_dryup", False)):
        return None
    if float(latest.get("body_ratio", 1.0)) < MIN_BODY_RATIO:
        return None

    vol_ratio = float(latest.get("vol_ratio", 0.0))
    score_gate = max(float(MIN_LIQ_EVENT_SCORE), HARD_MIN_LIQ_EVENT_SCORE)

    valid_events = []
    for event in events:
        event_type = event.get("type")
        if event_type not in ["SWEEP", "DISPLACEMENT_CONFIRMED"]:
            continue

        event_score = float(event.get("score", 0.0))
        if event_score < score_gate:
            continue

        if event_type == "SWEEP":
            vol_ok = bool(event.get("vol_confirmed", False)) or vol_ratio >= MIN_SWEEP_VOL_RATIO
            if not vol_ok:
                continue

        valid_events.append(event)

    if not valid_events:
        return None

    latest_event = valid_events[-1]
    confidence = float(latest_event.get("score", 0.0))
    if confidence < MIN_CONFIDENCE:
        return None

    ema_bullish = bool(latest.get("ema_bullish", False))
    net_power = float(latest.get("net_power", 0.0))
    sl_pad_atr = max(0.6, float(SL_ATR_MULT))

    if latest_event.get("direction") == "LONG_SIGNAL":
        if not ema_bullish or net_power < 0:
            return None

        entry = float(latest["close"])
        sl = float(latest_event["invalidation_level"]) - (atr * sl_pad_atr)
        risk = entry - sl
        if risk <= 0:
            return None

        return {
            "symbol": context.get("symbol", "UNKNOWN"),
            "side": "BUY",
            "entry_type": "MARKET",
            "entry_price": entry,
            "sl": float(sl),
            "tp1": float(entry + (risk * TP1_MULT)),
            "tp2": float(entry + (risk * TP2_MULT)),
            "tp3": float(entry + (risk * TP3_MULT)),
            "rationale": [
                f"LiqHunterV4 sweep@{latest_event.get('key_levels', [None])[0]}, score={confidence:.2f}>={score_gate:.2f}, vol_ratio={vol_ratio:.2f}"
            ],
            "confidence": confidence,
            "model": "LIQUIDITY",
        }

    if latest_event.get("direction") == "SHORT_SIGNAL":
        if ema_bullish or net_power > 0:
            return None

        entry = float(latest["close"])
        sl = float(latest_event["invalidation_level"]) + (atr * sl_pad_atr)
        risk = sl - entry
        if risk <= 0:
            return None

        return {
            "symbol": context.get("symbol", "UNKNOWN"),
            "side": "SELL",
            "entry_type": "MARKET",
            "entry_price": entry,
            "sl": float(sl),
            "tp1": float(entry - (risk * TP1_MULT)),
            "tp2": float(entry - (risk * TP2_MULT)),
            "tp3": float(entry - (risk * TP3_MULT)),
            "rationale": [
                f"LiqHunterV4 sweep@{latest_event.get('key_levels', [None])[0]}, score={confidence:.2f}>={score_gate:.2f}, vol_ratio={vol_ratio:.2f}"
            ],
            "confidence": confidence,
            "model": "LIQUIDITY",
        }

    return None
