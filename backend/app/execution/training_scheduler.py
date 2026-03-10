"""
Training Scheduler — แยกจาก MasterLoop training/coach methods.

จัดการ background tasks:
    - Self-training (via TrainingOrchestrator)
    - Shadow evaluation (via Trainer)
    - Coach analysis (via AutoCoach)
"""

from app.core.logging import get_logger

logger = get_logger(__name__)


class TrainingScheduler:
    """
    Manages periodic training / evaluation tasks in the background.

    Extracted from MasterLoop for better separation of concerns.
    """

    def __init__(self):
        self.training_orchestrator = None
        self.trainer = None
        self.auto_coach = None
        self._shadow_candle_cache: dict = {}

    def set_dependencies(self, **deps):
        for key, val in deps.items():
            setattr(self, key, val)

    async def run_self_training(self, cycle_count: int) -> None:
        """Run self-training session in background."""
        if not self.training_orchestrator:
            return

        try:
            logger.info("self_training_start", extra={"cycle": cycle_count})
            report = await self.training_orchestrator.run_training_session()
            logger.info("self_training_complete", extra={
                "session_id": report.session_id,
                "duration_s": report.duration_seconds,
                "symbols": len(report.symbols_trained),
                "strategies": report.strategies_tested,
            })
        except Exception as e:
            logger.error("self_training_error", extra={
                "error": str(e), "cycle": cycle_count,
            }, exc_info=True)

    async def run_shadow_evaluation(self) -> None:
        """Run shadow trade evaluation in background."""
        if not self.trainer:
            return
        try:
            result = await self.trainer.run_shadow_training_cycle(
                candles_by_symbol=self._shadow_candle_cache
            )
            if result.get("evaluated", 0) > 0:
                logger.info("shadow_training_complete", extra=result)
        except Exception as e:
            logger.error("shadow_evaluation_error", extra={
                "error": str(e),
            }, exc_info=True)
