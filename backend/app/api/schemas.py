"""
API Schemas — Pydantic request/response models สำหรับ API.

แยก schemas ออกจาก domain models เพื่อ:
    - API contract ชัดเจน
    - Domain models เปลี่ยนโดยไม่กระทบ API
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response จาก /api/health."""
    status: str
    mode: str
    version: str
    services: dict[str, str]


class SymbolResponse(BaseModel):
    """Response จาก /api/symbols/{symbol}."""
    symbol: str
    profile: Optional[dict] = None
    open_positions: int = 0
    max_positions: int = 2
    last_decision: Optional[dict] = None
    session_allowed: bool = True
    news_safe: bool = True


class DecisionResponse(BaseModel):
    """Response จาก /api/decisions."""
    symbol: str
    action: str
    confidence: float
    reason: str
    stage: str
    result: str
    timestamp: datetime


class MetricsResponse(BaseModel):
    """Response จาก /api/analytics/metrics."""
    symbol: str
    win_rate: float
    profit_factor: float
    max_drawdown: float
    expectancy: float
    avg_r: float
    sharpe_ratio: float
    total_trades: int
