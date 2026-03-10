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
    DAILY_PROFIT_TARGET_REACHED = "DAILY_PROFIT_TARGET_REACHED" # กำไรวันนี้ถึงเป้าหมายแล้ว (ล็อกกำไร)
    CAPITAL_FLOOR_BREACH = "CAPITAL_FLOOR_BREACH"   # ทุนเหลือต่ำกว่า 90% ของยอดตั้งต้น
    MAX_POSITIONS = "MAX_POSITIONS"                  # เปิดครบ max positions ต่อ symbol แล้ว
    MAX_TOTAL_POSITIONS = "MAX_TOTAL_POSITIONS"      # เปิดครบ max positions รวมทุก symbol แล้ว
    MARGIN_UTILIZATION_HIGH = "MARGIN_UTILIZATION_HIGH"  # margin ใช้เกิน threshold
    NO_STOP_LOSS = "NO_STOP_LOSS"                   # ไม่มี SL — ห้ามเทรดเด็ดขาด
    SL_TOO_CLOSE = "SL_TOO_CLOSE"                   # SL ใกล้ราคาปัจจุบันเกินไป (ต่ำกว่า stop level)
    LOT_SIZE_INVALID = "LOT_SIZE_INVALID"           # lot size ไม่ถูกต้อง
    RISK_EXCEEDED = "RISK_EXCEEDED"                 # ความเสี่ยง USD เกิน 2% ของ equity
    KILL_SWITCH = "KILL_SWITCH"                     # kill switch ถูกเปิด — หยุดทุกอย่าง
    ROLLOVER_BLOCK = "ROLLOVER_BLOCK"               # อยู่ในช่วง rollover
    REGIME_NO_TRADE = "REGIME_NO_TRADE"             # ตลาด sideways/volatility ต่ำ — ห้ามเทรด
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"             # อยู่ระหว่าง cooldown หลังขาดทุน
    SESSION_MAX_TRADES = "SESSION_MAX_TRADES"       # เทรดครบ max ต่อ session แล้ว
    DAILY_MAX_TRADES = "DAILY_MAX_TRADES"           # เทรดครบ max ต่อวันแล้ว (Sniper Mode)
    LOSS_STREAK_HALT = "LOSS_STREAK_HALT"           # แพ้ 2+ ครั้งติดกัน → หยุดเทรด session
    STRATEGY_BLACKLISTED = "STRATEGY_BLACKLISTED"   # Strategy/Behavior ถูกบล็อก (e.g. Martingale)
    EQUITY_TOO_LOW = "EQUITY_TOO_LOW"               # เงินในพอร์ตเหลือน้อยกว่าขั้นต่ำที่กำหนด
    CORRELATED_EXPOSURE = "CORRELATED_EXPOSURE"     # มี Position ในสินทรัพย์ที่ Correlation สูงอยู่แล้ว
    MACRO_BIAS_BLOCKED = "MACRO_BIAS_BLOCKED"       # สวนทางกับทิศทางตัวเลขเศรษฐกิจ (Macro Bias)
    OPUS_KILL_SWITCH = "OPUS_KILL_SWITCH"           # OPUS Ghost Protocol kill switch (3 losses / DD / ATR extreme)


class MarketSession(str, Enum):
    """เซสชันตลาด — ใช้กรองว่าเทรดช่วงไหนได้."""
    ASIA = "ASIA"           # เอเชีย (Tokyo)
    LONDON = "LONDON"       # ลอนดอน
    NEW_YORK = "NEW_YORK"   # นิวยอร์ก
    OVERLAP = "OVERLAP"     # ช่วงเซสชันทับกัน (สภาพคล่องสูง)
    WEEKEND = "WEEKEND"     # วันหยุดเสาร์-อาทิตย์ (24/7 Crypto)
    CLOSED = "CLOSED"       # ตลาดปิด


class RegimeType(str, Enum):
    """ประเภทสภาวะตลาด — AI Brain ใช้จัดกลุ่มและเลือก strategy."""
    # ─── Directional (backward compat) ───
    TRENDING_UP = "TRENDING_UP"       # เทรนด์ขึ้น
    TRENDING_DOWN = "TRENDING_DOWN"   # เทรนด์ลง
    # ─── Trend Strength (Intelligence Core v2) ───
    STRONG_TREND = "STRONG_TREND"     # ADX>25, EMA50≠EMA200, ATR ขยาย
    WEAK_TREND = "WEAK_TREND"         # ADX 15-25
    # ─── Range / Volatility ───
    RANGING = "RANGING"               # ไซด์เวย์ / กรอบแคบ (ADX<15, BB แคบ)
    HIGH_VOLATILITY = "HIGH_VOLATILITY"  # ATR percentile > 80%
    LOW_VOLATILITY = "LOW_VOLATILITY"    # ATR percentile < 20%
    # ─── Breakout / Trap ───
    BREAKOUT = "BREAKOUT"             # Volume spike + ทะลุ BB + ATR ขยาย
    FAKEOUT = "FAKEOUT"               # สับขาหลอก / False Break
    NEWS_SPIKE = "NEWS_SPIKE"         # กระชากข่าว
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP" # กวาด Stop Loss (Smart Money Trap)
    # ─── Smart Money ───
    ACCUMULATION = "ACCUMULATION"     # สะสม/กระจาย (Smart Money Phase)
    DISTRIBUTION = "DISTRIBUTION"     # กระจาย (Smart Money Distribution)
    # ─── OPUS Ghost Protocol ───
    VOL_EXPANSION = "VOL_EXPANSION"   # ATR กำลังขยายตัว (Volatility Expansion)
    VOL_COMPRESSION = "VOL_COMPRESSION"  # ATR หดตัว (Volatility Compression / Squeeze)
    # ─── Fallback ───
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
