from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class StrategySignal:
    symbol: str
    standard_symbol: str
    timeframe: str
    side: str
    entry_price: float
    confidence: float
    model: str
    entry_type: str = "MARKET"
    rationale: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    standard_symbol: str
    timeframe: str
    session: str
    bid: float
    ask: float
    spread_points: float
    point_size: float
    tick_value: float
    tick_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    atr: float
    atr_deviation: float
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class OpenExposure:
    symbol: str
    standard_symbol: str
    group: str
    side: str
    volume: float
    risk_usd: float


@dataclass(slots=True)
class PortfolioSnapshot:
    balance: float
    equity: float
    daily_pnl: float
    floating_pnl: float
    combined_pnl: float
    margin: float
    margin_free: float
    peak_equity: float
    drawdown_pct: float
    open_risk_usd: float
    exposures: list[OpenExposure] = field(default_factory=list)


@dataclass(slots=True)
class PerformanceSnapshot:
    live_trades: int = 0
    shadow_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    live_expectancy_r: float = 0.0
    shadow_expectancy_r: float = 0.0
    signal_expectancy_r: float = 0.0
    combined_expectancy_r: float = 0.0


@dataclass(slots=True)
class OrderPlan:
    entry_price: float
    stop_loss: float
    take_profit: float
    take_profit_2: float
    take_profit_3: float
    stop_distance: float
    rr: float
    risk_pct: float
    risk_usd: float
    lot_size: float
    spread_points: float
    estimated_slippage_points: float
    point_size: float

    def to_signal_payload(self, signal: StrategySignal) -> dict[str, Any]:
        payload = dict(signal.raw)
        payload.update(
            {
                "symbol": signal.symbol,
                "side": signal.side,
                "model": signal.model,
                "confidence": signal.confidence,
                "entry_type": signal.entry_type,
                "entry_price": self.entry_price,
                "sl": self.stop_loss,
                "tp1": self.take_profit,
                "tp2": self.take_profit_2,
                "tp3": self.take_profit_3,
                "rationale": list(signal.rationale),
            }
        )
        return payload


@dataclass(slots=True)
class ExecutionReport:
    status: str
    reason: str = ""
    ticket: int | None = None
    fill_price: float | None = None
    slippage_points: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EngineResult:
    status: str
    reason: str = ""
    blocked_reasons: list[str] = field(default_factory=list)
    signal: StrategySignal | None = None
    plan: OrderPlan | None = None
    performance: PerformanceSnapshot | None = None
    portfolio: PortfolioSnapshot | None = None
    execution: ExecutionReport | None = None
