from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone


DEFAULT_TRAILING_CONFIG = {
    "enabled": True,
    "atr_multiplier": 2.5,
    "ghost_buffer_pct": 0.20,
    "break_even_activation_r": 0.80,
    "break_even_buffer_r": 0.08,
    "atr_trail_activation_r": 1.40,
    "aggressive_atr_multiplier": 1.50,
    "recovery_drawdown_pct": 12.0,
    "recovery_break_even_activation_r": 0.20,
    "recovery_atr_trail_activation_r": 0.80,
    "xau_monday_break_even_activation_r": 0.60,
    "min_sl_move": 0.50,
    "lock_tiers": [
        {"trigger_r": 1.00, "lock_r_multiple": 0.05},
        {"trigger_r": 1.50, "lock_r_multiple": 0.50},
        {"trigger_r": 2.00, "lock_r_multiple": 1.00},
        {"trigger_r": 3.00, "lock_r_multiple": 1.80},
    ],
}


def build_trailing_config(trailing_cfg: dict | None) -> dict:
    cfg = deepcopy(DEFAULT_TRAILING_CONFIG)
    payload = trailing_cfg if isinstance(trailing_cfg, dict) else {}
    for key, value in payload.items():
        if key == "lock_tiers":
            continue
        cfg[key] = value

    lock_tiers = []
    raw_tiers = payload.get("lock_tiers", cfg["lock_tiers"])
    for raw in raw_tiers:
        if not isinstance(raw, dict):
            continue
        trigger_r = float(raw.get("trigger_r", raw.get("r_multiple", 0.0)) or 0.0)
        if trigger_r <= 0:
            continue
        tier = {"trigger_r": trigger_r}
        if raw.get("lock_r_multiple") is not None:
            tier["lock_r_multiple"] = float(raw.get("lock_r_multiple") or 0.0)
        elif raw.get("lock_pct") is not None:
            tier["lock_pct"] = float(raw.get("lock_pct") or 0.0)
        else:
            continue
        lock_tiers.append(tier)

    cfg["lock_tiers"] = sorted(lock_tiers, key=lambda item: item["trigger_r"])
    cfg["activation_r"] = float(cfg.get("atr_trail_activation_r", cfg.get("activation_r", 1.4)) or 1.4)
    return cfg


def base_stop_distance(atr: float, cfg: dict) -> float:
    atr_mult = float(cfg.get("atr_multiplier", DEFAULT_TRAILING_CONFIG["atr_multiplier"]) or 0.0)
    ghost_buffer = float(cfg.get("ghost_buffer_pct", DEFAULT_TRAILING_CONFIG["ghost_buffer_pct"]) or 0.0)
    return float(max(0.0, atr * atr_mult * (1.0 + ghost_buffer)))


def estimate_reference_risk(entry: float, current_sl: float, atr: float, cfg: dict) -> float:
    current_distance = abs(entry - current_sl) if current_sl else 0.0
    return float(max(current_distance, base_stop_distance(atr, cfg)))


def _improves_sl(side: str, candidate_sl: float, current_sl: float) -> bool:
    side = str(side or "").upper()
    if candidate_sl <= 0:
        return False
    if current_sl <= 0:
        return True
    if side == "BUY":
        return candidate_sl > current_sl
    if side == "SELL":
        return candidate_sl < current_sl
    return False


def _price_with_profit(entry: float, side: str, profit_distance: float) -> float:
    return entry + profit_distance if str(side or "").upper() == "BUY" else entry - profit_distance


def resolve_activation_levels(
    standard_symbol: str,
    floating_dd_pct: float,
    cfg: dict,
    now_utc: datetime | None = None,
) -> tuple[float, float]:
    break_even_r = float(cfg.get("break_even_activation_r", DEFAULT_TRAILING_CONFIG["break_even_activation_r"]) or 0.0)
    atr_trail_r = float(cfg.get("atr_trail_activation_r", cfg.get("activation_r", DEFAULT_TRAILING_CONFIG["atr_trail_activation_r"])) or 0.0)
    recovery_dd_pct = float(cfg.get("recovery_drawdown_pct", DEFAULT_TRAILING_CONFIG["recovery_drawdown_pct"]) or 0.0)

    symbol = str(standard_symbol or "").upper()
    now = now_utc or datetime.now(timezone.utc)
    now_th = now.astimezone(timezone(timedelta(hours=7)))
    is_monday_morning_xau = "XAU" in symbol and now_th.weekday() == 0 and now_th.hour < 10
    if is_monday_morning_xau:
        break_even_r = min(
            break_even_r,
            float(cfg.get("xau_monday_break_even_activation_r", break_even_r) or break_even_r),
        )

    if floating_dd_pct >= recovery_dd_pct > 0:
        break_even_r = min(
            break_even_r,
            float(cfg.get("recovery_break_even_activation_r", break_even_r) or break_even_r),
        )
        atr_trail_r = min(
            atr_trail_r,
            float(cfg.get("recovery_atr_trail_activation_r", atr_trail_r) or atr_trail_r),
        )

    return break_even_r, atr_trail_r


def compute_trailing_stop(
    *,
    entry: float,
    current_sl: float,
    current_price: float,
    side: str,
    atr: float,
    standard_symbol: str = "",
    floating_dd_pct: float = 0.0,
    cfg: dict | None = None,
    now_utc: datetime | None = None,
) -> dict:
    config = build_trailing_config(cfg)
    normalized_side = str(side or "").upper()
    if not config.get("enabled", True):
        return {"new_sl": current_sl, "reason": "", "r_multiple": 0.0, "reference_risk": 0.0}
    if normalized_side not in {"BUY", "SELL"} or atr <= 0:
        return {"new_sl": current_sl, "reason": "", "r_multiple": 0.0, "reference_risk": 0.0}

    if normalized_side == "BUY":
        profit_distance = current_price - entry
    else:
        profit_distance = entry - current_price

    reference_risk = estimate_reference_risk(entry, current_sl, atr, config)
    if reference_risk <= 0:
        return {"new_sl": current_sl, "reason": "", "r_multiple": 0.0, "reference_risk": 0.0}

    r_multiple = profit_distance / reference_risk
    if profit_distance <= 0:
        return {
            "new_sl": current_sl,
            "reason": "",
            "r_multiple": r_multiple,
            "reference_risk": reference_risk,
        }

    new_sl = current_sl
    reason = ""
    break_even_r, atr_trail_r = resolve_activation_levels(
        standard_symbol,
        floating_dd_pct,
        config,
        now_utc=now_utc,
    )

    if r_multiple >= break_even_r:
        buffer_profit = reference_risk * float(config.get("break_even_buffer_r", DEFAULT_TRAILING_CONFIG["break_even_buffer_r"]) or 0.0)
        be_sl = _price_with_profit(entry, normalized_side, buffer_profit)
        if _improves_sl(normalized_side, be_sl, current_sl):
            new_sl = be_sl
            reason = f"Break-Even Lock (+{break_even_r:.2f}R, offset {buffer_profit:.2f})"

    for tier in sorted(config.get("lock_tiers", []), key=lambda item: item["trigger_r"], reverse=True):
        trigger_r = float(tier.get("trigger_r", 0.0) or 0.0)
        if r_multiple < trigger_r:
            continue

        if tier.get("lock_r_multiple") is not None:
            locked_profit = reference_risk * float(tier.get("lock_r_multiple") or 0.0)
        else:
            locked_profit = profit_distance * float(tier.get("lock_pct") or 0.0)

        tier_sl = _price_with_profit(entry, normalized_side, locked_profit)
        if _improves_sl(normalized_side, tier_sl, new_sl):
            new_sl = tier_sl
            reason = f"Profit Lock +{trigger_r:.2f}R"
        break

    if r_multiple >= atr_trail_r:
        recovery_dd_pct = float(config.get("recovery_drawdown_pct", DEFAULT_TRAILING_CONFIG["recovery_drawdown_pct"]) or 0.0)
        trail_mult = float(config.get("atr_multiplier", DEFAULT_TRAILING_CONFIG["atr_multiplier"]) or 0.0)
        if floating_dd_pct >= recovery_dd_pct > 0:
            trail_mult = float(config.get("aggressive_atr_multiplier", DEFAULT_TRAILING_CONFIG["aggressive_atr_multiplier"]) or trail_mult)

        atr_sl = current_price - (atr * trail_mult) if normalized_side == "BUY" else current_price + (atr * trail_mult)
        if _improves_sl(normalized_side, atr_sl, new_sl):
            new_sl = atr_sl
            reason = f"ATR Trail ({trail_mult:.2f}x ATR)"

    return {
        "new_sl": float(new_sl),
        "reason": reason,
        "r_multiple": float(r_multiple),
        "reference_risk": float(reference_risk),
        "break_even_activation_r": float(break_even_r),
        "atr_trail_activation_r": float(atr_trail_r),
    }
