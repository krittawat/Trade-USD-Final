import json
import logging
from typing import Dict, Iterable

from backend.trader.config.paths import SETTINGS_PATH
from backend.trader.data.mapper import mapper
from backend.trader.storage.sqlite_db import db


logger = logging.getLogger("opus_logger")

_DEFAULT_CFG = {
    "enabled": True,
    "live_lookback_days": 30,
    "live_min_trades": 4,
    "shadow_lookback_trades": 12,
    "shadow_min_trades": 6,
    "cold_live_max_win_rate": 0.42,
    "cold_live_max_profit_factor": 0.95,
    "hot_live_min_win_rate": 0.57,
    "hot_live_min_profit_factor": 1.20,
    "shadow_cold_max_win_rate": 0.35,
    "elite_bypass_confidence": 0.84,
    "elite_bypass_rr": 1.80,
    "live_hot_confidence_boost": 0.03,
    "shadow_cold_confidence_penalty": 0.04,
    "asia_nonprime_min_confidence": 0.80,
    "sleep_nonprime_min_confidence": 0.86,
}


def _load_guard_cfg() -> Dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f).get("strategy", {}).get("professional_guard", {}) or {}
    except Exception:
        cfg = {}
    merged = dict(_DEFAULT_CFG)
    merged.update(cfg)
    for pct_key in (
        "cold_live_max_win_rate",
        "hot_live_min_win_rate",
        "shadow_cold_max_win_rate",
        "elite_bypass_confidence",
        "asia_nonprime_min_confidence",
        "sleep_nonprime_min_confidence",
    ):
        value = float(merged.get(pct_key, _DEFAULT_CFG[pct_key]) or 0.0)
        merged[pct_key] = value / 100.0 if value > 1.0 else value
    return merged


_GUARD_CFG = _load_guard_cfg()


def _symbol_family(symbol: str) -> str:
    text = str(symbol or "").upper()
    if any(tok in text for tok in ("XAU", "XAG", "GOLD", "SILVER")):
        return "METALS"
    if "BTC" in text or "ETH" in text or "SOL" in text:
        return "CRYPTO"
    if "OIL" in text:
        return "ENERGY"
    if any(tok in text for tok in ("30", "TEC", "NAS", "500", "HK", "JP", "AUS")):
        return "INDICES"
    if any(tok in text for tok in ("EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD")):
        return "FX"
    return "OTHER"


def _normalize_standard_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip()
    if not raw:
        return ""
    std = mapper.to_standard(raw)
    if std == raw and raw.endswith(("m", "c", "M", "C")):
        std = raw[:-1]
    return str(std or raw).upper()


def _copy_signal(signal: dict) -> dict:
    out = dict(signal)
    out["rationale"] = list(signal.get("rationale") or [])
    return out


class DBPerformanceProvider:
    def get_live_stats(self, symbol: str, model: str, days: int) -> Dict:
        return db.get_closed_trade_model_performance(symbol=symbol, model=model, days=days)

    def get_shadow_stats(self, symbol: str, model: str, lookback: int) -> Dict:
        return db.get_shadow_model_performance(symbol=symbol, model=model, lookback=lookback)


def apply_professional_guard(
    candidates: Iterable[dict],
    context: dict,
    *,
    perf_provider=None,
    config: Dict | None = None,
) -> list[dict]:
    cfg = dict(_GUARD_CFG)
    if config:
        cfg.update(config)
    if not bool(cfg.get("enabled", True)):
        return list(candidates or [])

    provider = perf_provider or DBPerformanceProvider()
    session = str(context.get("session") or context.get("current_session") or "").upper()
    live_days = int(cfg.get("live_lookback_days", 30) or 30)
    live_min_trades = int(cfg.get("live_min_trades", 4) or 4)
    shadow_lookback = int(cfg.get("shadow_lookback_trades", 12) or 12)
    shadow_min_trades = int(cfg.get("shadow_min_trades", 6) or 6)

    live_cache: Dict[tuple[str, str], Dict] = {}
    shadow_cache: Dict[tuple[str, str], Dict] = {}
    approved: list[dict] = []

    for signal in candidates or []:
        if not isinstance(signal, dict):
            continue

        sig = _copy_signal(signal)
        symbol = _normalize_standard_symbol(sig.get("symbol") or context.get("symbol"))
        model = str(sig.get("model", "UNKNOWN") or "UNKNOWN").upper()
        family = _symbol_family(symbol)
        confidence = float(sig.get("confidence", 0.0) or 0.0)
        rr = float(sig.get("rr", 0.0) or 0.0)
        key = (symbol, model)

        reasons: list[str] = []
        blocked = False

        if family in {"METALS", "ENERGY", "INDICES"} and session == "ASIA":
            needed = float(cfg.get("asia_nonprime_min_confidence", 0.80) or 0.80)
            if confidence < needed:
                blocked = True
                reasons.append(f"Asia session for {family.lower()} requires >= {needed:.2f} confidence")
            else:
                reasons.append("A+ Asia-session exception")

        if not blocked and family != "CRYPTO" and session == "SLEEP":
            needed = float(cfg.get("sleep_nonprime_min_confidence", 0.86) or 0.86)
            elite_rr = float(cfg.get("elite_bypass_rr", 1.80) or 1.80)
            if confidence < needed or rr < elite_rr:
                blocked = True
                reasons.append(f"Sleep session requires elite setup ({needed:.2f} conf / {elite_rr:.2f}R)")

        if key not in live_cache:
            live_cache[key] = provider.get_live_stats(symbol, model, live_days) or {}
        live_stats = live_cache[key]
        live_trades = int(live_stats.get("trades", 0) or 0)
        cold_live = (
            live_trades >= live_min_trades
            and float(live_stats.get("win_rate", 0.0) or 0.0) <= float(cfg.get("cold_live_max_win_rate", 0.42))
            and float(live_stats.get("profit_factor", 0.0) or 0.0) <= float(cfg.get("cold_live_max_profit_factor", 0.95))
            and float(live_stats.get("net_pnl", 0.0) or 0.0) <= 0.0
        )
        hot_live = (
            live_trades >= live_min_trades
            and float(live_stats.get("win_rate", 0.0) or 0.0) >= float(cfg.get("hot_live_min_win_rate", 0.57))
            and float(live_stats.get("profit_factor", 0.0) or 0.0) >= float(cfg.get("hot_live_min_profit_factor", 1.20))
            and float(live_stats.get("net_pnl", 0.0) or 0.0) > 0.0
        )

        elite_conf = float(cfg.get("elite_bypass_confidence", 0.84) or 0.84)
        elite_rr = float(cfg.get("elite_bypass_rr", 1.80) or 1.80)
        elite_override = confidence >= elite_conf and rr >= elite_rr

        if not blocked and cold_live:
            if elite_override:
                reasons.append(
                    f"A+ override vs cold live stats (WR {float(live_stats.get('win_rate', 0.0))*100:.0f}% / PF {float(live_stats.get('profit_factor', 0.0)):.2f})"
                )
            else:
                blocked = True
                reasons.append(
                    f"Cold live stats for {model} on {symbol} "
                    f"(WR {float(live_stats.get('win_rate', 0.0))*100:.0f}% / PF {float(live_stats.get('profit_factor', 0.0)):.2f})"
                )

        if key not in shadow_cache:
            shadow_cache[key] = provider.get_shadow_stats(symbol, model, shadow_lookback) or {}
        shadow_stats = shadow_cache[key]
        shadow_trades = int(shadow_stats.get("trades", 0) or 0)
        cold_shadow = (
            shadow_trades >= shadow_min_trades
            and float(shadow_stats.get("win_rate", 0.0) or 0.0) <= float(cfg.get("shadow_cold_max_win_rate", 0.35))
        )

        if not blocked and hot_live:
            boost = float(cfg.get("live_hot_confidence_boost", 0.03) or 0.03)
            confidence = min(1.0, confidence + boost)
            reasons.append(
                f"Hot live edge +{boost:.2f} "
                f"(WR {float(live_stats.get('win_rate', 0.0))*100:.0f}% / PF {float(live_stats.get('profit_factor', 0.0)):.2f})"
            )

        if not blocked and cold_shadow:
            penalty = float(cfg.get("shadow_cold_confidence_penalty", 0.04) or 0.04)
            confidence = max(0.0, confidence - penalty)
            reasons.append(
                f"Shadow cold streak -{penalty:.2f} "
                f"(WR {float(shadow_stats.get('win_rate', 0.0))*100:.0f}% / N {shadow_trades})"
            )

        if blocked:
            logger.info(f"🚫 [PRO GUARD] {model} {symbol} blocked: {' | '.join(reasons)}")
            continue

        sig["confidence"] = confidence
        sig["professional_guard"] = {
            "session": session,
            "family": family,
            "live_stats": live_stats,
            "shadow_stats": shadow_stats,
            "elite_override": elite_override,
        }
        for reason in reasons:
            sig["rationale"].append(f"🧠 {reason}")
        approved.append(sig)

    return approved
