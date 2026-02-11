"""
Antigravity AI Trading System — Master Loop.

The main async loop that:
    1. Fetches active symbols from QuestDB profiles
    2. For each symbol: runs the execution pipeline (signal → gate → risk → execute)
    3. Manages position health checks (SL verify, break-even, DD guard)
    4. Respects kill-switch and mode transitions
    5. Logs every cycle with structured JSON

Design:
    - Single loop serves all modes (LIVE/DRY/REPLAY/BACKTEST)
    - No unbounded buffers — symbols processed sequentially or in bounded batch
    - Graceful shutdown on SIGINT/SIGTERM
    - Health checks emitted every N cycles
"""

import asyncio
from datetime import datetime, timezone

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class MasterLoop:
    """Main trading loop — orchestrates the execution pipeline per symbol."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.running = False
        self.kill_switch = False
        self.cycle_count = 0

    async def run(self) -> None:
        """Start the main loop. Runs until stopped or kill-switch triggered."""
        self.running = True
        logger.info("master_loop_started", extra={"mode": self.settings.trading_mode})

        try:
            while self.running and not self.kill_switch:
                await self._run_cycle()
                await asyncio.sleep(1.0)  # Configurable tick interval
        except asyncio.CancelledError:
            logger.info("master_loop_cancelled")
        finally:
            await self._shutdown()

    async def _run_cycle(self) -> None:
        """Execute one full cycle: fetch symbols → pipeline per symbol → health checks."""
        self.cycle_count += 1
        cycle_start = datetime.now(timezone.utc)

        # --- 1. Fetch active symbols from QuestDB ---
        # symbols = await self.profile_repo.get_active_symbols()
        symbols: list[str] = []  # Stub: populated from QuestDB later

        # --- 2. Run execution pipeline per symbol ---
        for symbol in symbols:
            if self.kill_switch:
                logger.warning("kill_switch_active", extra={"symbol": symbol})
                break

            try:
                await self._process_symbol(symbol)
            except Exception as e:
                # No silent exceptions — log with full context
                logger.error(
                    "symbol_processing_error",
                    extra={
                        "symbol": symbol,
                        "error": str(e),
                        "type": type(e).__name__,
                        "cycle": self.cycle_count,
                    },
                    exc_info=True,
                )

        # --- 3. Health check emission ---
        if self.cycle_count % 60 == 0:
            elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
            logger.info(
                "health_check",
                extra={
                    "cycle": self.cycle_count,
                    "symbols_count": len(symbols),
                    "elapsed_s": round(elapsed, 3),
                    "mode": self.settings.trading_mode,
                },
            )

    async def _process_symbol(self, symbol: str) -> None:
        """
        Run the full execution pipeline for one symbol.

        Pipeline: signal → gate → risk check → order plan → execute
        This is delegated to ExecutionPipeline (same logic for all modes).
        """
        # from app.execution.pipeline import ExecutionPipeline
        # pipeline = ExecutionPipeline(self.settings, symbol)
        # result = await pipeline.execute()
        logger.debug(
            "symbol_processed",
            extra={"symbol": symbol, "cycle": self.cycle_count},
        )

    async def _shutdown(self) -> None:
        """Graceful shutdown — close connections, flush logs."""
        self.running = False
        logger.info(
            "master_loop_shutdown",
            extra={"total_cycles": self.cycle_count},
        )

    def activate_kill_switch(self) -> None:
        """Immediately halt all order sending. Can be called from API or signal handler."""
        self.kill_switch = True
        logger.critical(
            "kill_switch_activated",
            extra={"cycle": self.cycle_count},
        )
