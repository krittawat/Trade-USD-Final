"""
Typed Exceptions — No silent failures.

Every exception carries context for structured logging.
All exceptions inherit from TradingSystemError for catch-all safety.
"""


class TradingSystemError(Exception):
    """Base exception for all trading system errors."""

    def __init__(self, message: str, context: dict | None = None):
        super().__init__(message)
        self.context = context or {}


class ConfigError(TradingSystemError):
    """Invalid or missing configuration."""
    pass


class ConnectionError(TradingSystemError):
    """Failed to connect to external service (MT5, QuestDB, etc.)."""
    pass


class RiskViolation(TradingSystemError):
    """Risk engine blocked the trade. Reason is in context['reason']."""

    def __init__(self, reason: str, symbol: str = "", context: dict | None = None):
        ctx = context or {}
        ctx["reason"] = reason
        ctx["symbol"] = symbol
        super().__init__(f"Risk violation: {reason} [{symbol}]", ctx)
        self.reason = reason
        self.symbol = symbol


class OrderError(TradingSystemError):
    """Order placement/modification failed."""
    pass


class DataError(TradingSystemError):
    """Data fetch or validation error (candles, ticks, profiles)."""
    pass


class KillSwitchActivated(TradingSystemError):
    """Kill switch has been triggered — halt all order sending."""
    pass
