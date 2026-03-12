from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from backend.trader.config.paths import OPUS_DB_PATH, SETTINGS_PATH

from .config import EngineConfig
from .execution import ExecutionService
from .journal import TradingJournal
from .logging import StructuredEventLogger
from .metrics import PerformanceMetricsService
from .models import EngineResult, MarketSnapshot
from .portfolio import PortfolioService
from .risk import RiskManager
from .strategy import StrategyService


class ProfessionalTradingEngine:
    def __init__(
        self,
        *,
        settings_path: str | Path = SETTINGS_PATH,
        db_store=None,
        logger=None,
        journal_db_path: str | Path = OPUS_DB_PATH,
    ) -> None:
        self.config = EngineConfig.load(settings_path)
        self.logger = logger or logging.getLogger("opus_logger")
        self.events = StructuredEventLogger(self.logger)
        self.strategy = StrategyService()
        self.portfolio = PortfolioService(self.config)
        self.risk = RiskManager(self.config)
        self.metrics = PerformanceMetricsService(db_store, self.config)
        self.execution = ExecutionService()
        self.journal = TradingJournal(journal_db_path)

    def observe_account_state(self, account_state: dict) -> None:
        self.portfolio.observe_equity(
            float(account_state.get("equity", 0.0) or 0.0),
            float(account_state.get("balance", 0.0) or 0.0),
        )

    def process_signal(
        self,
        *,
        raw_signal: dict,
        symbol: str,
        timeframe: str,
        session: str,
        market_state: dict,
        account_state: dict,
        executor,
        mode: str,
        mt5_module=None,
        atr: float = 0.0,
        timestamp: datetime | None = None,
    ) -> EngineResult:
        signal = self.strategy.normalize_signal(raw_signal, symbol, timeframe)
        if signal is None:
            return EngineResult(status="skipped", reason="signal_invalid")

        market = self._build_market_snapshot(
            symbol=symbol,
            timeframe=timeframe,
            session=session,
            market_state=market_state,
            atr=atr,
            timestamp=timestamp,
        )
        portfolio = self.portfolio.snapshot(account_state, mt5_module)

        plan, reason = self.risk.build_order_plan(signal, market, portfolio)
        performance = self.metrics.build_snapshot(signal, rr=plan.rr if plan else 0.0)

        if plan is None:
            result = EngineResult(
                status="blocked",
                reason=reason,
                blocked_reasons=[reason],
                signal=signal,
                performance=performance,
                portfolio=portfolio,
            )
            self.journal.record_result(mode, market, result)
            self.journal.record_metrics(mode, market, result)
            self.events.warning(
                "engine_plan_blocked",
                symbol=signal.standard_symbol,
                model=signal.model,
                reasons=result.blocked_reasons,
            )
            return result

        blocked_reasons = self.risk.assess_plan(signal, market, portfolio, plan, performance)
        if blocked_reasons:
            result = EngineResult(
                status="blocked",
                reason="; ".join(blocked_reasons),
                blocked_reasons=blocked_reasons,
                signal=signal,
                plan=plan,
                performance=performance,
                portfolio=portfolio,
            )
            self.journal.record_result(mode, market, result)
            self.journal.record_metrics(mode, market, result)
            self.events.warning(
                "engine_trade_blocked",
                symbol=signal.standard_symbol,
                model=signal.model,
                session=market.session,
                expectancy_r=performance.combined_expectancy_r,
                rr=plan.rr,
                reasons=blocked_reasons,
            )
            return result

        execution = self.execution.execute(executor, signal, plan)
        status = "executed" if str(mode).lower() == "live" else "simulated"
        if execution.status != "ok":
            status = "error"

        result = EngineResult(
            status=status,
            reason=execution.reason,
            signal=signal,
            plan=plan,
            performance=performance,
            portfolio=portfolio,
            execution=execution,
        )
        self.journal.record_result(mode, market, result)
        self.journal.record_metrics(mode, market, result)

        if execution.status == "ok":
            self.events.info(
                "engine_trade_executed",
                symbol=signal.standard_symbol,
                model=signal.model,
                mode=str(mode).lower(),
                lot_size=plan.lot_size,
                risk_usd=plan.risk_usd,
                expectancy_r=performance.combined_expectancy_r,
                rr=plan.rr,
                ticket=execution.ticket,
            )
        else:
            self.events.error(
                "engine_trade_error",
                symbol=signal.standard_symbol,
                model=signal.model,
                reason=execution.reason or "execution_failed",
            )
        return result

    def _build_market_snapshot(
        self,
        *,
        symbol: str,
        timeframe: str,
        session: str,
        market_state: dict,
        atr: float,
        timestamp: datetime | None,
    ) -> MarketSnapshot:
        from backend.trader.data.mapper import mapper

        ts = timestamp or datetime.now(timezone.utc)
        return MarketSnapshot(
            symbol=symbol,
            standard_symbol=mapper.to_standard(symbol).upper(),
            timeframe=str(timeframe or "").upper(),
            session=str(session or "").upper(),
            bid=float(market_state.get("bid", 0.0) or 0.0),
            ask=float(market_state.get("ask", 0.0) or 0.0),
            spread_points=float(market_state.get("spread", 0.0) or 0.0),
            point_size=float(market_state.get("point", market_state.get("tick_size", 0.0)) or 0.0),
            tick_value=float(market_state.get("tick_value", 0.0) or 0.0),
            tick_size=float(market_state.get("tick_size", 0.0) or 0.0),
            volume_min=float(market_state.get("volume_min", 0.0) or 0.0),
            volume_max=float(market_state.get("volume_max", 0.0) or 0.0),
            volume_step=float(market_state.get("volume_step", 0.0) or 0.0),
            atr=float(atr or market_state.get("atr", 0.0) or 0.0),
            atr_deviation=float(market_state.get("atr_deviation", 1.0) or 1.0),
            timestamp=ts,
        )
