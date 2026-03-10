import pandas as pd

TP1_R = 0.8
TP2_R = 2.0
TP3_R = 3.0
MIN_EVENT_SCORE = 0.90
MIN_SWEEP_VOL_RATIO = 1.15
MIN_BODY_RATIO = 0.20
BASE_SL_PAD_ATR = 0.60
SL_FLOOR_ATR = {"BTC": 1.40, "XAU": 1.00, "XAG": 1.00}
DEFAULT_SL_FLOOR_ATR = 0.90


def _sl_floor_multiplier(symbol: str) -> float:
    sym = (symbol or "").upper()
    for key, mult in SL_FLOOR_ATR.items():
        if key in sym:
            return mult
    return DEFAULT_SL_FLOOR_ATR


def _apply_sl_floor(entry: float, sl: float, side: str, atr: float, symbol: str):
    min_dist = atr * _sl_floor_multiplier(symbol)
    curr_dist = abs(entry - sl)
    if curr_dist >= min_dist:
        return sl, False, curr_dist

    if side == "BUY":
        return entry - min_dist, True, min_dist
    return entry + min_dist, True, min_dist


def signal_liquidity_hunter(df: pd.DataFrame, events: list, context: dict) -> dict:
    """
    Generate signals based on liquidity sweeps and displacements.
    Primary model for sideways, accumulation, distribution, and weak trend.
    """
    if not events:
        return None

    latest = df.iloc[-1]
    atr = float(latest.get("atr", df["high"].iloc[-1] - df["low"].iloc[-1]))
    if atr <= 0:
        return None

    # Skip low-quality bars where sweeps fail often.
    if bool(latest.get("vol_dryup", False)):
        return None
    if float(latest.get("body_ratio", 1.0)) < MIN_BODY_RATIO:
        return None

    vol_ratio = float(latest.get("vol_ratio", 1.0))
    net_power = float(latest.get("net_power", 0.0))

    valid_events = []
    for e in events:
        if e.get("type") not in ["SWEEP", "DISPLACEMENT_CONFIRMED"]:
            continue
        score = float(e.get("score", 0.0))
        if score < MIN_EVENT_SCORE:
            continue
        # Plain sweep must have at least some volume confirmation.
        if e.get("type") == "SWEEP":
            vol_ok = bool(e.get("vol_confirmed", False)) or vol_ratio >= MIN_SWEEP_VOL_RATIO
            if not vol_ok:
                continue
        valid_events.append(e)

    if not valid_events:
        return None

    latest_event = valid_events[-1]
    symbol = context.get("symbol", "UNKNOWN")
    key_level = latest_event.get("key_levels", [latest_event.get("invalidation_level")])[0]
    confidence = float(latest_event.get("score", 0.0))

    if latest_event["direction"] == "LONG_SIGNAL":
        if net_power < 0:
            return None

        entry = float(latest["close"])
        sl = float(latest_event["invalidation_level"]) - (atr * BASE_SL_PAD_ATR)
        sl, sl_floored, sl_dist = _apply_sl_floor(entry, sl, "BUY", atr, symbol)
        risk = entry - sl
        if risk <= 0:
            return None

        rationale = [f"Liq sweep at {key_level}. score={confidence:.2f}"]
        if sl_floored:
            rationale.append(f"SL floor applied: {sl_dist:.2f} ({_sl_floor_multiplier(symbol):.2f}x ATR)")

        return {
            "symbol": symbol,
            "side": "BUY",
            "entry_type": "MARKET",
            "entry_price": entry,
            "sl": float(sl),
            "tp1": float(entry + (risk * TP1_R)),
            "tp2": float(entry + (risk * TP2_R)),
            "tp3": float(entry + (risk * TP3_R)),
            "rationale": rationale,
            "confidence": confidence,
            "model": "LIQUIDITY",
        }

    if latest_event["direction"] == "SHORT_SIGNAL":
        if net_power > 0:
            return None

        entry = float(latest["close"])
        sl = float(latest_event["invalidation_level"]) + (atr * BASE_SL_PAD_ATR)
        sl, sl_floored, sl_dist = _apply_sl_floor(entry, sl, "SELL", atr, symbol)
        risk = sl - entry
        if risk <= 0:
            return None

        rationale = [f"Liq sweep at {key_level}. score={confidence:.2f}"]
        if sl_floored:
            rationale.append(f"SL floor applied: {sl_dist:.2f} ({_sl_floor_multiplier(symbol):.2f}x ATR)")

        return {
            "symbol": symbol,
            "side": "SELL",
            "entry_type": "MARKET",
            "entry_price": entry,
            "sl": float(sl),
            "tp1": float(entry - (risk * TP1_R)),
            "tp2": float(entry - (risk * TP2_R)),
            "tp3": float(entry - (risk * TP3_R)),
            "rationale": rationale,
            "confidence": confidence,
            "model": "LIQUIDITY",
        }

    return None
