#!/usr/bin/env python3
"""
Antigravity AI Trading System — Entry Point (Thin Runner).

run_bot.py เป็นแค่ entry point → เรียก uvicorn.run() เท่านั้น.
ทุก component init อยู่ใน main.py lifespan (Single Source of Truth):
    1. SQLite connect
    2. Brain MemoryStore connect
    3. Strategy Factory auto-register
    4. MT5 Client connect (auto-connect to running terminal)
    5. MasterLoop spawn (background task)

Default: DRY_RUN mode.
"""

import sys
import signal

import uvicorn

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def main() -> None:
    """Start Antigravity Trading System."""
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

    settings = get_settings()
    logger.info("startup_begin", extra={
        "mode": settings.trading_mode,
        "api_port": settings.api_port,
    })

    try:
        uvicorn.run(
            "app.api.main:app",
            host=settings.api_host,
            port=settings.api_port,
            log_level="warning",
            # Single worker — MasterLoop ต้องรันใน main process
            workers=1,
        )
    except KeyboardInterrupt:
        logger.info("shutdown_requested")
    except SystemExit:
        pass
    except Exception as e:
        logger.error("fatal_crash", extra={
            "error": str(e),
            "type": type(e).__name__,
        }, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
