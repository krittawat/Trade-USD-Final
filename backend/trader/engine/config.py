from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from backend.trader.config.paths import SETTINGS_PATH
from backend.trader.data.mapper import mapper


def _base_symbol(symbol: str) -> str:
    return mapper.to_standard(str(symbol or "")).upper()


def _sorted_rr_levels(*values: float) -> tuple[float, float, float]:
    cleaned = sorted(max(1.1, float(v or 0.0)) for v in values if float(v or 0.0) > 0)
    while len(cleaned) < 3:
        cleaned.append(cleaned[-1] * 1.35 if cleaned else 1.8)
    return tuple(cleaned[:3])


def _default_spread_limits() -> dict[str, float]:
    return {
        "XAUUSD": 1000.0,
        "XAGUSD": 1500.0,
        "BTCUSD": 5000.0,
        "USOIL": 300.0,
        "US30": 300.0,
        "USTEC": 500.0,
    }


def _default_slippage_limits() -> dict[str, float]:
    return {
        "XAUUSD": 120.0,
        "XAGUSD": 180.0,
        "BTCUSD": 1500.0,
        "USOIL": 60.0,
        "US30": 80.0,
        "USTEC": 120.0,
        "EURUSD": 25.0,
        "GBPUSD": 30.0,
        "USDJPY": 30.0,
    }


def _default_session_allowlist() -> dict[str, set[str]]:
    return {
        "BTCUSD": {"ASIA", "LONDON", "NY", "SLEEP"},
        "XAUUSD": {"LONDON", "NY"},
        "XAGUSD": {"LONDON", "NY"},
        "USOIL": {"LONDON", "NY"},
        "US30": {"LONDON", "NY"},
        "USTEC": {"LONDON", "NY"},
        "EURUSD": {"ASIA", "LONDON", "NY"},
        "GBPUSD": {"ASIA", "LONDON", "NY"},
        "USDJPY": {"ASIA", "LONDON", "NY"},
    }


def _default_correlation_groups() -> dict[str, set[str]]:
    return {
        "METALS": {"XAUUSD", "XAGUSD"},
        "INDICES": {"US30", "USTEC"},
        "ENERGY": {"USOIL", "UKOIL"},
        "CRYPTO": {"BTCUSD"},
        "USD_MAJORS": {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"},
        "JPY": {"USDJPY"},
    }


@dataclass(slots=True)
class EngineConfig:
    base_risk_pct: float = 0.3
    max_open_risk_pct: float = 1.0
    max_daily_loss_currency: float = 12.0
    max_drawdown_pct: float = 15.0
    max_spread_points: dict[str, float] = field(default_factory=_default_spread_limits)
    max_slippage_points: dict[str, float] = field(default_factory=_default_slippage_limits)
    base_stop_atr_multiplier: float = 1.2
    min_stop_atr_multiplier: dict[str, float] = field(default_factory=dict)
    max_stop_atr_multiplier: dict[str, float] = field(default_factory=dict)
    rr_levels: tuple[float, float, float] = (1.5, 2.0, 2.5)
    min_rr: float = 1.2
    min_expectancy_r: float = 0.10
    min_live_trades: int = 6
    min_shadow_trades: int = 8
    performance_lookback_days: int = 90
    correlation_groups: dict[str, set[str]] = field(default_factory=_default_correlation_groups)
    same_direction_correlation_limit: int = 1
    max_group_risk_pct: float = 0.7
    session_allowlist: dict[str, set[str]] = field(default_factory=_default_session_allowlist)

    @classmethod
    def load(cls, settings_path: str | Path = SETTINGS_PATH) -> "EngineConfig":
        with open(Path(settings_path), "r", encoding="utf-8") as handle:
            payload = json.load(handle)

        risk_limits = (payload or {}).get("risk_limits", {}) or {}
        strategy = (payload or {}).get("strategy", {}) or {}
        governor = (payload or {}).get("opus_governor", {}) or {}
        engine_cfg = (payload or {}).get("professional_engine", {}) or {}

        base_risk_pct = float(
            engine_cfg.get(
                "base_risk_pct",
                min(
                    float(risk_limits.get("max_risk_per_trade_percent", 0.5) or 0.5),
                    float(risk_limits.get("hard_risk_per_trade_pct", risk_limits.get("max_risk_per_trade_percent", 0.5)) or 0.5),
                ),
            )
        )
        rr_levels = _sorted_rr_levels(
            engine_cfg.get("tp1_rr", strategy.get("tp1_risk_multiplier", 1.5)),
            engine_cfg.get("tp2_rr", strategy.get("tp2_risk_multiplier", 2.5)),
            engine_cfg.get("tp3_rr", strategy.get("tp3_risk_multiplier", 3.0)),
        )

        return cls(
            base_risk_pct=max(0.05, base_risk_pct),
            max_open_risk_pct=float(engine_cfg.get("max_open_risk_pct", risk_limits.get("hard_max_open_risk_pct", 1.0)) or 1.0),
            max_daily_loss_currency=abs(float(engine_cfg.get("max_daily_loss_currency", risk_limits.get("max_daily_loss_currency", 12.0)) or 12.0)),
            max_drawdown_pct=float(engine_cfg.get("max_drawdown_pct", payload.get("hedge_threshold_dd_pct", 15.0)) or 15.0),
            max_spread_points={
                str(k).upper(): float(v)
                for k, v in (risk_limits.get("max_spread_points", {}) or {}).items()
            }
            or _default_spread_limits(),
            max_slippage_points={
                str(k).upper(): float(v)
                for k, v in (engine_cfg.get("max_slippage_points", {}) or {}).items()
            }
            or _default_slippage_limits(),
            base_stop_atr_multiplier=float(engine_cfg.get("base_stop_atr_multiplier", strategy.get("sl_atr_multiplier", 1.2)) or 1.2),
            min_stop_atr_multiplier={
                str(k).upper(): float(v)
                for k, v in (risk_limits.get("min_sl_atr_multiplier", {}) or {}).items()
            },
            max_stop_atr_multiplier={
                str(k).upper(): float(v)
                for k, v in (risk_limits.get("max_sl_atr_multiplier", {}) or {}).items()
            },
            rr_levels=rr_levels,
            min_rr=float(engine_cfg.get("min_rr", governor.get("rr_minimum", 1.2)) or 1.2),
            min_expectancy_r=float(engine_cfg.get("min_expectancy_r", 0.10) or 0.10),
            min_live_trades=int(engine_cfg.get("min_live_trades", 6) or 6),
            min_shadow_trades=int(engine_cfg.get("min_shadow_trades", 8) or 8),
            performance_lookback_days=int(engine_cfg.get("performance_lookback_days", 90) or 90),
            correlation_groups={
                str(group).upper(): {str(sym).upper() for sym in symbols}
                for group, symbols in (
                    _default_correlation_groups()
                    | {
                        str(group).upper(): set(symbols)
                        for group, symbols in (engine_cfg.get("correlation_groups", {}) or {}).items()
                    }
                ).items()
            },
            same_direction_correlation_limit=int(engine_cfg.get("same_direction_correlation_limit", 1) or 1),
            max_group_risk_pct=float(engine_cfg.get("max_group_risk_pct", min(float(risk_limits.get("hard_max_open_risk_pct", 1.0) or 1.0), 0.7)) or 0.7),
            session_allowlist={
                str(symbol).upper(): {str(token).upper() for token in tokens}
                for symbol, tokens in (
                    _default_session_allowlist()
                    | {
                        str(symbol).upper(): set(tokens)
                        for symbol, tokens in (engine_cfg.get("session_allowlist", {}) or {}).items()
                    }
                ).items()
            },
        )

    def symbol_key(self, symbol: str) -> str:
        return _base_symbol(symbol)

    def spread_limit(self, symbol: str) -> float:
        key = self.symbol_key(symbol)
        return float(self.max_spread_points.get(key, self.max_spread_points.get("DEFAULT", float("inf"))))

    def slippage_limit(self, symbol: str) -> float:
        key = self.symbol_key(symbol)
        return float(self.max_slippage_points.get(key, self.max_slippage_points.get("DEFAULT", float("inf"))))

    def stop_multiplier(self, symbol: str) -> float:
        key = self.symbol_key(symbol)
        value = float(self.base_stop_atr_multiplier)
        minimum = float(self.min_stop_atr_multiplier.get(key, value) or value)
        maximum = float(self.max_stop_atr_multiplier.get(key, value) or value)
        if maximum < minimum:
            maximum = minimum
        return min(max(value, minimum), maximum)

    def rr_levels_for(self, symbol: str) -> tuple[float, float, float]:
        _ = symbol
        return _sorted_rr_levels(*self.rr_levels)

    def sessions_for(self, symbol: str) -> set[str]:
        key = self.symbol_key(symbol)
        return set(self.session_allowlist.get(key, {"ASIA", "LONDON", "NY"}))

    def correlation_group(self, symbol: str) -> str:
        key = self.symbol_key(symbol)
        for group, symbols in self.correlation_groups.items():
            if key in symbols:
                return group
        return key
