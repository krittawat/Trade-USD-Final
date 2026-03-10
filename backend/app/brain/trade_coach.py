"""
Trade Coach — เรียนรู้จากทุกไม้ที่ปิดแล้ว

วิเคราะห์ trade journal แล้วสร้าง "lessons":
    1. Pattern ที่แพ้ซ้ำ 3 ครั้ง → บล็อกชั่วคราว (30 นาที)
    2. Session ที่แพ้บ่อย → ลด confidence
    3. Regime ที่แพ้บ่อย → ลด confidence
    4. Strategy ที่ทำกำไร → เพิ่ม confidence

ใช้ใน SymbolProcessor ก่อนตัดสินใจเทรด
"""

import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════
# ค่าตั้ง
# ═══════════════════════════════════════════════
BLOCK_AFTER_N_LOSSES = 3        # บล็อกหลังแพ้ N ไม้ติดในเงื่อนไขเดียวกัน
BLOCK_DURATION_MINUTES = 30     # บล็อกนานกี่นาที
LOOKBACK_TRADES = 50            # วิเคราะห์กี่ไม้ล่าสุด
MIN_TRADES_FOR_LESSON = 5      # ต้องมีอย่างน้อย N ไม้ถึงจะสรุป lesson


@dataclass
class CoachLesson:
    """บทเรียนจากการวิเคราะห์ไม้ที่ผ่านมา"""
    key: str                     # เช่น "XAUUSDc:BUY:ASIA:RANGING"
    lesson_type: str             # "block" | "reduce" | "boost"
    reason: str                  # เหตุผลอธิบาย
    confidence_adj: float = 0.0  # ปรับ confidence (+/-)
    blocked_until: str = ""      # ISO timestamp ถ้าถูกบล็อก
    win_rate: float = 0.0        # อัตราชนะของ combo นี้
    sample_size: int = 0         # จำนวนไม้ที่ใช้วิเคราะห์


class TradeCoach:
    """
    วิเคราะห์ประวัติเทรด แล้วสร้าง actionable lessons
    สำหรับ SymbolProcessor ก่อนเข้าเทรด
    """

    def __init__(self, db=None):
        self._db = db
        self._lessons: dict[str, CoachLesson] = {}
        self._last_analysis: datetime | None = None
        self._analysis_interval = timedelta(minutes=10)  # วิเคราะห์ทุก 10 นาที

    def analyze(self, force: bool = False) -> None:
        """วิเคราะห์ trade journal แล้วสร้าง lessons ใหม่"""
        now = datetime.now(timezone.utc)

        # ไม่ต้องวิเคราะห์บ่อย — ทุก 10 นาทีพอ
        if not force and self._last_analysis:
            if now - self._last_analysis < self._analysis_interval:
                return

        if not self._db:
            return

        try:
            trades = self._db.get_recent_trades(limit=LOOKBACK_TRADES)
        except Exception as e:
            logger.debug("coach_get_trades_error", extra={"error": str(e)})
            return

        if not trades or len(trades) < MIN_TRADES_FOR_LESSON:
            logger.debug("coach_skip_no_trades", extra={"count": len(trades) if trades else 0})
            self._last_analysis = now
            return

        new_lessons: dict[str, CoachLesson] = {}

        # ─── 1. วิเคราะห์ตาม Combo: symbol + action + session + regime ───
        combos = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})

        for t in trades:
            symbol = t.get("symbol", "")
            action = t.get("action", "")
            session = t.get("session", "UNKNOWN")
            regime = t.get("regime", "UNKNOWN")
            profit = t.get("profit_usd", 0) or 0

            key = f"{symbol}:{action}:{session}:{regime}"
            combos[key]["total"] += 1
            if profit > 0:
                combos[key]["wins"] += 1
            else:
                combos[key]["losses"] += 1

        for key, stats in combos.items():
            total = stats["total"]
            if total < MIN_TRADES_FOR_LESSON:
                continue

            wr = stats["wins"] / total * 100 if total > 0 else 0

            if wr < 30 and total >= BLOCK_AFTER_N_LOSSES:
                # WR < 30% กับ sample >= 3 → บล็อกชั่วคราว
                blocked_until = (now + timedelta(minutes=BLOCK_DURATION_MINUTES)).isoformat()
                new_lessons[key] = CoachLesson(
                    key=key,
                    lesson_type="block",
                    reason=f"WR={wr:.0f}% ({stats['wins']}W/{stats['losses']}L) — บล็อก {BLOCK_DURATION_MINUTES} นาที",
                    blocked_until=blocked_until,
                    win_rate=wr,
                    sample_size=total,
                )
            elif wr < 45:
                # WR < 45% → ลด confidence
                new_lessons[key] = CoachLesson(
                    key=key,
                    lesson_type="reduce",
                    reason=f"WR={wr:.0f}% ({stats['wins']}W/{stats['losses']}L) — ลด confidence",
                    confidence_adj=-0.05,
                    win_rate=wr,
                    sample_size=total,
                )
            elif wr > 65:
                # WR > 65% → เพิ่ม confidence
                new_lessons[key] = CoachLesson(
                    key=key,
                    lesson_type="boost",
                    reason=f"WR={wr:.0f}% ({stats['wins']}W/{stats['losses']}L) — เพิ่ม confidence",
                    confidence_adj=+0.05,
                    win_rate=wr,
                    sample_size=total,
                )

        # ─── 2. วิเคราะห์แพ้ติดกัน (consecutive losses) ───
        per_symbol_losses: dict[str, int] = defaultdict(int)
        for t in trades:
            symbol = t.get("symbol", "")
            profit = t.get("profit_usd", 0) or 0
            if profit < 0:
                per_symbol_losses[symbol] += 1
            else:
                break  # หยุดนับเมื่อเจอไม้ที่ชนะ

        for symbol, streak in per_symbol_losses.items():
            if streak >= BLOCK_AFTER_N_LOSSES:
                key = f"{symbol}:STREAK"
                blocked_until = (now + timedelta(minutes=BLOCK_DURATION_MINUTES * 2)).isoformat()
                new_lessons[key] = CoachLesson(
                    key=key,
                    lesson_type="block",
                    reason=f"แพ้ติดต่อกัน {streak} ไม้ — พัก {BLOCK_DURATION_MINUTES*2} นาที",
                    blocked_until=blocked_until,
                    sample_size=streak,
                )

        self._lessons = new_lessons
        self._last_analysis = now

        if new_lessons:
            blocks = [l for l in new_lessons.values() if l.lesson_type == "block"]
            reduces = [l for l in new_lessons.values() if l.lesson_type == "reduce"]
            boosts = [l for l in new_lessons.values() if l.lesson_type == "boost"]
            logger.info("coach_analysis_complete", extra={
                "lessons": len(new_lessons),
                "blocks": len(blocks),
                "reduces": len(reduces),
                "boosts": len(boosts),
            })

    def get_advice(
        self,
        symbol: str,
        action: str,
        session: str = "",
        regime: str = "",
    ) -> tuple[bool, float, str]:
        """
        ขอคำแนะนำจาก Coach ก่อนเทรด

        Returns:
            (should_trade, confidence_adj, reason)
            - should_trade: False = ถูกบล็อก
            - confidence_adj: ปรับ confidence (+/-)
            - reason: เหตุผล
        """
        now = datetime.now(timezone.utc)
        total_adj = 0.0
        reasons = []

        # ─── ตรวจ combo ที่ตรงกัน ───
        key = f"{symbol}:{action}:{session}:{regime}"
        if key in self._lessons:
            lesson = self._lessons[key]
            if lesson.lesson_type == "block":
                if lesson.blocked_until and now.isoformat() < lesson.blocked_until:
                    return False, 0.0, f"Coach บล็อก: {lesson.reason}"
            total_adj += lesson.confidence_adj
            if lesson.confidence_adj != 0:
                reasons.append(lesson.reason)

        # ─── ตรวจแพ้ติดกัน ───
        streak_key = f"{symbol}:STREAK"
        if streak_key in self._lessons:
            lesson = self._lessons[streak_key]
            if lesson.lesson_type == "block":
                if lesson.blocked_until and now.isoformat() < lesson.blocked_until:
                    return False, 0.0, f"Coach บล็อก: {lesson.reason}"

        reason_str = " | ".join(reasons) if reasons else "coach:ok"
        return True, total_adj, reason_str

    @property
    def active_lessons(self) -> list[dict]:
        """ดึง lessons ที่ยังมีผลอยู่สำหรับ dashboard"""
        return [
            {
                "key": l.key,
                "type": l.lesson_type,
                "reason": l.reason,
                "win_rate": l.win_rate,
                "sample": l.sample_size,
            }
            for l in self._lessons.values()
        ]
