from fastapi import APIRouter, Request
from app.core.logging import get_logger
from pathlib import Path

logger = get_logger(__name__)
router = APIRouter()

# Absolute path to coach report (same as master_loop uses)
_REPORT_PATH = Path(__file__).resolve().parents[3] / "logs" / "coach_report.md"

@router.get("/report")
async def get_coach_report(request: Request):
    """Get the latest Auto Coach analysis report."""
    # 1. Try in-memory from MasterLoop (Fastest)
    master_loop = getattr(request.app.state, "master_loop", None)
    if master_loop and getattr(master_loop, "latest_coach_report", None):
        return master_loop.latest_coach_report

    # 2. Try to read generated markdown
    try:
        if _REPORT_PATH.exists():
            return {"report_markdown": _REPORT_PATH.read_text(encoding="utf-8")}
    except Exception as e:
        logger.error("api_coach_read_error", extra={"error": str(e)})
        
    return {"status": "No report available yet"}
