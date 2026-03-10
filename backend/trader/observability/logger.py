"""
ANTIGRAVITY — Structured Logger with Beautiful Console Output
=============================================================
File logs  → JSON Lines (machine-readable)
Console    → Compact dashboard view (human-readable)
"""
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Logging directory ────────────────────────────────────────
LOG_DIR = Path("d:/VibeCode/Trade/trader/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Color codes for Windows terminal (ANSI) ──────────────────
class C:
    """ANSI color shortcuts."""
    RST   = "\033[0m"
    BOLD  = "\033[1m"
    DIM   = "\033[2m"
    # Foreground
    RED   = "\033[91m"
    GREEN = "\033[92m"
    YELLOW= "\033[93m"
    BLUE  = "\033[94m"
    CYAN  = "\033[96m"
    WHITE = "\033[97m"
    GRAY  = "\033[90m"
    MAGENTA = "\033[95m"


# ── JSON Formatter (for file logs) ──────────────────────────
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        for attr in ("reason", "metrics", "symbol", "mode"):
            if hasattr(record, attr):
                log_record[attr] = getattr(record, attr)
        return json.dumps(log_record)


# ── Pretty Console Formatter ────────────────────────────────
class PrettyConsoleFormatter(logging.Formatter):
    """
    Compact, color-coded console output.
    Shows only HH:MM:SS timestamp + message.
    Level is encoded by color, not text.
    """
    LEVEL_STYLE = {
        "DEBUG":    C.GRAY,
        "INFO":     C.WHITE,
        "WARNING":  C.YELLOW,
        "ERROR":    C.RED + C.BOLD,
        "CRITICAL": C.RED + C.BOLD,
    }

    def format(self, record):
        ts = datetime.now().strftime("%H:%M:%S")
        color = self.LEVEL_STYLE.get(record.levelname, C.WHITE)
        msg = record.getMessage()

        # Error/Warning get explicit prefix
        if record.levelname in ("ERROR", "CRITICAL"):
            prefix = f"{C.RED}✖ ERROR{C.RST} "
        elif record.levelname == "WARNING":
            prefix = f"{C.YELLOW}⚠ WARN{C.RST}  "
        else:
            prefix = ""

        return f"{C.DIM}{ts}{C.RST} {prefix}{color}{msg}{C.RST}"


# ── Logger Setup ────────────────────────────────────────────
def setup_logger(name="opus_logger", log_file="trade.log"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers on re-import
    if not logger.handlers:
        # File handler: JSON lines (full detail)
        fh = logging.FileHandler(LOG_DIR / log_file, encoding="utf-8")
        fh.setFormatter(JSONFormatter())
        fh.setLevel(logging.DEBUG)

        # Console handler: pretty compact (UTF-8 for Windows emoji/Thai support)
        if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except Exception:
                pass
        
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(PrettyConsoleFormatter())
        ch.setLevel(logging.INFO)

        logger.addHandler(fh)
        logger.addHandler(ch)

    return logger


# Enable ANSI on Windows
if sys.platform == "win32":
    os.system("")  # enable VT100 escape sequences

logger = setup_logger()
