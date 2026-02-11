"""
Trading Mode — LIVE / DRY_RUN / REPLAY / BACKTEST.

Default is always DRY_RUN. LIVE requires explicit enable after QC passes.
The execution pipeline reads this to decide whether to send real orders.
"""

from enum import Enum


class TradingMode(str, Enum):
    """Operating mode of the trading system."""

    LIVE = "LIVE"          # Real orders — requires explicit enable
    DRY_RUN = "DRY_RUN"   # Default — logs decisions, no real orders
    REPLAY = "REPLAY"     # Streams historical data through live pipeline
    BACKTEST = "BACKTEST"  # Batch historical analysis via DuckDB

    @property
    def is_live(self) -> bool:
        return self == TradingMode.LIVE

    @property
    def can_send_orders(self) -> bool:
        return self == TradingMode.LIVE

    @property
    def needs_mt5(self) -> bool:
        return self in (TradingMode.LIVE, TradingMode.DRY_RUN)
