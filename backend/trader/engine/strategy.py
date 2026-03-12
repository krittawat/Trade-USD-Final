from __future__ import annotations

from backend.trader.data.mapper import mapper

from .models import StrategySignal


class StrategyService:
    def normalize_signal(self, raw_signal: dict | None, symbol: str, timeframe: str) -> StrategySignal | None:
        if not isinstance(raw_signal, dict):
            return None

        side = str(raw_signal.get("side", "") or "").upper()
        if side not in {"BUY", "SELL"}:
            return None

        model = str(raw_signal.get("model") or raw_signal.get("strategy") or "UNKNOWN").upper()
        rationale = raw_signal.get("rationale") or []
        if isinstance(rationale, str):
            rationale = [rationale]

        return StrategySignal(
            symbol=symbol,
            standard_symbol=mapper.to_standard(symbol).upper(),
            timeframe=str(timeframe or "").upper(),
            side=side,
            entry_price=float(raw_signal.get("entry_price", 0.0) or 0.0),
            confidence=float(raw_signal.get("confidence", 0.0) or 0.0),
            model=model,
            entry_type=str(raw_signal.get("entry_type", "MARKET") or "MARKET").upper(),
            rationale=[str(item) for item in rationale],
            raw=dict(raw_signal),
        )
