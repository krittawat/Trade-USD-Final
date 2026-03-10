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



class RegimeContext(BaseModel):
    """
    บริบทสภาวะตลาด (Unified Regime Context).
    รวมข้อมูลจากทุก indicator เพื่อบอกว่าตลาดเป็นแบบไหนและ 'เทรดได้ไหม'.
    """
    regime: RegimeType                 # ENUM: TRENDING_UP, RANGING, FAKEOUT, etc.
    actionable: bool = True            # True = เทรดได้, False = อันตราย (เช่น sideways/fakeout)
    score: float = 0.5                 # ความชัดเจนของ regime (0.0 - 1.0)
    reason: str = ""                   # เหตุผลประกอบ
    details: dict = Field(default_factory=dict) # ค่า indicator (adx, wick_ratio, etc.)


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
    risk_pct: Optional[float] = None           # ความเสี่ยงที่ strategy แนะนำ (ถ้ามี)
    strategy_name: str = ""                    # ชื่อ strategy ที่ใช้
    timeframe: str = "M5"                      # ไทม์เฟรม
    tags: list[str] = Field(default_factory=list)    # แท็กเพิ่มเติม
    debug: dict = Field(default_factory=dict)        # ข้อมูล debug (indicator values ฯลฯ)
    extra: dict = Field(default_factory=dict)        # Metadata เพิ่มเติม (เช่น be_activation_r)
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
    ENTRY_PRICE: Optional[float] = None
    strategy_name: str = ""
    comment: str = ""
    trailing_config: dict = Field(default_factory=dict)
    magic: Optional[int] = None
    ghost_protocol: bool = False  # Stealth mode (virtual SL/TP in memory)


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
    peak_equity: float = 0.0   # ยอด equity สูงสุดที่เคยทำได้ (สำหรับ Trailing Capital Floor)
    leverage: int = 0          # Account leverage (e.g. 500, 2000)
    
    # ─── Raw values from MT5 for Telegram display ───
    real_balance: float = 0.0
    real_equity: float = 0.0
    currency: str = "USD"



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


# ====================================================================
# Pattern Signal — สัญญาณจาก PatternDetector
# ====================================================================

class PatternSignal(BaseModel):
    """สัญญาณ pattern ที่ตรวจพบ จาก PatternDetector."""
    name: str                     # ชื่อ pattern เช่น "bullish_engulfing"
    direction: str                # "bullish" | "bearish" | "neutral"
    strength: float               # 0.0-1.0
    bar_index: int = -1
    details: dict = Field(default_factory=dict)


# ====================================================================
# Self-Training Models — ผลฝึกซ้อมและรายงานการฝึก
# ====================================================================

class PracticeResult(BaseModel):
    """
    ผลลัพธ์จากการฝึกซ้อม strategy บนข้อมูลอดีต.

    สร้างจาก PracticeEngine.run_practice() — ไม่ส่งออเดอร์จริง.
    ใช้เปรียบเทียบ strategies และเลือกตัวที่ดีที่สุด.
    """
    strategy_name: str               # ชื่อ strategy ที่ฝึก
    symbol: str                      # สัญลักษณ์ เช่น XAUUSDc
    params: dict = Field(default_factory=dict)  # parameters ที่ใช้
    total_trades: int = 0            # จำนวนเทรดทั้งหมด
    wins: int = 0                    # จำนวนชนะ
    losses: int = 0                  # จำนวนแพ้
    win_rate: float = 0.0            # อัตราชนะ (0.0 - 1.0)
    profit_factor: float = 0.0       # PF = total_profit / total_loss
    max_drawdown: float = 0.0        # Max DD (% of peak equity)
    total_r: float = 0.0             # ผลรวม R ทั้งหมด
    expectancy: float = 0.0          # กำไรที่คาดหวังต่อเทรด (R)
    avg_rr: float = 0.0              # R:R เฉลี่ย
    score: float = 0.0               # composite score: PF × WR × (1 - DD/100)
    candles_tested: int = 0          # จำนวนแท่งเทียนที่ทดสอบ
    regime: str = "UNKNOWN"          # สภาวะตลาดที่ทดสอบ
    session: str = ""                # session ตอนทดสอบ
    patterns_found: dict = Field(default_factory=dict)     # pattern → count
    pattern_win_rates: dict = Field(default_factory=dict)  # pattern → win_rate
    news_stats: dict = Field(default_factory=dict)         # news_context → {count, win_rate}
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TrainingReport(BaseModel):
    """
    รายงานผลการฝึกซ้อมรอบเดียว (Training Session).

    สร้างจาก TrainingOrchestrator.run_training_session().
    เก็บสรุปผลรวม: จำนวน symbols, strategies ที่ทดสอบ, ตัวที่ดีที่สุด.
    """
    session_id: str                  # ID ของ training session (UUID)
    started_at: datetime             # เวลาเริ่ม
    completed_at: datetime           # เวลาจบ
    duration_seconds: float = 0.0    # ระยะเวลาฝึก (วินาที)
    symbols_trained: list[str] = Field(default_factory=list)    # symbols ที่ฝึก
    strategies_tested: int = 0       # จำนวน strategies ที่ลอง
    best_performers: list[PracticeResult] = Field(default_factory=list)  # ผลดีที่สุด
    params_evolved: dict = Field(default_factory=dict)    # parameters ที่ evolve แล้ว
    improvements: dict = Field(default_factory=dict)      # ปรับปรุงจากรอบก่อน


class ExecutionResult(BaseModel):
    """
    ผลลัพธ์การส่งคำสั่ง (Execution Output).
    ใช้ return กลับมาจาก ExecutionAdapter.
    """
    ticket: int                  # Order ticket (Real or Paper)
    price: float                 # Build/Fill price
    volume: float                # Fill volume
    retcode: int = 0             # MT5 retcode or 0 for OK
    comment: str = ""            # Broker comment
    error: Optional[str] = None  # Error message if failed
    mode: str = "LIVE"           # Execution mode


# ====================================================================
# Regime Intelligence — ผลวิเคราะห์สภาวะตลาดแบบครบวงจร
# ====================================================================

class RegimeIntelligence(BaseModel):
    """
    ผลวิเคราะห์สภาวะตลาดแบบ Intelligence Core.

    รวมทุกอย่าง: regime, confidence, strategy แนะนำ, risk profile,
    และเหตุผล — ส่งให้ pipeline ตัดสินใจเทรด.
    """
    symbol: str                                         # สัญลักษณ์ เช่น BTCUSD
    regime: RegimeType = RegimeType.UNKNOWN              # สภาวะตลาดหลัก
    direction: RegimeType = RegimeType.UNKNOWN           # ทิศทาง (TRENDING_UP/DOWN) สำหรับ compat
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)  # ความมั่นใจ 0-1
    recommended_strategy: str = ""                       # ชื่อ strategy แนะนำ
    risk_profile: dict = Field(default_factory=dict)     # {lot_multiplier, sl_atr, tp_rr}
    trade_allowed: bool = True                           # False = ห้ามเทรด
    reason: str = ""                                     # เหตุผลประกอบ
    details: dict = Field(default_factory=dict)          # snapshot ค่า indicator


