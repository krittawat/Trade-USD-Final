from __future__ import annotations

from backend.trader.data.mapper import mapper

from .config import EngineConfig
from .models import OpenExposure, PortfolioSnapshot


class PortfolioService:
    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self._peak_equity = 0.0

    def observe_equity(self, equity: float, balance: float = 0.0) -> None:
        baseline = max(float(equity or 0.0), float(balance or 0.0))
        if baseline > self._peak_equity:
            self._peak_equity = baseline

    def snapshot(self, account_state: dict, mt5_module=None) -> PortfolioSnapshot:
        balance = float(account_state.get("balance", 0.0) or 0.0)
        equity = float(account_state.get("equity", 0.0) or 0.0)
        daily_pnl = float(account_state.get("daily_pnl", 0.0) or 0.0)
        margin = float(account_state.get("margin", 0.0) or 0.0)
        margin_free = float(account_state.get("margin_free", 0.0) or 0.0)

        self.observe_equity(equity, balance)

        floating_pnl = 0.0
        open_risk_usd = 0.0
        exposures: list[OpenExposure] = []

        positions = list(mt5_module.positions_get() or []) if mt5_module is not None else []
        for position in positions:
            symbol = str(getattr(position, "symbol", "") or "")
            standard_symbol = mapper.to_standard(symbol).upper()
            side = "BUY" if int(getattr(position, "type", 0) or 0) == 0 else "SELL"
            volume = float(getattr(position, "volume", 0.0) or 0.0)
            floating_pnl += float(getattr(position, "profit", 0.0) or 0.0)

            risk_usd = 0.0
            entry_price = float(getattr(position, "price_open", 0.0) or 0.0)
            stop_loss = float(getattr(position, "sl", 0.0) or 0.0)
            if entry_price > 0 and stop_loss > 0 and volume > 0 and mt5_module is not None:
                info = mt5_module.symbol_info(symbol)
                if info is not None:
                    tick_size = float(
                        getattr(info, "trade_tick_size", 0.0)
                        or getattr(info, "point", 0.0)
                        or 0.0
                    )
                    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
                    if tick_size > 0 and tick_value > 0:
                        risk_usd = abs(entry_price - stop_loss) / tick_size * tick_value * volume

            open_risk_usd += risk_usd
            exposures.append(
                OpenExposure(
                    symbol=symbol,
                    standard_symbol=standard_symbol,
                    group=self.config.correlation_group(standard_symbol),
                    side=side,
                    volume=volume,
                    risk_usd=max(0.0, risk_usd),
                )
            )

        peak_equity = max(self._peak_equity, equity, balance)
        drawdown_pct = 0.0
        if peak_equity > 0 and equity < peak_equity:
            drawdown_pct = ((peak_equity - equity) / peak_equity) * 100.0

        combined_pnl = daily_pnl + floating_pnl
        return PortfolioSnapshot(
            balance=balance,
            equity=equity,
            daily_pnl=daily_pnl,
            floating_pnl=floating_pnl,
            combined_pnl=combined_pnl,
            margin=margin,
            margin_free=margin_free,
            peak_equity=peak_equity,
            drawdown_pct=max(0.0, drawdown_pct),
            open_risk_usd=max(0.0, open_risk_usd),
            exposures=exposures,
        )
