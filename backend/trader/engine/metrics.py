from __future__ import annotations

import math

from .config import EngineConfig
from .models import PerformanceSnapshot, StrategySignal


def _coerce_probability(value: float) -> float:
    return max(0.05, min(0.95, float(value or 0.0)))


def _expectancy_from_profit_factor(win_rate: float, profit_factor: float) -> float:
    wr = _coerce_probability(win_rate)
    pf = float(profit_factor or 0.0)
    if pf <= 0 or not math.isfinite(pf):
        return 0.0
    return (1.0 - wr) * (pf - 1.0)


class PerformanceMetricsService:
    def __init__(self, db_store, config: EngineConfig) -> None:
        self.db_store = db_store
        self.config = config

    def build_snapshot(self, signal: StrategySignal, rr: float) -> PerformanceSnapshot:
        live_stats = self._get_live_stats(signal)
        shadow_stats = self._get_shadow_stats(signal)

        live_trades = int(live_stats.get("trades", 0) or 0)
        shadow_trades = int(shadow_stats.get("trades", 0) or 0)
        live_wr = float(live_stats.get("win_rate", 0.0) or 0.0)
        live_pf = float(live_stats.get("profit_factor", 0.0) or 0.0)

        live_expectancy = _expectancy_from_profit_factor(live_wr, live_pf) if live_trades > 0 else 0.0
        shadow_expectancy = (
            float(shadow_stats.get("net_r", 0.0) or 0.0) / shadow_trades
            if shadow_trades > 0
            else 0.0
        )

        signal_wr = self._signal_probability(signal.confidence)
        signal_expectancy = (signal_wr * max(0.0, rr)) - (1.0 - signal_wr)

        live_weight = min(0.55, (live_trades / max(1, self.config.min_live_trades)) * 0.55) if live_trades else 0.0
        shadow_weight = min(0.25, (shadow_trades / max(1, self.config.min_shadow_trades)) * 0.25) if shadow_trades else 0.0
        signal_weight = max(0.20, 1.0 - live_weight - shadow_weight)
        total_weight = live_weight + shadow_weight + signal_weight

        combined_expectancy = (
            (live_expectancy * live_weight)
            + (shadow_expectancy * shadow_weight)
            + (signal_expectancy * signal_weight)
        ) / max(total_weight, 1e-9)

        shadow_wr = float(shadow_stats.get("win_rate", 0.0) or 0.0)
        combined_wr = (
            (live_wr * live_weight)
            + (shadow_wr * shadow_weight)
            + (signal_wr * signal_weight)
        ) / max(total_weight, 1e-9)

        return PerformanceSnapshot(
            live_trades=live_trades,
            shadow_trades=shadow_trades,
            win_rate=combined_wr,
            profit_factor=live_pf,
            live_expectancy_r=live_expectancy,
            shadow_expectancy_r=shadow_expectancy,
            signal_expectancy_r=signal_expectancy,
            combined_expectancy_r=combined_expectancy,
        )

    def _signal_probability(self, confidence: float) -> float:
        centered = 0.5 + ((float(confidence or 0.0) - 0.5) * 0.70)
        return _coerce_probability(centered)

    def _get_live_stats(self, signal: StrategySignal) -> dict:
        if self.db_store is None or not hasattr(self.db_store, "get_closed_trade_model_performance"):
            return {}
        try:
            return self.db_store.get_closed_trade_model_performance(
                symbol=signal.standard_symbol,
                model=signal.model,
                days=self.config.performance_lookback_days,
            ) or {}
        except Exception:
            return {}

    def _get_shadow_stats(self, signal: StrategySignal) -> dict:
        if self.db_store is None or not hasattr(self.db_store, "get_shadow_model_performance"):
            return {}

        for candidate in (signal.symbol.upper(), signal.standard_symbol):
            try:
                stats = self.db_store.get_shadow_model_performance(
                    symbol=candidate,
                    model=signal.model,
                    lookback=max(self.config.min_shadow_trades, 12),
                ) or {}
            except Exception:
                stats = {}
            if int(stats.get("trades", 0) or 0) > 0:
                return stats
        return {}
