import time
from typing import Callable


def run_live_position_management(
    *,
    mode: str,
    manage_positions: Callable[[], int],
    logger,
    reason: str = "",
) -> int | None:
    if str(mode or "").lower() != "live":
        return None

    try:
        managed = manage_positions()
        suffix = f" during {reason}" if reason else ""
        logger.info(
            f"  🛡️ Positions managed{suffix}: {managed if isinstance(managed, int) else '?'}"
        )
        return managed if isinstance(managed, int) else None
    except Exception as exc:
        suffix = f" during {reason}" if reason else ""
        logger.error(f"Position manager error{suffix}: {exc}", exc_info=True)
        return None


def handle_safety_pause(
    *,
    mode: str,
    maintenance_blocked: bool,
    maintenance_reason: str,
    news_blocked: bool,
    lead_symbol_news_safe: bool,
    allow_news_cycle: bool = False,
    manage_positions: Callable[[], int],
    logger,
    sleep_fn: Callable[[float], None] = time.sleep,
    sleep_seconds: int = 60,
) -> bool:
    if maintenance_blocked:
        run_live_position_management(
            mode=mode,
            manage_positions=manage_positions,
            logger=logger,
            reason="maintenance block",
        )
        logger.info(f"  🕒 MAINTENANCE: {maintenance_reason} — รอ {sleep_seconds} วินาที")
        sleep_fn(sleep_seconds)
        return True

    if news_blocked and not lead_symbol_news_safe and not allow_news_cycle:
        run_live_position_management(
            mode=mode,
            manage_positions=manage_positions,
            logger=logger,
            reason="news block",
        )
        logger.info(f"  🏛️ NEWS BLOCK — รอ {sleep_seconds} วินาที")
        sleep_fn(sleep_seconds)
        return True

    return False
