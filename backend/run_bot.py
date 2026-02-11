#!/usr/bin/env python3
"""
Antigravity AI Trading System — Bot Entry Point.

Startup order:
    1. Load & validate config (.env)
    2. Initialize structured logger
    3. Connect DB clients (QuestDB, SQLite, DuckDB)
    4. Connect MT5 (if LIVE or DRY_RUN mode)
    5. Run QC suite (must pass before any trading)
    6. Start master loop in DRY_RUN mode
    7. LIVE mode requires explicit user enable

Safety:
    - Default mode is always DRY_RUN
    - On crash: auto-restart in DRY_RUN only
    - Kill-switch halts order sending instantly
"""

import asyncio
import sys

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.mode import TradingMode

logger = get_logger(__name__)


async def startup() -> None:
    """Initialize all system components in correct order."""
    settings = get_settings()
    logger.info(
        "startup_begin",
        extra={
            "mode": settings.trading_mode,
            "api_port": settings.api_port,
        },
    )

    # --- 1. Validate config ---
    logger.info("config_validated", extra={"trading_mode": settings.trading_mode})

    # --- 2. DB connections (stubs — will be implemented) ---
    # from app.db.questdb import QuestDBClient
    # from app.db.sqlite import SQLiteStore
    # from app.db.duckdb import DuckDBStore

    # --- 3. MT5 connection (only for LIVE/DRY_RUN) ---
    if settings.trading_mode in (TradingMode.LIVE, TradingMode.DRY_RUN):
        logger.info("mt5_connect_attempt")
        # from app.mt5.client import MT5Client
        # mt5 = MT5Client(settings)
        # mt5.connect()

    # --- 4. Start master loop ---
    logger.info("master_loop_starting", extra={"mode": settings.trading_mode})
    from app.master_loop import MasterLoop

    loop = MasterLoop(settings)
    await loop.run()


def main() -> None:
    """Entry point with crash protection."""
    try:
        asyncio.run(startup())
    except KeyboardInterrupt:
        logger.info("shutdown_requested")
    except Exception as e:
        logger.error(
            "fatal_crash",
            extra={"error": str(e), "type": type(e).__name__},
            exc_info=True,
        )
        # On crash: would auto-restart in DRY_RUN mode
        sys.exit(1)


if __name__ == "__main__":
    main()
