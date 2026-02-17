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


# ====================================================================
# Position Management Schemas
# ====================================================================

class ModifySLRequest(BaseModel):
    """Request สำหรับแก้ Stop Loss."""
    sl: float

class ModifyTPRequest(BaseModel):
    """Request สำหรับแก้ Take Profit."""
    tp: float

class TrailingStopRequest(BaseModel):
    """Request สำหรับตั้ง/ปิด trailing stop."""
    mode: str = "atr"              # "atr" | "fixed" | "off"
    atr_multiplier: float = 3.0
    fixed_points: float = 0.0
    activation_r: float = 1.0      # เริ่ม trail เมื่อ >= NR

class ProfitLockTierRequest(BaseModel):
    """Tier เดียวของ profit lock."""
    r: float           # R multiple (เช่น 1.0 = 1R)
    lock_pct: float    # เปอร์เซ็นต์ที่ล็อค (0.0 = BE, 0.5 = 50%)

class ProfitLockRequest(BaseModel):
    """Request สำหรับตั้ง profit lock."""
    tiers: list[ProfitLockTierRequest]

class GhostGuardRequest(BaseModel):
    """Request สำหรับตั้ง Ghost Guard (Virtual SL/TP ซ่อนจากโบรกเกอร์)."""
    virtual_sl: float = 0.0    # Virtual Stop Loss (0 = ไม่ใช้)
    virtual_tp: float = 0.0    # Virtual Take Profit (0 = ไม่ใช้)

class PositionResponse(BaseModel):
    """Response สำหรับ position ที่เปิดอยู่."""
    ticket: int
    symbol: str
    type: str
    volume: float
    entry_price: float
    current_price: float
    sl: float
    tp: float
    profit: float
    magic: int = -1
    trailing_active: bool = False
    trailing_mode: str = "off"
    profit_lock_tier: int = -1
    ghost_active: bool = False
    ghost_sl: float = 0.0
    ghost_tp: float = 0.0
