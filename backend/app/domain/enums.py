"""
Enums — ค่าคงที่ทั้งหมดของระบบ.

รวม enum สำหรับ:
    - Action (BUY/SELL/HOLD)
    - BlockReason (เหตุผลที่บล็อกเทรด, ต้องแสดงบน dashboard เสมอ)
    - MarketSession (ASIA/LONDON/NY/OVERLAP)
    - RegimeType (ประเภทสภาวะตลาด)
    - TradeStage (ขั้นตอนในไปป์ไลน์)
"""

from enum import Enum


class Action(str, Enum):
    """คำสั่งเทรด — BUY, SELL หรือ HOLD (ไม่ทำอะไร)."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"  # ไม่เปิดออเดอร์


class BlockReason(str, Enum):
    """
    เหตุผลที่ Risk Gate บล็อกเทรด.
    ทุกครั้งที่ไม่เทรด ต้องมีเหตุผลชัดเจน — ห้ามบล็อกเงียบ.
    """
    MT5_DISCONNECTED = "MT5_DISCONNECTED"           # MT5 ขาดการเชื่อมต่อ
    SYMBOL_NOT_TRADABLE = "SYMBOL_NOT_TRADABLE"     # สัญลักษณ์เทรดไม่ได้
    MARKET_CLOSED = "MARKET_CLOSED"                 # ตลาดปิด
    PROFILE_INCOMPLETE = "PROFILE_INCOMPLETE"       # โปรไฟล์สัญลักษณ์ไม่ครบ
    SPREAD_GUARD = "SPREAD_GUARD"                   # สเปรดกว้างเกินไป
    SESSION_BLOCKED = "SESSION_BLOCKED"             # ไม่อยู่ในเซสชันที่อนุญาต
    NEWS_BLOCK = "NEWS_BLOCK"                       # อยู่ในช่วงข่าวสำคัญ (±30 นาที)
    FLOATING_DD_EXCEEDED = "FLOATING_DD_EXCEEDED"   # ขาดทุนลอยเกิน 10%
    DAILY_LOSS_EXCEEDED = "DAILY_LOSS_EXCEEDED"     # ขาดทุนวันนี้เกินลิมิต
    CAPITAL_FLOOR_BREACH = "CAPITAL_FLOOR_BREACH"   # ทุนเหลือต่ำกว่า 90% ของยอดตั้งต้น
    MAX_POSITIONS = "MAX_POSITIONS"                  # เปิดครบ max positions แล้ว
    NO_STOP_LOSS = "NO_STOP_LOSS"                   # ไม่มี SL — ห้ามเทรดเด็ดขาด
    LOT_SIZE_INVALID = "LOT_SIZE_INVALID"           # lot size ไม่ถูกต้อง
    RISK_EXCEEDED = "RISK_EXCEEDED"                 # ความเสี่ยง USD เกิน 2% ของ equity
    KILL_SWITCH = "KILL_SWITCH"                     # kill switch ถูกเปิด — หยุดทุกอย่าง
    ROLLOVER_BLOCK = "ROLLOVER_BLOCK"               # อยู่ในช่วง rollover


class MarketSession(str, Enum):
    """เซสชันตลาด — ใช้กรองว่าเทรดช่วงไหนได้."""
    ASIA = "ASIA"           # เอเชีย (Tokyo)
    LONDON = "LONDON"       # ลอนดอน
    NEW_YORK = "NEW_YORK"   # นิวยอร์ก
    OVERLAP = "OVERLAP"     # ช่วงเซสชันทับกัน (สภาพคล่องสูง)
    CLOSED = "CLOSED"       # ตลาดปิด


class RegimeType(str, Enum):
    """ประเภทสภาวะตลาด — AI Brain ใช้จัดกลุ่มและเลือก strategy."""
    TRENDING_UP = "TRENDING_UP"       # เทรนด์ขึ้น
    TRENDING_DOWN = "TRENDING_DOWN"   # เทรนด์ลง
    RANGING = "RANGING"               # ไซด์เวย์ / กรอบแคบ
    HIGH_VOLATILITY = "HIGH_VOLATILITY"  # ความผันผวนสูง
    LOW_VOLATILITY = "LOW_VOLATILITY"    # ความผันผวนต่ำ
    UNKNOWN = "UNKNOWN"               # ยังวิเคราะห์ไม่ได้


class TradeStage(str, Enum):
    """ขั้นตอนใน execution pipeline — ใช้ใน structured log."""
    SIGNAL = "signal"       # สัญญาณจาก strategy
    GATE = "gate"           # Pre-Trade Gate ตรวจสอบ
    RISK = "risk"           # คำนวณ lot size และความเสี่ยง
    ORDER = "order"         # ส่งคำสั่งเทรด
    POSTFILL = "postfill"   # ตรวจ SL หลังเปิดออเดอร์
    BE_MOVE = "be_move"     # ย้าย SL ไป Break-Even
    CLOSE = "close"         # ปิดออเดอร์
