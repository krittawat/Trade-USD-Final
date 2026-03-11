import logging
import time

import MetaTrader5 as mt5


logger = logging.getLogger("opus_logger")
_LAST_LOGGED_BY_CONTEXT: dict[str, float] = {}
_THROTTLE_SECONDS = 30.0


def get_trade_block_reason() -> str | None:
    info = mt5.terminal_info()
    if info is None:
        return "MT5 terminal info unavailable"
    if getattr(info, "tradeapi_disabled", False):
        return "MT5 blocks trading via external Python API (tradeapi_disabled=True)"
    if not getattr(info, "trade_allowed", True):
        return "MT5 Algo Trading is OFF in terminal (trade_allowed=False)"
    return None


def log_trade_block(context: str, level: str = "error", throttle_seconds: float = _THROTTLE_SECONDS) -> str | None:
    reason = get_trade_block_reason()
    if not reason:
        return None

    now = time.time()
    last_logged = _LAST_LOGGED_BY_CONTEXT.get(context, 0.0)
    if throttle_seconds > 0 and now - last_logged < throttle_seconds:
        return reason

    _LAST_LOGGED_BY_CONTEXT[context] = now
    log_line = f"MT5 trade action blocked during {context}: {reason}"
    getattr(logger, level, logger.error)(log_line)
    return reason
