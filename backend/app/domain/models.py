"""
Domain Models — โมเดลข้อมูลหลักของระบบ (Pydantic typed models).

ทุกโมเดลใช้ Pydantic เพื่อ:
    - Validate ข้อมูลอัตโนมัติ
    - Serialize เป็น JSON สำหรับ API/log
    - Type safety ตลอดทั้งไปป์ไลน์

โมเดลหลัก:
    - Decision: ผลลัพธ์จาก strategy (BUY/SELL/HOLD + เหตุผล)
    - OrderPlan: แผนคำสั่งเทรดที่ผ่าน risk gate แล้ว
    - SymbolProfile: ข้อมูลสัญลักษณ์จาก QuestDB
    - AccountState: สถานะบัญชีปัจจุบัน
    - TradeRecord: บันทึกเทรดสำหรับ journal
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.domain.enums import Action, BlockReason, MarketSession, RegimeType


class Decision(BaseModel):
    """
    ผลลัพธ์จาก Strategy — บอกว่าจะทำอะไร.
    
    Strategy สร้าง Decision แล้วส่งไปที่ execution pipeline.
    Strategy ห้ามส่งออเดอร์เอง — ต้องผ่าน risk gate เสมอ.
    """
    symbol: str                                # สัญลักษณ์เทรด เช่น XAUUSD
    action: Action                             # BUY / SELL / HOLD
    confidence: float = Field(ge=0.0, le=1.0)  # ความมั่นใจ 0.0 - 1.0
    reason: str                                # เหตุผลที่ตัดสินใจ (ภาษาคน)
    stop_loss: Optional[float] = None          # ราคา SL (ต้องมีถ้า BUY/SELL)
    take_profit: Optional[float] = None        # ราคา TP
    risk_reward_ratio: Optional[float] = None  # อัตราส่วน R:R
    strategy_name: str = ""                    # ชื่อ strategy ที่ใช้
    timeframe: str = "M5"                      # ไทม์เฟรม
    tags: list[str] = Field(default_factory=list)    # แท็กเพิ่มเติม
    debug: dict = Field(default_factory=dict)        # ข้อมูล debug (indicator values ฯลฯ)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class OrderPlan(BaseModel):
    """
    แผนคำสั่งเทรด — สร้างหลังผ่าน risk gate.
    
    ประกอบด้วย lot size ที่คำนวณแล้ว, SL/TP ที่ตรวจสอบแล้ว,
    และความเสี่ยง USD จริง.
    """
    symbol: str                    # สัญลักษณ์เทรด
    action: Action                 # BUY / SELL
    lot_size: float                # lot size ที่คำนวณและปัดเศษแล้ว
    stop_loss: float               # ราคา SL (ต้องมีเสมอ)
    take_profit: Optional[float]   # ราคา TP
    risk_usd: float                # ความเสี่ยงจริงเป็น USD
    risk_pct: float                # ความเสี่ยงเป็น % ของ equity
    entry_price: Optional[float] = None  # ราคาเข้า (สำหรับ limit order)
    strategy_name: str = ""
    comment: str = ""              # comment ที่จะใส่ในออเดอร์ MT5


class GateResult(BaseModel):
    """
    ผลลัพธ์จาก Pre-Trade Gate.
    
    passed=True = ผ่านทุกด่าน, สามารถเทรดได้.
    passed=False = บล็อก, ต้องแสดงเหตุผลบน dashboard.
    """
    passed: bool                             # ผ่านหรือไม่ผ่าน
    reasons: list[BlockReason] = Field(default_factory=list)  # เหตุผลที่บล็อก (อาจมีหลายข้อ)
    details: dict = Field(default_factory=dict)  # รายละเอียดเพิ่มเติม เช่น spread ปัจจุบัน


class SymbolProfile(BaseModel):
    """
    โปรไฟล์สัญลักษณ์เทรด — โหลดจาก QuestDB.
    
    มีข้อมูลที่ต้องใช้ในการคำนวณ lot size, ตรวจสอบ spread,
    และเลือก strategy.
    """
    symbol: str                        # ชื่อสัญลักษณ์ เช่น XAUUSD
    contract_size: float = 100.0       # ขนาดสัญญา
    volume_min: float = 0.01           # lot ต่ำสุด
    volume_max: float = 100.0          # lot สูงสุด
    volume_step: float = 0.01          # ขั้นต่ำของ lot
    point: float = 0.01               # ค่า point
    digits: int = 2                    # ทศนิยม
    spread_avg: float = 0.0           # สเปรดเฉลี่ย
    spread_max_allowed: float = 50.0  # สเปรดสูงสุดที่ยอมรับ
    sessions_allowed: list[str] = Field(default_factory=list)  # เซสชันที่เทรดได้
    is_active: bool = True             # เปิดใช้งานหรือไม่


class AccountState(BaseModel):
    """
    สถานะบัญชีปัจจุบัน — ใช้คำนวณ risk.
    
    อัปเดตทุกรอบของ master loop.
    """
    balance: float              # ยอดเงินในบัญชี
    equity: float               # equity ปัจจุบัน (รวมกำไร/ขาดทุนลอย)
    margin: float = 0.0        # margin ที่ใช้อยู่
    free_margin: float = 0.0   # margin ที่เหลือ
    floating_pl: float = 0.0   # กำไร/ขาดทุนลอยรวม
    open_positions: int = 0    # จำนวนออเดอร์ที่เปิดอยู่
    daily_pl: float = 0.0      # กำไร/ขาดทุนวันนี้
    initial_balance: float = 0.0  # ยอดเงินตั้งต้น (สำหรับคำนวณ capital floor)


class TradeRecord(BaseModel):
    """
    บันทึกเทรด — เก็บใน SQLite trade journal.
    
    บันทึกทุกอย่าง: entry, exit, กำไร/ขาดทุน, strategy, เหตุผล.
    """
    id: Optional[int] = None
    symbol: str
    action: Action
    lot_size: float
    entry_price: float
    exit_price: Optional[float] = None
    stop_loss: float
    take_profit: Optional[float] = None
    risk_usd: float
    profit_usd: Optional[float] = None
    strategy_name: str = ""
    entry_time: datetime = Field(default_factory=datetime.utcnow)
    exit_time: Optional[datetime] = None
    regime: RegimeType = RegimeType.UNKNOWN
    session: MarketSession = MarketSession.CLOSED
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
