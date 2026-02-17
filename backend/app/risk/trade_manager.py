"""
Trade Manager — จัดการ Trailing Stop, Profit Lock, Manual Trade Protection.

3 ส่วนหลัก:
    1. TrailingStopManager — ลาก SL ตามกำไร (ATR-based / fixed-point)
    2. ProfitLockManager — ล็อคกำไรเป็นชั้น (multi-tier)
    3. ManualTradeProtector — ตั้ง SL/TP ให้ไม้ manual (magic=0)

กฎ:
    - SL ห้ามถอย (ย้ายได้แค่ทิศทางดีขึ้น)
    - ทำงานเฉพาะ LIVE mode (DRY_RUN → log only)
    - Bounded memory: cleanup entries เก่ากว่า 24 ชม.
    - Log ทุก action ด้วย structured logger

Performance (8GB RAM):
    - dict per ticket → bounded with cleanup
    - No unbounded buffers
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── Cleanup thresholds (shared) ───
_MAX_ENTRIES = 5_000
_CLEANUP_AGE_SECS = 86_400  # 24 ชม.


# ====================================================================
# TrailingStopManager — ลาก SL ตามราคากำไร
# ====================================================================

@dataclass
class TrailingConfig:
    """Configuration สำหรับ trailing stop ของ 1 ticket."""
    mode: str = "off"              # "atr" | "fixed" | "off"
    atr_multiplier: float = 3.0    # ATR × N สำหรับ trailing distance
    fixed_points: float = 0.0      # จำนวน points คงที่สำหรับ trailing
    activation_r: float = 1.0      # เริ่ม trail เมื่อกำไร >= NR
    created_at: float = field(default_factory=time.time)


@dataclass
class TrailingState:
    """State ปัจจุบันของ trailing stop ต่อ ticket."""
    peak_price: float = 0.0        # ราคาสูงสุด/ต่ำสุดที่เคยเห็น
    last_sl: float = 0.0           # SL ล่าสุดที่ตั้ง
    activated: bool = False        # เริ่ม trail แล้วหรือยัง
    created_at: float = field(default_factory=time.time)


class TrailingStopManager:
    """
    ลาก SL ตามกำไรอัตโนมัติ.

    Modes:
        - atr: SL = peak_price - (ATR × multiplier)  [BUY]
        - fixed: SL = peak_price - fixed_points       [BUY]
        - off: ไม่ทำอะไร

    กฎ:
        - SL ห้ามถอยกลับ (monotonic increase for BUY, decrease for SELL)
        - เริ่ม trail เมื่อกำไร >= activation_r × SL distance
    """

    def __init__(self, mt5_client=None) -> None:
        self.mt5 = mt5_client
        self._configs: dict[int, TrailingConfig] = {}   # ticket → config
        self._states: dict[int, TrailingState] = {}     # ticket → state

    def set_trailing(self, ticket: int, config: TrailingConfig) -> None:
        """ตั้ง trailing config ให้ ticket."""
        self._configs[ticket] = config
        self._states[ticket] = TrailingState()
        logger.info("trailing_set", extra={
            "ticket": ticket,
            "mode": config.mode,
            "atr_mult": config.atr_multiplier,
            "fixed_pts": config.fixed_points,
            "activation_r": config.activation_r,
        })

    def remove_trailing(self, ticket: int) -> None:
        """ยกเลิก trailing สำหรับ ticket."""
        self._configs.pop(ticket, None)
        self._states.pop(ticket, None)
        logger.info("trailing_removed", extra={"ticket": ticket})

    def get_status(self, ticket: int) -> dict:
        """ดึงสถานะ trailing ของ ticket."""
        cfg = self._configs.get(ticket)
        state = self._states.get(ticket)
        if not cfg:
            return {"active": False, "mode": "off"}
        return {
            "active": True,
            "mode": cfg.mode,
            "atr_multiplier": cfg.atr_multiplier,
            "fixed_points": cfg.fixed_points,
            "activation_r": cfg.activation_r,
            "peak_price": state.peak_price if state else 0.0,
            "activated": state.activated if state else False,
        }

    def update_all(self, positions: list[dict], atr_values: dict[str, float] | None = None) -> list[dict]:
        """
        ตรวจ + update trailing stop ของทุก position ที่มี config.

        Args:
            positions: list ของ position dicts จาก MT5
            atr_values: {symbol: atr_value} สำหรับ ATR-based trailing

        Returns:
            list ของ actions ที่ทำ [{ticket, action, old_sl, new_sl}]
        """
        actions = []
        atr_values = atr_values or {}

        for pos in positions:
            ticket = pos["ticket"]
            cfg = self._configs.get(ticket)
            if not cfg or cfg.mode == "off":
                continue

            state = self._states.get(ticket)
            if not state:
                state = TrailingState()
                self._states[ticket] = state

            entry = pos["price_open"]
            current = pos["price_current"]
            sl = pos["sl"]
            is_buy = pos["type"] == "BUY"
            symbol = pos["symbol"]

            # ─── คำนวณ SL distance (1R) ───
            sl_distance = abs(entry - sl) if sl > 0 else 0
            if sl_distance <= 0:
                continue  # ไม่มี SL → ข้าม (ให้ ManualTradeProtector จัดการ)

            # ─── ตรวจว่าถึง activation threshold หรือยัง ───
            if is_buy:
                profit_distance = current - entry
            else:
                profit_distance = entry - current

            required_activation = sl_distance * cfg.activation_r
            if profit_distance < required_activation:
                continue  # ยังไม่ถึง activation → ข้าม

            state.activated = True

            # ─── Update peak price ───
            if is_buy:
                if current > state.peak_price or state.peak_price == 0:
                    state.peak_price = current
            else:
                if current < state.peak_price or state.peak_price == 0:
                    state.peak_price = current

            # ─── คำนวณ new SL ───
            trail_distance = 0.0
            if cfg.mode == "atr":
                atr = atr_values.get(symbol, sl_distance)  # fallback to SL distance
                trail_distance = atr * cfg.atr_multiplier
            elif cfg.mode == "fixed":
                trail_distance = cfg.fixed_points

            if trail_distance <= 0:
                continue

            if is_buy:
                new_sl = state.peak_price - trail_distance
                # SL ห้ามถอย: new_sl ต้อง > current SL
                if new_sl <= sl:
                    continue
                # SL ต้องไม่เกินราคาปัจจุบัน
                if new_sl >= current:
                    continue
            else:
                new_sl = state.peak_price + trail_distance
                # SL ห้ามถอย: new_sl ต้อง < current SL (สำหรับ SELL)
                if sl > 0 and new_sl >= sl:
                    continue
                if new_sl <= current:
                    continue

            # ─── Round SL to symbol digits ───
            digits = 2  # default for most
            info = pos.get("digits", digits)
            new_sl = round(new_sl, info if isinstance(info, int) else digits)

            # ─── ส่ง modify ───
            old_sl = sl
            if self.mt5:
                success = self.mt5.modify_sl(ticket, new_sl, pos.get("tp", 0.0))
                if success:
                    actions.append({
                        "ticket": ticket,
                        "action": "trailing_sl_moved",
                        "old_sl": old_sl,
                        "new_sl": new_sl,
                        "peak": state.peak_price,
                        "mode": cfg.mode,
                    })
                    state.last_sl = new_sl
                    logger.info("trailing_sl_moved", extra={
                        "ticket": ticket,
                        "symbol": symbol,
                        "old_sl": old_sl,
                        "new_sl": new_sl,
                        "peak": state.peak_price,
                        "mode": cfg.mode,
                        "stage": "trailing",
                        "result": "ok",
                    })
                else:
                    logger.warning("trailing_sl_modify_failed", extra={
                        "ticket": ticket,
                        "symbol": symbol,
                        "stage": "trailing",
                        "result": "error",
                    })

        self._cleanup_stale()
        return actions

    def _cleanup_stale(self) -> None:
        """ลบ entries เก่ากว่า 24 ชม."""
        if len(self._configs) < _MAX_ENTRIES:
            return
        cutoff = time.time() - _CLEANUP_AGE_SECS
        stale = [t for t, c in self._configs.items() if c.created_at < cutoff]
        for t in stale:
            self._configs.pop(t, None)
            self._states.pop(t, None)
        if stale:
            logger.debug("trailing_cleanup", extra={"removed": len(stale)})


# ====================================================================
# ProfitLockManager — ล็อคกำไรเป็นชั้นๆ
# ====================================================================

@dataclass
class ProfitLockTier:
    """กฎล็อคกำไร 1 ชั้น."""
    r_multiple: float      # เมื่อกำไรถึง NR
    lock_pct: float         # ล็อคกำไร N% (0.0 = BE, 0.5 = 50%, 0.75 = 75%)


@dataclass
class ProfitLockConfig:
    """Config สำหรับ profit locking ของ 1 ticket."""
    tiers: list[ProfitLockTier] = field(default_factory=lambda: [
        ProfitLockTier(r_multiple=1.0, lock_pct=0.0),    # +1R → BE (lock 0%)
        ProfitLockTier(r_multiple=2.0, lock_pct=0.5),    # +2R → lock 50%
        ProfitLockTier(r_multiple=3.0, lock_pct=0.75),   # +3R → lock 75%
    ])
    created_at: float = field(default_factory=time.time)


class ProfitLockManager:
    """
    ล็อคกำไรเป็นชั้นๆ — ย้าย SL ขึ้นตาม tier.

    ตัวอย่าง:
        entry=2000, SL=1990 (SL dist = 10)
        +1R (current >= 2010) → SL → 2000 (BE, lock 0%)
        +2R (current >= 2020) → SL → 2010 (lock 50%)
        +3R (current >= 2030) → SL → 2022.5 (lock 75%)

    กฎ:
        - SL ย้ายได้แค่ทิศทางดีขึ้น
        - ไม่ tier-skip (ต้องผ่าน tier 1 ก่อน)
    """

    def __init__(self, mt5_client=None) -> None:
        self.mt5 = mt5_client
        self._configs: dict[int, ProfitLockConfig] = {}
        self._current_tier: dict[int, int] = {}  # ticket → highest tier reached (index)

    def set_profit_lock(self, ticket: int, config: ProfitLockConfig) -> None:
        """ตั้ง profit lock config ให้ ticket."""
        # Sort tiers by r_multiple ascending
        config.tiers.sort(key=lambda t: t.r_multiple)
        self._configs[ticket] = config
        self._current_tier[ticket] = -1  # ยังไม่ถึง tier ไหน
        logger.info("profit_lock_set", extra={
            "ticket": ticket,
            "tiers": len(config.tiers),
        })

    def remove_profit_lock(self, ticket: int) -> None:
        """ยกเลิก profit lock สำหรับ ticket."""
        self._configs.pop(ticket, None)
        self._current_tier.pop(ticket, None)

    def get_status(self, ticket: int) -> dict:
        """ดึงสถานะ profit lock ของ ticket."""
        cfg = self._configs.get(ticket)
        if not cfg:
            return {"active": False, "current_tier": -1}
        return {
            "active": True,
            "current_tier": self._current_tier.get(ticket, -1),
            "total_tiers": len(cfg.tiers),
            "tiers": [{"r": t.r_multiple, "lock_pct": t.lock_pct} for t in cfg.tiers],
        }

    def check_all(self, positions: list[dict]) -> list[dict]:
        """
        ตรวจทุก position ที่มี profit lock config.

        Returns:
            list ของ actions ที่ทำ [{ticket, tier, old_sl, new_sl}]
        """
        actions = []

        for pos in positions:
            ticket = pos["ticket"]
            cfg = self._configs.get(ticket)
            if not cfg:
                continue

            entry = pos["price_open"]
            current = pos["price_current"]
            sl = pos["sl"]
            is_buy = pos["type"] == "BUY"

            sl_distance = abs(entry - sl) if sl > 0 else 0
            if sl_distance <= 0:
                continue

            # ─── คำนวณ profit distance ───
            if is_buy:
                profit_distance = current - entry
            else:
                profit_distance = entry - current

            # ─── ตรวจแต่ละ tier ───
            current_tier_idx = self._current_tier.get(ticket, -1)

            for i, tier in enumerate(cfg.tiers):
                if i <= current_tier_idx:
                    continue  # tier นี้ผ่านแล้ว

                required_profit = sl_distance * tier.r_multiple
                if profit_distance < required_profit:
                    break  # ยังไม่ถึง tier นี้ → หยุดตรวจ

                # ─── ถึง tier แล้ว → คำนวณ new SL ───
                lock_distance = profit_distance * tier.lock_pct
                if is_buy:
                    new_sl = entry + lock_distance
                    if new_sl <= sl:
                        continue  # SL ห้ามถอย
                else:
                    new_sl = entry - lock_distance
                    if sl > 0 and new_sl >= sl:
                        continue

                # Round
                new_sl = round(new_sl, 2)
                old_sl = sl

                if self.mt5:
                    success = self.mt5.modify_sl(ticket, new_sl, pos.get("tp", 0.0))
                    if success:
                        self._current_tier[ticket] = i
                        sl = new_sl  # update for next tier check
                        actions.append({
                            "ticket": ticket,
                            "tier": i,
                            "r_multiple": tier.r_multiple,
                            "lock_pct": tier.lock_pct,
                            "old_sl": old_sl,
                            "new_sl": new_sl,
                        })
                        logger.info("profit_lock_triggered", extra={
                            "ticket": ticket,
                            "symbol": pos["symbol"],
                            "tier": i,
                            "r_multiple": tier.r_multiple,
                            "lock_pct": tier.lock_pct,
                            "old_sl": old_sl,
                            "new_sl": new_sl,
                            "stage": "profit_lock",
                            "result": "ok",
                        })

        self._cleanup_stale()
        return actions

    def _cleanup_stale(self) -> None:
        """ลบ entries เก่ากว่า 24 ชม."""
        if len(self._configs) < _MAX_ENTRIES:
            return
        cutoff = time.time() - _CLEANUP_AGE_SECS
        stale = [t for t, c in self._configs.items() if c.created_at < cutoff]
        for t in stale:
            self._configs.pop(t, None)
            self._current_tier.pop(t, None)
        if stale:
            logger.debug("profit_lock_cleanup", extra={"removed": len(stale)})


# ====================================================================
# ManualTradeProtector — ปกป้องไม้ manual (magic=0)
# ====================================================================

class ManualTradeProtector:
    """
    ตรวจ positions ที่เปิดมือ (magic=0) แล้วไม่มี SL → ตั้ง emergency SL.

    กฎ:
        - ใส่ SL = entry ± (entry × emergency_sl_pct)  [default 2%]
        - ใส่ suggestion TP = entry ± (entry × tp_suggestion_pct)  [default 4%]
        - จำ ticket ที่ protected แล้ว → ไม่ทำซ้ำ
        - Log ทุกครั้งที่ protect
    """

    def __init__(self, mt5_client=None, emergency_sl_pct: float = 0.02,
                 tp_suggestion_pct: float = 0.04) -> None:
        self.mt5 = mt5_client
        self.emergency_sl_pct = emergency_sl_pct
        self.tp_suggestion_pct = tp_suggestion_pct
        self._protected: dict[int, float] = {}  # ticket → timestamp

    def protect_unguarded(self, positions: list[dict]) -> list[dict]:
        """
        ตรวจ positions manual ที่ไม่มี SL → ตั้ง emergency SL.

        Returns:
            list ของ actions [{ticket, sl_set, tp_set}]
        """
        actions = []

        for pos in positions:
            ticket = pos["ticket"]
            magic = pos.get("magic", -1)
            sl = pos.get("sl", 0.0)

            # ─── เฉพาะ magic=0 (manual) ที่ไม่มี SL ───
            if magic != 0:
                continue
            if sl > 0:
                continue
            if ticket in self._protected:
                continue

            entry = pos["price_open"]
            is_buy = pos["type"] == "BUY"
            symbol = pos["symbol"]

            # ─── คำนวณ emergency SL ───
            sl_dist = entry * self.emergency_sl_pct
            if is_buy:
                emergency_sl = round(entry - sl_dist, 2)
                suggestion_tp = round(entry + entry * self.tp_suggestion_pct, 2)
            else:
                emergency_sl = round(entry + sl_dist, 2)
                suggestion_tp = round(entry - entry * self.tp_suggestion_pct, 2)

            # ─── ส่ง modify ───
            if self.mt5:
                tp = pos.get("tp", 0.0)
                # ตั้ง SL + TP ถ้ายังไม่มี TP
                new_tp = suggestion_tp if tp <= 0 else tp
                success = self.mt5.modify_sl(ticket, emergency_sl, new_tp)

                if success:
                    self._protected[ticket] = time.time()
                    actions.append({
                        "ticket": ticket,
                        "symbol": symbol,
                        "sl_set": emergency_sl,
                        "tp_set": new_tp,
                        "action": "manual_trade_protected",
                    })
                    logger.warning("manual_trade_protected", extra={
                        "ticket": ticket,
                        "symbol": symbol,
                        "entry": entry,
                        "sl_set": emergency_sl,
                        "tp_set": new_tp,
                        "stage": "manual_protect",
                        "result": "ok",
                    })
                else:
                    logger.error("manual_trade_protect_failed", extra={
                        "ticket": ticket,
                        "symbol": symbol,
                        "stage": "manual_protect",
                        "result": "error",
                    })

        self._cleanup_stale()
        return actions

    def _cleanup_stale(self) -> None:
        """ลบ entries เก่ากว่า 24 ชม."""
        if len(self._protected) < _MAX_ENTRIES:
            return
        cutoff = time.time() - _CLEANUP_AGE_SECS
        stale = [t for t, ts in self._protected.items() if ts < cutoff]
        for t in stale:
            del self._protected[t]
        if stale:
            logger.debug("manual_protect_cleanup", extra={"removed": len(stale)})

    def reset(self) -> None:
        """รีเซ็ต protected list (ใช้ตอนเริ่มวันใหม่)."""
        self._protected.clear()


# ====================================================================
# GhostGuard — Virtual SL/TP (ซ่อนจากโบรกเกอร์)
# ====================================================================

@dataclass
class GhostLevel:
    """Virtual SL/TP level ที่เก็บใน bot memory — โบรกเกอร์มองไม่เห็น."""
    virtual_sl: float = 0.0     # Virtual Stop Loss (0 = ไม่ตั้ง)
    virtual_tp: float = 0.0     # Virtual Take Profit (0 = ไม่ตั้ง)
    created_at: float = field(default_factory=time.time)


class GhostGuard:
    """
    Ghost Guard — SL/TP ล่องหน (Virtual / Invisible).

    แทนที่จะวาง SL/TP ไว้บน server ให้โบรกเกอร์เห็น
    → เก็บไว้ใน memory ของ bot → ปิดเอง เมื่อราคาถึงเป้า

    ข้อดี:
        - โบรกเกอร์ / เจ้ามือ ไม่เห็น SL/TP → ป้องกัน stop hunting
        - ปิด position ด้วย market order → ราคาจริง ณ ขณะนั้น

    ข้อเสีย:
        - ถ้า bot ตาย → ไม่มี safety net (ใช้ร่วมกับ server SL ที่กว้างขึ้นเป็น fallback)
        - Slippage อาจมากกว่า server SL ในตลาดเร็ว

    กลยุทธ์แนะนำ:
        - ตั้ง virtual SL/TP ที่ต้องการจริงๆ (แคบ)
        - ตั้ง server SL ไว้ไกลๆ เป็น safety net (กว้าง) → โบรกเกอร์เห็นแค่ตัวกว้าง

    กฎ:
        - ตรวจทุก cycle
        - ปิด position ทันทีที่ราคาข้าม virtual level
        - Log ทุก action + เหตุผล
    """

    def __init__(self, mt5_client=None) -> None:
        self.mt5 = mt5_client
        self._levels: dict[int, GhostLevel] = {}  # ticket → GhostLevel

    def set_ghost(self, ticket: int, virtual_sl: float = 0.0, virtual_tp: float = 0.0) -> None:
        """
        ตั้ง virtual SL/TP สำหรับ ticket.

        Args:
            ticket: หมายเลข position
            virtual_sl: Virtual Stop Loss (0 = ไม่ใช้)
            virtual_tp: Virtual Take Profit (0 = ไม่ใช้)
        """
        self._levels[ticket] = GhostLevel(
            virtual_sl=virtual_sl,
            virtual_tp=virtual_tp,
        )
        logger.info("ghost_set", extra={
            "ticket": ticket,
            "virtual_sl": virtual_sl,
            "virtual_tp": virtual_tp,
            "stage": "ghost",
            "result": "ok",
        })

    def remove_ghost(self, ticket: int) -> None:
        """ยกเลิก ghost guard สำหรับ ticket."""
        self._levels.pop(ticket, None)
        logger.info("ghost_removed", extra={"ticket": ticket})

    def get_status(self, ticket: int) -> dict:
        """ดึงสถานะ ghost guard ของ ticket."""
        lvl = self._levels.get(ticket)
        if not lvl:
            return {"active": False, "virtual_sl": 0.0, "virtual_tp": 0.0}
        return {
            "active": True,
            "virtual_sl": lvl.virtual_sl,
            "virtual_tp": lvl.virtual_tp,
        }

    def check_all(self, positions: list[dict]) -> list[dict]:
        """
        ตรวจทุก position ที่มี ghost levels → ปิดถ้าราคาถึง.

        เรียกทุก cycle จาก MasterLoop._check_positions()

        Returns:
            list ของ actions [{ticket, action, reason, virtual_level, price}]
        """
        actions = []

        for pos in positions:
            ticket = pos["ticket"]
            lvl = self._levels.get(ticket)
            if not lvl:
                continue

            current = pos["price_current"]
            is_buy = pos["type"] == "BUY"
            symbol = pos["symbol"]

            close_reason = None

            # ─── Virtual SL Check ───
            if lvl.virtual_sl > 0:
                if is_buy and current <= lvl.virtual_sl:
                    close_reason = "ghost_sl_hit"
                elif not is_buy and current >= lvl.virtual_sl:
                    close_reason = "ghost_sl_hit"

            # ─── Virtual TP Check ───
            if not close_reason and lvl.virtual_tp > 0:
                if is_buy and current >= lvl.virtual_tp:
                    close_reason = "ghost_tp_hit"
                elif not is_buy and current <= lvl.virtual_tp:
                    close_reason = "ghost_tp_hit"

            if not close_reason:
                continue

            # ─── ปิด position ทันที ───
            if self.mt5:
                success = self.mt5.close_position(ticket, reason=close_reason)
                action_result = {
                    "ticket": ticket,
                    "symbol": symbol,
                    "action": close_reason,
                    "price": current,
                    "virtual_sl": lvl.virtual_sl,
                    "virtual_tp": lvl.virtual_tp,
                    "success": success,
                }
                actions.append(action_result)

                if success:
                    # ลบออกจาก tracking หลังปิดสำเร็จ
                    self._levels.pop(ticket, None)
                    logger.info("ghost_triggered", extra={
                        "ticket": ticket,
                        "symbol": symbol,
                        "reason": close_reason,
                        "price": current,
                        "virtual_sl": lvl.virtual_sl,
                        "virtual_tp": lvl.virtual_tp,
                        "profit": pos.get("profit", 0),
                        "stage": "ghost",
                        "result": "ok",
                    })
                else:
                    logger.error("ghost_close_failed", extra={
                        "ticket": ticket,
                        "symbol": symbol,
                        "reason": close_reason,
                        "price": current,
                        "stage": "ghost",
                        "result": "error",
                    })

        self._cleanup_stale()
        return actions

    def _cleanup_stale(self) -> None:
        """ลบ entries เก่ากว่า 24 ชม."""
        if len(self._levels) < _MAX_ENTRIES:
            return
        cutoff = time.time() - _CLEANUP_AGE_SECS
        stale = [t for t, lvl in self._levels.items() if lvl.created_at < cutoff]
        for t in stale:
            self._levels.pop(t, None)
        if stale:
            logger.debug("ghost_cleanup", extra={"removed": len(stale)})

    def reset(self) -> None:
        """รีเซ็ต ghost levels ทั้งหมด."""
        self._levels.clear()
        logger.info("ghost_guard_reset")

