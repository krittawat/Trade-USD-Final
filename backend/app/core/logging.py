"""
Structured Logger — Pretty console + JSON file.

Console: สี + compact one-liner → อ่านง่ายตอน monitor
File:    JSON Lines → machine-parseable สำหรับ analytics

Every log entry includes:
    ts, level, symbol, mode, stage, result, reason, metrics

Design:
    - Uses Python stdlib logging with dual formatters
    - No silent exceptions — all errors get full stacktrace + context
    - Thread-safe for async usage
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any


# ─── ANSI Colors ───
_COLORS = {
    "RESET":   "\033[0m",
    "DIM":     "\033[2m",
    "BOLD":    "\033[1m",
    "RED":     "\033[91m",
    "GREEN":   "\033[92m",
    "YELLOW":  "\033[93m",
    "CYAN":    "\033[96m",
    "MAGENTA": "\033[95m",
    "BLUE":    "\033[94m",
    "WHITE":   "\033[97m",
    "GRAY":    "\033[90m",
}

# Disable colors if not a real terminal
if not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
    _COLORS = {k: "" for k in _COLORS}
else:
    # Enable ANSI on Windows
    if sys.platform == "win32":
        os.system("")  # triggers VT100 mode


# Level → (icon, color)
_LEVEL_STYLE = {
    "DEBUG":    ("~", _COLORS["GRAY"]),
    "INFO":     (">", _COLORS["CYAN"]),
    "WARNING":  ("!", _COLORS["YELLOW"]),
    "ERROR":    ("X", _COLORS["RED"]),
    "CRITICAL": ("*", _COLORS["RED"] + _COLORS["BOLD"]),
}

# Highlight certain extra keys
_HIGHLIGHT_KEYS = {"symbol", "action", "strategy", "stage", "result", "reason", "mode"}

# Keys to skip in console (reduce noise)
_SKIP_KEYS = {
    "name", "msg", "args", "created", "relativeCreated",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "pathname", "filename", "module", "levelno", "levelname",
    "msecs", "thread", "threadName", "processName", "process",
    "message", "taskName",
}


class PrettyConsoleFormatter(logging.Formatter):
    """
    Human-readable one-liner for terminal:
        09:01 ▶ pipeline_signal  XAUUSDc  BUY  conf=0.9  strat=test  [signal → ok]
    """

    def format(self, record: logging.LogRecord) -> str:
        C = _COLORS
        ts = datetime.now().strftime("%H:%M:%S")
        icon, level_color = _LEVEL_STYLE.get(record.levelname, ("?", C["WHITE"]))

        msg = record.getMessage()

        # Collect extras
        extras = {}
        for key, value in record.__dict__.items():
            if key not in _SKIP_KEYS:
                extras[key] = value

        # Build compact key=value pairs
        parts = []

        # Symbol first (bold)
        symbol = extras.pop("symbol", "")
        if symbol:
            parts.append(f"{C['BOLD']}{symbol}{C['RESET']}")

        # Action (colored)
        action = extras.pop("action", "")
        if action:
            act_color = C["GREEN"] if action == "BUY" else C["RED"] if action == "SELL" else C["GRAY"]
            parts.append(f"{act_color}{action}{C['RESET']}")

        # Stage → result (compact)
        stage = extras.pop("stage", "")
        result = extras.pop("result", "")
        if stage or result:
            sr = f"{stage}->{result}" if stage and result else (stage or result)
            result_color = C["GREEN"] if result == "ok" else C["RED"] if result in ("blocked", "error") else C["YELLOW"]
            parts.append(f"{C['DIM']}[{C['RESET']}{result_color}{sr}{C['RESET']}{C['DIM']}]{C['RESET']}")

        # Other important fields
        for key in ("confidence", "strategy", "reason", "mode", "lot", "risk_pct",
                     "risk_usd", "spread", "profit", "ticket", "multiplier",
                     "from", "to", "cycle", "error"):
            val = extras.pop(key, None)
            if val is not None:
                # Shorten key names
                short = {
                    "confidence": "conf", "strategy": "strat", "risk_pct": "risk%",
                    "risk_usd": "risk$", "multiplier": "mult",
                }.get(key, key)

                if isinstance(val, float):
                    val = f"{val:.2f}" if abs(val) < 100 else f"{val:.1f}"
                parts.append(f"{C['DIM']}{short}={C['RESET']}{val}")

        # Remaining extras (compact, dimmed)
        for key, val in list(extras.items())[:4]:
            if isinstance(val, (dict, list)):
                continue  # skip complex objects in console
            if isinstance(val, float):
                val = f"{val:.2f}"
            parts.append(f"{C['GRAY']}{key}={val}{C['RESET']}")

        # Assemble line
        detail = "  ".join(parts)
        line = f"{C['DIM']}{ts}{C['RESET']} {level_color}{icon}{C['RESET']} {C['BOLD']}{msg}{C['RESET']}"
        if detail:
            line += f"  {detail}"

        # Exception
        if record.exc_info and record.exc_info[1]:
            line += f"\n{C['RED']}{self.formatException(record.exc_info)}{C['RESET']}"

        return line


class JSONFormatter(logging.Formatter):
    """Formats log records as JSON Lines for file storage."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge extra fields
        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                if key not in _SKIP_KEYS:
                    log_entry[key] = value

        # Include exception info if present
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


_stream_handler = None
_file_handler = None

def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """
    Get a structured logger with pretty console + JSON file output.

    Usage:
        logger = get_logger(__name__)
        logger.info("trade_blocked", extra={"symbol": "XAUUSD", "reason": "SPREAD_GUARD"})

    Console output:
        09:01 ▶ trade_blocked  XAUUSD  [→blocked]  reason=SPREAD_GUARD
    File output:
        {"ts": "...", "level": "INFO", "message": "trade_blocked", "symbol": "XAUUSD", ...}
    """
    global _stream_handler, _file_handler
    
    logger = logging.getLogger(name)

    if not logger.handlers:
        if _stream_handler is None:
            # 1. Console Handler (Pretty formatted)
            # UTF-8 support for Windows terminal (fix for cp874/UnicodeEncodeError)
            if sys.platform == "win32":
                try:
                    if hasattr(sys.stdout, "reconfigure"):
                        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
                    if hasattr(sys.stderr, "reconfigure"):
                        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
                except Exception:
                    import io
                    if hasattr(sys.stdout, "buffer"):
                        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
                    if hasattr(sys.stderr, "buffer"):
                        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
            
            _stream_handler = logging.StreamHandler(sys.stdout)
            _stream_handler.setFormatter(PrettyConsoleFormatter())

            # 2. File Handler (JSON Lines for machine parsing)
            try:
                from logging.handlers import RotatingFileHandler
                from pathlib import Path
                
                # Use absolute path relative to this file (backend/app/core/logging.py)
                # We want backend/logs
                base_dir = Path(__file__).resolve().parent.parent.parent
                log_dir = base_dir / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                
                # Windows: Prevent rotation conflicts by appending PID
                # This is critical for uvicorn reloaders + multiprocessing
                pid = os.getpid()
                
                _file_handler = RotatingFileHandler(
                    log_dir / f"trade_{pid}.log",
                    maxBytes=10*1024*1024,  # 10MB
                    backupCount=5,
                    encoding="utf-8"
                )
                _file_handler.setFormatter(JSONFormatter())
            except Exception as e:
                # Fallback if file logging fails (e.g. permission)
                print(f"Failed to setup file logging: {e}", file=sys.stderr)

        if _stream_handler:
            logger.addHandler(_stream_handler)
        if _file_handler:
            logger.addHandler(_file_handler)

        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        logger.propagate = False

    return logger
