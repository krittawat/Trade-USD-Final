from __future__ import annotations

import math

from .config import EngineConfig
from .models import MarketSnapshot, OrderPlan, PerformanceSnapshot, PortfolioSnapshot, StrategySignal


def _round_down_to_step(value: float, step: float) -> float:
    if step <= 0:
        return max(0.0, value)
    if value <= 0:
        return 0.0
    return max(0.0, math.floor(value / step) * step)


def _round_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return float(price)
    return float(round(round(price / tick_size) * tick_size, 8))


class RiskManager:
    def __init__(self, config: EngineConfig) -> None:
        self.config = config

    def build_order_plan(
        self,
        signal: StrategySignal,
        market: MarketSnapshot,
        portfolio: PortfolioSnapshot,
    ) -> tuple[OrderPlan | None, str]:
        if signal.side not in {"BUY", "SELL"}:
            return None, "invalid_side"
        if portfolio.equity <= 0:
            return None, "equity_unavailable"
        if market.atr <= 0 or market.tick_size <= 0 or market.tick_value <= 0:
            return None, "incomplete_market_pricing"

        entry_price = float(signal.entry_price or 0.0)
        estimated_slippage_points = 0.0
        if signal.entry_type != "LIMIT":
            entry_price = float(market.ask if signal.side == "BUY" else market.bid)
            if market.point_size > 0 and signal.entry_price > 0:
                estimated_slippage_points = abs(entry_price - float(signal.entry_price)) / market.point_size
        if entry_price <= 0:
            return None, "entry_price_unavailable"

        stop_multiplier = self.config.stop_multiplier(signal.standard_symbol)
        stop_distance = max(market.atr * stop_multiplier, market.point_size * 10.0)
        rr1, rr2, rr3 = self.config.rr_levels_for(signal.standard_symbol)

        if signal.side == "BUY":
            stop_loss = _round_to_tick(entry_price - stop_distance, market.tick_size)
            take_profit = _round_to_tick(entry_price + (stop_distance * rr1), market.tick_size)
            take_profit_2 = _round_to_tick(entry_price + (stop_distance * rr2), market.tick_size)
            take_profit_3 = _round_to_tick(entry_price + (stop_distance * rr3), market.tick_size)
        else:
            stop_loss = _round_to_tick(entry_price + stop_distance, market.tick_size)
            take_profit = _round_to_tick(entry_price - (stop_distance * rr1), market.tick_size)
            take_profit_2 = _round_to_tick(entry_price - (stop_distance * rr2), market.tick_size)
            take_profit_3 = _round_to_tick(entry_price - (stop_distance * rr3), market.tick_size)

        risk_per_lot = (stop_distance / market.tick_size) * market.tick_value
        if risk_per_lot <= 0:
            return None, "risk_per_lot_invalid"

        risk_budget_usd = portfolio.equity * (self.config.base_risk_pct / 100.0)
        raw_lot = risk_budget_usd / risk_per_lot
        lot_size = _round_down_to_step(raw_lot, market.volume_step)
        if lot_size < market.volume_min:
            # 💰 [MICRO ACCOUNT ADAPTATION]
            # If account is small (< $500), allow 0.01 lot even if it exceeds standard risk %
            # but cap it at a hard "Survival Limit" (e.g. 10% of equity)
            min_lot_risk = risk_per_lot * market.volume_min
            survival_cap = portfolio.equity * 0.10 # Max 10% risk per trade as absolute floor
            
            if portfolio.equity < 500.0 and min_lot_risk <= survival_cap:
                lot_size = market.volume_min
                # logger.info(f"🛡️ [MICRO-CAP] Allow min_lot 0.01 (Risk=${min_lot_risk:.2f} <= Cap=${survival_cap:.2f})")
            elif min_lot_risk > (risk_budget_usd * 1.05):
                return None, "min_lot_exceeds_risk_budget | 🚫 เสี่ยงสูงเกินไปสำหรับพอร์ตเล็ก (Min Lot > Budget)"
            else:
                lot_size = market.volume_min

        lot_size = min(float(market.volume_max), lot_size)
        actual_risk_usd = risk_per_lot * lot_size

        return (
            OrderPlan(
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                take_profit_2=take_profit_2,
                take_profit_3=take_profit_3,
                stop_distance=stop_distance,
                rr=max(0.0, rr1),
                risk_pct=float(self.config.base_risk_pct),
                risk_usd=max(0.0, actual_risk_usd),
                lot_size=lot_size,
                spread_points=float(market.spread_points),
                estimated_slippage_points=max(0.0, estimated_slippage_points),
                point_size=float(market.point_size),
            ),
            "",
        )

    def assess_plan(
        self,
        signal: StrategySignal,
        market: MarketSnapshot,
        portfolio: PortfolioSnapshot,
        plan: OrderPlan,
        performance: PerformanceSnapshot,
    ) -> list[str]:
        reasons: list[str] = []

        if not self._session_allowed(signal.standard_symbol, market.session):
            reasons.append(f"session_filter:{market.session}")

        spread_limit = self.config.spread_limit(signal.standard_symbol)
        if market.spread_points > spread_limit:
            reasons.append(
                f"spread_filter:{market.spread_points:.1f}>{spread_limit:.1f}"
            )

        slippage_limit = self.config.slippage_limit(signal.standard_symbol)
        if signal.entry_type != "LIMIT" and plan.estimated_slippage_points > slippage_limit:
            reasons.append(
                f"slippage_filter:{plan.estimated_slippage_points:.1f}>{slippage_limit:.1f}"
            )

        if portfolio.daily_pnl <= -self.config.max_daily_loss_currency:
            reasons.append(f"daily_loss_guard:realized={portfolio.daily_pnl:.2f}")
        elif portfolio.combined_pnl <= -self.config.max_daily_loss_currency:
            reasons.append(f"daily_loss_guard:projected={portfolio.combined_pnl:.2f}")

        if portfolio.drawdown_pct >= self.config.max_drawdown_pct:
            reasons.append(
                f"drawdown_guard:{portfolio.drawdown_pct:.2f}%>={self.config.max_drawdown_pct:.2f}%"
            )

        if plan.rr < self.config.min_rr:
            reasons.append(f"rr_guard:{plan.rr:.2f}<{self.config.min_rr:.2f}")

        if performance.combined_expectancy_r < self.config.min_expectancy_r:
            reasons.append(
                f"expectancy_guard:{performance.combined_expectancy_r:.2f}R<{self.config.min_expectancy_r:.2f}R"
            )

        max_open_risk_usd = portfolio.equity * (self.config.max_open_risk_pct / 100.0)
        if (portfolio.open_risk_usd + plan.risk_usd) > max_open_risk_usd:
            reasons.append(
                f"open_risk_guard:{portfolio.open_risk_usd + plan.risk_usd:.2f}>{max_open_risk_usd:.2f}"
            )

        correlation_reason = self._correlation_reason(signal, portfolio, plan)
        if correlation_reason:
            reasons.append(correlation_reason)

        return reasons

    def _session_allowed(self, symbol: str, session: str) -> bool:
        if not session:
            return False
        normalized = str(session).upper()
        if normalized == "WEEKEND":
            return "BTC" in symbol.upper()
        allowed = self.config.sessions_for(symbol)
        tokens = {normalized}
        tokens.update(part.strip() for part in normalized.split("/") if part.strip())
        return bool(tokens & allowed)

    def _correlation_reason(
        self,
        signal: StrategySignal,
        portfolio: PortfolioSnapshot,
        plan: OrderPlan,
    ) -> str:
        group = self.config.correlation_group(signal.standard_symbol)
        same_direction = [
            exposure
            for exposure in portfolio.exposures
            if exposure.group == group and exposure.side == signal.side
        ]
        cross_symbol = [
            exposure
            for exposure in same_direction
            if exposure.standard_symbol != signal.standard_symbol
        ]
        if len(cross_symbol) >= self.config.same_direction_correlation_limit:
            return f"correlation_guard:{group}:{signal.side}"

        group_risk = sum(exposure.risk_usd for exposure in same_direction)
        max_group_risk = portfolio.equity * (self.config.max_group_risk_pct / 100.0)
        if (group_risk + plan.risk_usd) > max_group_risk:
            return f"correlation_risk_guard:{group}:{group_risk + plan.risk_usd:.2f}>{max_group_risk:.2f}"

        return ""
