"""
Structured JSON Logger — JSON Lines format.

Every log entry includes:
    ts, level, symbol, mode, stage, result, reason, metrics

Design:
    - Uses Python stdlib logging with a JSON formatter
    - No silent exceptions — all errors get full stacktrace + context
    - Thread-safe for async usage
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """Formats log records as JSON Lines for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge extra fields (symbol, mode, stage, result, reason, metrics, etc.)
        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                if key not in (
                    "name", "msg", "args", "created", "relativeCreated",
                    "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                    "pathname", "filename", "module", "levelno", "levelname",
                    "msecs", "thread", "threadName", "processName", "process",
                    "message", "taskName",
                ):
                    log_entry[key] = value

        # Include exception info if present
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """
    Get a structured JSON logger.

    Usage:
        logger = get_logger(__name__)
        logger.info("trade_blocked", extra={"symbol": "XAUUSD", "reason": "SPREAD_GUARD"})
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        logger.propagate = False

    return logger
