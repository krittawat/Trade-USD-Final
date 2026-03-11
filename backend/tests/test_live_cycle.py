import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from backend.trader.services.live_cycle import handle_safety_pause, run_live_position_management


class _StubLogger:
    def __init__(self):
        self.info_lines = []
        self.error_lines = []

    def info(self, message):
        self.info_lines.append(message)

    def error(self, message, exc_info=False):
        self.error_lines.append((message, exc_info))


def test_run_live_position_management_skips_non_live_mode():
    calls = []
    logger = _StubLogger()

    result = run_live_position_management(
        mode="dry_run",
        manage_positions=lambda: calls.append("managed"),
        logger=logger,
    )

    assert result is None
    assert calls == []
    assert logger.info_lines == []


def test_handle_safety_pause_runs_position_manager_during_news_block():
    calls = []
    logger = _StubLogger()

    paused = handle_safety_pause(
        mode="live",
        maintenance_blocked=False,
        maintenance_reason="",
        news_blocked=True,
        lead_symbol_news_safe=False,
        manage_positions=lambda: calls.append("managed") or 3,
        logger=logger,
        sleep_fn=lambda seconds: calls.append(("sleep", seconds)),
    )

    assert paused is True
    assert calls == ["managed", ("sleep", 60)]
    assert any("Positions managed during news block: 3" in line for line in logger.info_lines)
    assert any("NEWS BLOCK" in line for line in logger.info_lines)


def test_handle_safety_pause_does_not_pause_when_news_is_safe():
    calls = []
    logger = _StubLogger()

    paused = handle_safety_pause(
        mode="live",
        maintenance_blocked=False,
        maintenance_reason="",
        news_blocked=True,
        lead_symbol_news_safe=True,
        manage_positions=lambda: calls.append("managed") or 1,
        logger=logger,
        sleep_fn=lambda seconds: calls.append(("sleep", seconds)),
    )

    assert paused is False
    assert calls == []
