from __future__ import annotations

from .models import ExecutionReport, OrderPlan, StrategySignal


class ExecutionService:
    def execute(self, executor, signal: StrategySignal, plan: OrderPlan) -> ExecutionReport:
        payload = plan.to_signal_payload(signal)
        raw_result = executor.place_order(payload, lot_size=plan.lot_size)
        if not isinstance(raw_result, dict):
            return ExecutionReport(status="error", reason="executor_return_invalid", raw={})

        status = str(raw_result.get("status", "error"))
        reason = str(raw_result.get("reason", "") or "")
        trade = raw_result.get("trade") if isinstance(raw_result.get("trade"), dict) else {}
        fill_price = trade.get("entry_price")
        ticket = trade.get("ticket")

        slippage_points = 0.0
        if fill_price is not None and plan.point_size > 0:
            slippage_points = abs(float(fill_price) - plan.entry_price) / plan.point_size

        return ExecutionReport(
            status=status,
            reason=reason,
            ticket=int(ticket) if ticket is not None else None,
            fill_price=float(fill_price) if fill_price is not None else None,
            slippage_points=float(slippage_points),
            raw=raw_result,
        )
