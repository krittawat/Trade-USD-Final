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
    mode: str = "off"              # "atr" | "fixed" | "step" | "chandelier" | "adaptive" | "dynamic" | "structure" | "off"
    atr_multiplier: float = 3.0    # ATR × N for trailing distance
    fixed_points: float = 0.0      # Fixed points
    activation_r: float = 1.0      # Start trail when profit >= NR
    # Step mode
    step_r: float = 0.5            # Move every 0.5R
    # Chandelier mode
    chandelier_period: int = 14    # Lookback period for HH/LL
    # Adaptive mode
    adaptive_min_mult: float = 1.0 # Min ATR mult
    adaptive_max_mult: float = 3.0 # Max ATR mult
    adaptive_ramp_r: float = 3.0   # R to hit min ATR
    # Structure mode
    structure_fractal_window: int = 5 # Window for swing high/low
    structure_buffer_points: float = 1.0 # Buffer from swing point
    # ─── Anti-Hunt Protection (ป้องกัน Stop Hunt ตอนย้าย SL) ───
    anti_hunt: bool = True         # เปิด/ปิด anti-hunt สำหรับ trailing
    anti_hunt_dodge_pts: float = 1.5  # ระยะ dodge จากเลขกลม ($)
    # ─── Dynamic mode — 3 phases อัตโนมัติ ───
    # Phase 1: +activation_r → BE + buffer
    # Phase 2: +1R–2R → Step trailing ทีละ step_r
    # Phase 3: +2R+ → ATR trailing ที่บีบแน่นขึ้น
    dynamic_phase2_r: float = 1.0  # R ที่เริ่ม Phase 2 (step trailing)
    dynamic_phase3_r: float = 2.0  # R ที่เริ่ม Phase 3 (ATR tightening)
    created_at: float = field(default_factory=time.time)


@dataclass
class TrailingState:
    """State ปัจจุบันของ trailing stop ต่อ ticket."""
    peak_price: float = 0.0        # ราคาสูงสุด/ต่ำสุดที่เคยเห็น
    last_sl: float = 0.0           # SL ล่าสุดที่ตั้ง
    activated: bool = False        # เริ่ม trail แล้วหรือยัง
    last_step_r: float = 0.0       # step mode: R ล่าสุดที่ย้าย SL
    created_at: float = field(default_factory=time.time)


class TrailingStopManager:
    """
    ลาก SL ตามกำไรอัตโนมัติ — 6 modes + Anti-Hunt + Profit Lock Floor.

    Modes:
        - atr:         SL = peak − ATR × multiplier              [ปรับตาม volatility]
        - fixed:       SL = peak − fixed_points                  [คงที่]
        - step:        ย้าย SL ทีละ step_r เมื่อกำไรถึง R         [ป้องกันโดนเขี่ย]
        - chandelier:  SL = HH − ATR × mult (BUY) / LL+ATR      [trend following]
        - adaptive:    ATR mult ลดตามกำไร → SL บีบแน่นขึ้น      [hybrid]
        - dynamic:     3 phases: BE → Step → ATR tightening       [ล็อคกำไร + ป้องกัน stop hunt]
        - off:         ไม่ทำอะไร

    กฎ:
        - SL ห้ามถอยกลับ (monotonic increase for BUY, decrease for SELL)
        - เริ่ม trail เมื่อกำไร >= activation_r × SL distance
        - Anti-Hunt: ทุกครั้งที่ย้าย SL จะ dodge เลขกลม + buffer
        - Profit Lock Floor: SL ห้ามต่ำกว่า tier ที่ล็อคไว้
    """

    def __init__(self, mt5_client=None, profit_lock_mgr=None) -> None:
        self.mt5 = mt5_client
        self._configs: dict[int, TrailingConfig] = {}   # ticket → config
        self._states: dict[int, TrailingState] = {}     # ticket → state
        self._profit_lock_mgr = profit_lock_mgr         # อ้างอิง ProfitLockManager (ถ้ามี)

    def set_trailing(self, ticket: int, config: TrailingConfig) -> None:
        """ตั้ง trailing config ให้ ticket."""
        self._configs[ticket] = config
        self._states[ticket] = TrailingState()
        logger.debug("trailing_set", extra={
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

    def update_all(
        self,
        positions: list[dict],
        atr_values: dict[str, float] | None = None,
        candles_cache: dict | None = None,
    ) -> list[dict]:
        """
        ตรวจ + update trailing stop ของทุก position ที่มี config.

        Args:
            positions: list ของ position dicts จาก MT5
            atr_values: {symbol: atr_value} สำหรับ ATR/chandelier/adaptive
            candles_cache: {symbol: DataFrame} สำหรับ chandelier (HH/LL)

        Returns:
            list ของ actions ที่ทำ [{ticket, action, old_sl, new_sl, symbol, ...}]
        """
        actions = []
        atr_values = atr_values or {}
        candles_cache = candles_cache or {}

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
                continue  # ไม่มี SL → ข้าม

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

            # ─── คำนวณ new SL ตาม mode ───
            new_sl = self._calculate_trailing_sl(
                cfg=cfg, state=state, pos=pos,
                sl_distance=sl_distance, profit_distance=profit_distance,
                atr_values=atr_values, candles_cache=candles_cache,
            )

            if new_sl is None:
                continue

            # ─── Round SL ก่อน ───
            digits = pos.get("digits", 2)
            digits = digits if isinstance(digits, int) else 2

            # ─── Anti-Hunt: หลบเลขกลม + buffer (ป้องกัน stop hunt) ───
            if cfg.anti_hunt:
                new_sl = self._apply_anti_hunt_on_sl(
                    new_sl, symbol, "BUY" if is_buy else "SELL",
                    dodge_distance=cfg.anti_hunt_dodge_pts,
                    digits=digits,
                )

            # ─── Profit Lock Floor: SL ห้ามต่ำกว่า tier ที่ล็อคไว้ ───
            lock_floor = self._get_profit_lock_floor(ticket, pos)
            if lock_floor is not None:
                if is_buy and new_sl < lock_floor:
                    new_sl = lock_floor
                    logger.debug("trailing_profit_lock_floor", extra={
                        "ticket": ticket, "floor": lock_floor,
                        "reason": "SL ถูกดันขึ้นให้ไม่ต่ำกว่า locked tier",
                    })
                elif not is_buy and new_sl > lock_floor:
                    new_sl = lock_floor
                    logger.debug("trailing_profit_lock_floor", extra={
                        "ticket": ticket, "floor": lock_floor,
                        "reason": "SL ถูกดันลงให้ไม่สูงกว่า locked tier",
                    })

            new_sl = round(new_sl, digits)

            # ─── SL ห้ามถอย (monotonic) ───
            if is_buy:
                if new_sl <= sl or new_sl >= current:
                    continue
            else:
                if (sl > 0 and new_sl >= sl) or new_sl <= current:
                    continue

            # ─── ส่ง modify ───
            old_sl = sl
            if self.mt5:
                success = self.mt5.modify_sl(ticket, new_sl, pos.get("tp", 0.0))
                if success:
                    actions.append({
                        "ticket": ticket,
                        "symbol": symbol,
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

    def _calculate_trailing_sl(
        self,
        cfg: TrailingConfig,
        state: TrailingState,
        pos: dict,
        sl_distance: float,
        profit_distance: float,
        atr_values: dict[str, float],
        candles_cache: dict,
    ) -> float | None:
        """
        คำนวณ new SL ตาม trailing mode.

        Returns:
            new_sl price หรือ None ถ้าไม่ต้องย้าย
        """
        entry = pos["price_open"]
        is_buy = pos["type"] == "BUY"
        symbol = pos["symbol"]
        atr = atr_values.get(symbol, sl_distance)  # fallback = SL distance

        # ========== ATR MODE ==========
        if cfg.mode == "atr":
            trail_dist = atr * cfg.atr_multiplier
            if trail_dist <= 0:
                return None
            if is_buy:
                return state.peak_price - trail_dist
            else:
                return state.peak_price + trail_dist

        # ========== FIXED MODE ==========
        elif cfg.mode == "fixed":
            if cfg.fixed_points <= 0:
                return None
            if is_buy:
                return state.peak_price - cfg.fixed_points
            else:
                return state.peak_price + cfg.fixed_points

        # ========== STEP MODE ==========
        # ย้าย SL ทีละ step_r เมื่อกำไรถึงแต่ละ R threshold
        # เช่น step_r=0.5: +0.5R→SL=entry, +1R→SL=+0.5R, +1.5R→SL=+1R ...
        elif cfg.mode == "step":
            if cfg.step_r <= 0:
                return None
            current_r = profit_distance / sl_distance if sl_distance > 0 else 0
            # หา R threshold ถัดไปที่ควร trigger
            next_step = state.last_step_r + cfg.step_r
            if current_r < next_step:
                return None  # ยังไม่ถึง step ถัดไป
            # ย้าย SL ไปที่ entry + (last_step_r × sl_distance)
            # กำไรที่จะล็อค = last_step_r × sl_distance (lock trailing step ก่อนหน้า)
            lock_distance = state.last_step_r * sl_distance
            state.last_step_r = next_step  # อัปเดต step
            if is_buy:
                return entry + lock_distance
            else:
                return entry - lock_distance

        # ========== CHANDELIER MODE ==========
        # SL = Highest High − ATR × mult (BUY) / Lowest Low + ATR × mult (SELL)
        elif cfg.mode == "chandelier":
            candles = candles_cache.get(symbol)
            if candles is None or len(candles) < cfg.chandelier_period:
                # Fallback to ATR mode
                trail_dist = atr * cfg.atr_multiplier
                if trail_dist <= 0:
                    return None
                if is_buy:
                    return state.peak_price - trail_dist
                else:
                    return state.peak_price + trail_dist

            period = cfg.chandelier_period
            if is_buy:
                highest_high = float(candles["high"].iloc[-period:].max())
                return highest_high - (atr * cfg.atr_multiplier)
            else:
                lowest_low = float(candles["low"].iloc[-period:].min())
                return lowest_low + (atr * cfg.atr_multiplier)

        # ========== ADAPTIVE MODE ==========
        # ATR multiplier ลดลง linear ตามกำไร → SL บีบแน่นขึ้น
        # กำไร 0R → max_mult, กำไร ramp_r → min_mult
        elif cfg.mode == "adaptive":
            current_r = profit_distance / sl_distance if sl_distance > 0 else 0
            # Linear interpolation: max_mult → min_mult
            progress = min(1.0, current_r / cfg.adaptive_ramp_r) if cfg.adaptive_ramp_r > 0 else 1.0
            adaptive_mult = cfg.adaptive_max_mult - (cfg.adaptive_max_mult - cfg.adaptive_min_mult) * progress
            trail_dist = atr * adaptive_mult
            if trail_dist <= 0:
                return None
            if is_buy:
                return state.peak_price - trail_dist
            else:
                return state.peak_price + trail_dist

        # ========== DYNAMIC MODE ==========
        # 3 Phases อัตโนมัติ — ล็อคกำไรเป็นอันดับแรก:
        #   Phase 1 (+activation_r ถึง +phase2_r): ย้าย SL ไป BE + buffer
        #   Phase 2 (+phase2_r ถึง +phase3_r): Step trailing ทีละ step_r (ล็อคกำไรทีละขั้น)
        #   Phase 3 (+phase3_r ขึ้นไป): ATR trailing ที่บีบแน่นขึ้นตามกำไร
        elif cfg.mode == "dynamic":
            current_r = profit_distance / sl_distance if sl_distance > 0 else 0

            # --- Phase 1: Break-Even + Buffer ---
            if current_r < cfg.dynamic_phase2_r:
                # ย้าย SL ไปจุดคุ้มทุน + buffer เล็กน้อย
                buffer = sl_distance * 0.05  # buffer 5% ของ SL distance
                if is_buy:
                    return entry + buffer
                else:
                    return entry - buffer

            # --- Phase 2: Step Trailing (ล็อคกำไรทีละขั้น) ---
            elif current_r < cfg.dynamic_phase3_r:
                if cfg.step_r <= 0:
                    return None
                next_step = state.last_step_r + cfg.step_r
                if current_r < next_step:
                    return None  # ยังไม่ถึง step ถัดไป
                lock_distance = state.last_step_r * sl_distance
                state.last_step_r = next_step
                if is_buy:
                    return entry + lock_distance
                else:
                    return entry - lock_distance

            # --- Phase 3: ATR Trailing ที่บีบแน่นขึ้น ---
            else:
                # คล้าย adaptive: กำไรมาก → mult ลดลง → SL บีบแน่นขึ้น
                ramp_start = cfg.dynamic_phase3_r
                ramp_end = cfg.adaptive_ramp_r if cfg.adaptive_ramp_r > ramp_start else ramp_start + 3.0
                progress = min(1.0, (current_r - ramp_start) / (ramp_end - ramp_start))
                adaptive_mult = cfg.adaptive_max_mult - (cfg.adaptive_max_mult - cfg.adaptive_min_mult) * progress
                trail_dist = atr * adaptive_mult
                if trail_dist <= 0:
                    return None
                if is_buy:
                    return state.peak_price - trail_dist
                else:
                    return state.peak_price + trail_dist

        # ========== STRUCTURE MODE ==========
        elif cfg.mode == "structure":
            try:
                from app.risk.trailing_structure import StructuralTrailingGuard
                guard = StructuralTrailingGuard(
                    fractal_window=cfg.structure_fractal_window,
                    buffer_points=cfg.structure_buffer_points,
                )
                
                # Fetch symbols directly mapping through adapter if needed
                symbol = pos.get("symbol", "")
                
                # We need candles data to determine structure
                candles = candles_cache.get(symbol)
                
                if candles is None or len(candles) < (cfg.structure_fractal_window * 2 + 1):
                    # fallback to ATR mode
                    trail_dist = atr * cfg.atr_multiplier
                    if trail_dist <= 0:
                        return None
                    return state.peak_price - trail_dist if is_buy else state.peak_price + trail_dist

                direction = "BUY" if is_buy else "SELL"
                new_sl, reason = guard.check(pos, candles, direction)
                if new_sl and new_sl > 0:
                    return new_sl
            except Exception as e:
                logger.error("structure_trailing_error", extra={"error": str(e), "ticket": pos.get("ticket")})
            
            return None

        return None

    # ─── Anti-Hunt: หลบเลขกลม + micro-buffer ───
    @staticmethod
    def _apply_anti_hunt_on_sl(
        sl_price: float,
        symbol: str,
        direction: str,
        dodge_distance: float = 1.5,
        digits: int = 2,
    ) -> float:
        """
        ปรับ SL ให้หลบเลขกลม + buffer (ป้องกัน stop hunt ตอน trailing).

        ใช้ apply_anti_hunt_trailing() จาก anti_hunt_sl module.
        """
        try:
            from app.risk.anti_hunt_sl import apply_anti_hunt_trailing

            # คำนวณ point ตาม symbol
            s = symbol.upper()
            if "XAU" in s or "GOLD" in s:
                point = 0.01
            elif "XAG" in s or "SILVER" in s:
                point = 0.001
            elif "BTC" in s:
                point = 1.0
            elif "JPY" in s:
                point = 0.001
            else:
                point = 0.00001  # forex default

            return apply_anti_hunt_trailing(
                sl_price=sl_price,
                direction=direction,
                dodge_distance=dodge_distance,
                point=point,
                digits=digits,
            )
        except Exception as e:
            logger.debug("anti_hunt_trailing_skip", extra={"error": str(e)})
            return sl_price

    # ─── Profit Lock Floor: ดึง SL ขั้นต่ำจาก ProfitLockManager ───
    def _get_profit_lock_floor(
        self, ticket: int, pos: dict,
    ) -> float | None:
        """
        ดึง SL ขั้นต่ำจาก ProfitLockManager — SL ห้ามต่ำกว่านี้.

        คำนวณจาก tier สูงสุดที่ถึงแล้ว:
          new_sl_floor = entry + (lock_pct × profit_at_tier)

        Returns:
            SL floor price หรือ None ถ้าไม่มี profit lock
        """
        if not self._profit_lock_mgr:
            return None

        cfg = self._profit_lock_mgr._configs.get(ticket)
        if not cfg:
            return None

        tier_idx = self._profit_lock_mgr._current_tier.get(ticket, -1)
        if tier_idx < 0:
            return None

        # ดึง tier ที่ล็อคไว้สูงสุด
        tier = cfg.tiers[tier_idx]
        entry = pos["price_open"]
        sl = pos["sl"]
        is_buy = pos["type"] == "BUY"
        sl_distance = abs(entry - sl) if sl > 0 else 0
        if sl_distance <= 0:
            return None

        # กำไรที่ tier นี้ = tier.r_multiple × sl_distance
        profit_at_tier = tier.r_multiple * sl_distance
        lock_distance = profit_at_tier * tier.lock_pct

        if is_buy:
            return entry + lock_distance
        else:
            return entry - lock_distance

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
        logger.debug("profit_lock_set", extra={
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
    ตรวจ positions ที่เปิดมือ (magic=0) แล้วดูแล SL/TP อัตโนมัติ.

    3 กรณีที่จัดการ:
        Case A: ไม่มี SL → ตั้งทั้ง SL + TP (ATR/Anti-Hunt + Smart TP)
        Case B: มี SL แต่ไม่มี TP → ตั้ง TP จาก Smart TP Engine
        Case C: มีทั้ง SL + TP → ข้าม (ไม่แก้ไข)

    กฎ:
        - SL: Anti-Stop-Hunt Engine (preferred) → ATR fallback → Fixed % fallback
        - TP: Smart TP Engine (Swing Target + Round Magnet + Dynamic RR)
        - จำ ticket ที่ protected แล้ว → ไม่ทำซ้ำ
        - Log + Telegram ทุกครั้งที่ protect
    """

    def __init__(self, mt5_client=None, emergency_sl_pct: float = 0.02,
                 sl_atr_multiplier: float = 2.0,
                 tp_rr_ratio: float = 2.0) -> None:
        self.mt5 = mt5_client
        self.emergency_sl_pct = emergency_sl_pct
        self.sl_atr_multiplier = sl_atr_multiplier
        self.tp_rr_ratio = tp_rr_ratio
        self._protected: dict[int, float] = {}  # ticket → timestamp

    @staticmethod
    def _calculate_atr(candles, period: int = 14) -> float:
        """คำนวณ ATR จาก candles DataFrame."""
        if candles is None or len(candles) < period + 1:
            return 0.0
        high = candles["high"]
        low = candles["low"]
        close = candles["close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = tr1.combine(tr2, max).combine(tr3, max)
        atr = tr.rolling(window=period).mean().iloc[-1]
        return float(atr) if atr == atr else 0.0  # NaN check

    def protect_unguarded(self, positions: list[dict],
                          candles_cache: dict | None = None) -> list[dict]:
        """
        ตรวจ positions manual ที่ไม่มี SL และ/หรือ TP → ตั้งให้อัตโนมัติ.

        3 กรณี:
            A) sl <= 0            → ตั้ง SL + TP (Anti-Hunt + Smart TP)
            B) sl > 0, tp <= 0    → ตั้ง TP อย่างเดียว (Smart TP)
            C) sl > 0, tp > 0     → ข้าม

        Args:
            positions: list ของ position dicts จาก MT5
            candles_cache: {symbol: DataFrame} — M5 candles สำหรับ ATR/Smart TP

        Returns:
            list ของ actions [{ticket, symbol, sl_set, tp_set, method, action_type}]
        """
        actions = []
        candles_cache = candles_cache or {}

        for pos in positions:
            ticket = pos["ticket"]
            magic = pos.get("magic", -1)
            sl = pos.get("sl", 0.0)
            tp = pos.get("tp", 0.0)

            # ─── เฉพาะ magic=0 (manual) ───
            if magic != 0:
                continue
            # ─── มีทั้ง SL + TP แล้ว → ข้าม ───
            if sl > 0 and tp > 0:
                continue
            # ─── เคย protect แล้ว → ข้าม ───
            if ticket in self._protected:
                continue

            entry = pos["price_open"]
            is_buy = pos["type"] == "BUY"
            symbol = pos["symbol"]
            digits = pos.get("digits", 2)
            direction = "BUY" if is_buy else "SELL"

            # ─── ดึง candles + ATR ───
            candles = candles_cache.get(symbol)
            atr = self._calculate_atr(candles) if candles is not None else 0.0

            # ─── กำหนด SL + TP ตาม case ───
            new_sl = sl   # ค่าเดิม (ถ้ามีอยู่แล้ว)
            new_tp = tp   # ค่าเดิม (ถ้ามีอยู่แล้ว)
            method = "unknown"
            action_type = "unknown"

            if sl <= 0:
                # ══════════════════════════════════════════════
                # Case A: ไม่มี SL → ตั้งทั้ง SL + TP
                # ══════════════════════════════════════════════
                action_type = "SL+TP"

                # --- SL: Anti-Hunt → ATR → Fixed % ---
                if atr > 0 and candles is not None and len(candles) >= 20:
                    from app.risk.anti_hunt_sl import apply_anti_hunt_sl
                    new_sl = apply_anti_hunt_sl(
                        df=candles,
                        close=entry,
                        atr=atr,
                        direction=direction,
                        atr_mult=self.sl_atr_multiplier,
                        min_sl_distance=atr * 0.5,
                    )
                    new_sl = round(new_sl, digits)
                    method = "anti_hunt"
                elif atr > 0:
                    sl_dist = atr * self.sl_atr_multiplier
                    new_sl = round(entry - sl_dist if is_buy else entry + sl_dist, digits)
                    method = "atr"
                else:
                    sl_dist = entry * self.emergency_sl_pct
                    new_sl = round(entry - sl_dist if is_buy else entry + sl_dist, digits)
                    method = "fixed_pct"

                # --- TP: Smart TP Engine (4 layers) ---
                if tp <= 0:
                    new_tp = self._compute_smart_tp(
                        candles=candles, entry=entry, sl_price=new_sl,
                        atr=atr, direction=direction, digits=digits,
                    )

            elif tp <= 0:
                # ══════════════════════════════════════════════
                # Case B: มี SL แต่ไม่มี TP → ตั้ง TP อย่างเดียว
                # ══════════════════════════════════════════════
                action_type = "TP_only"
                method = "smart_tp"

                new_tp = self._compute_smart_tp(
                    candles=candles, entry=entry, sl_price=sl,
                    atr=atr, direction=direction, digits=digits,
                )

            # ─── ส่ง modify ───
            if self.mt5 and (new_sl != sl or new_tp != tp):
                # ส่ง SL + TP พร้อมกัน (modify_sl รับ tp ด้วย)
                final_sl = new_sl if new_sl > 0 else sl
                final_tp = new_tp if new_tp > 0 else tp
                success = self.mt5.modify_sl(ticket, final_sl, final_tp)

                if success:
                    self._protected[ticket] = time.time()
                    actions.append({
                        "ticket": ticket,
                        "symbol": symbol,
                        "sl_set": final_sl,
                        "tp_set": final_tp,
                        "method": method,
                        "action_type": action_type,
                        "atr": round(atr, digits) if atr > 0 else 0,
                        "action": "manual_trade_protected",
                    })
                    logger.warning("manual_trade_protected", extra={
                        "ticket": ticket,
                        "symbol": symbol,
                        "entry": entry,
                        "sl_set": final_sl,
                        "tp_set": final_tp,
                        "method": method,
                        "action_type": action_type,
                        "atr": round(atr, digits) if atr > 0 else 0,
                        "stage": "manual_protect",
                        "result": "ok",
                    })
                else:
                    logger.error("manual_trade_protect_failed", extra={
                        "ticket": ticket,
                        "symbol": symbol,
                        "action_type": action_type,
                        "stage": "manual_protect",
                        "result": "error",
                    })

        self._cleanup_stale()
        return actions

    def _compute_smart_tp(
        self, candles, entry: float, sl_price: float,
        atr: float, direction: str, digits: int = 2,
    ) -> float:
        """
        คำนวณ TP อัจฉริยะ — ใช้ Smart TP Engine (4 layers).

        Fallback chain:
            Smart TP Engine → ATR-based → SL distance × RR ratio
        """
        sl_distance = abs(entry - sl_price)

        # ลอง Smart TP Engine ก่อน (preferred)
        try:
            from app.risk.smart_tp import apply_smart_tp
            tp = apply_smart_tp(
                df=candles,
                entry=entry,
                sl_price=sl_price,
                atr=atr if atr > 0 else sl_distance,
                direction=direction,
                min_rr=1.5,
                target_rr=self.tp_rr_ratio,
                auto_optimize_rr=True,
                enable_swing=(candles is not None and len(candles) >= 50),
                enable_round_magnet=True,
            )
            return round(tp, digits)
        except Exception as e:
            logger.debug("smart_tp_fallback", extra={"error": str(e)})

        # Fallback: SL distance × RR ratio
        tp_dist = sl_distance * self.tp_rr_ratio
        if direction == "BUY":
            return round(entry + tp_dist, digits)
        else:
            return round(entry - tp_dist, digits)

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


# ====================================================================
# TakeProfitManager — บริหาร TP แบบ Dynamic (Partial/Dynamic/Trailing)
# ====================================================================

@dataclass
class TPTier:
    """กฎ partial TP 1 ชั้น."""
    r_target: float            # กำไรกี่ R ถึงจะ trigger
    close_pct: float           # ปิดกี่ % ของ original volume (0.3 = 30%)
    move_sl_to: str | None = "be"  # "be" = entry | "prev_tp" = R ก่อนหน้า | None = ไม่ย้าย


@dataclass
class TPConfig:
    """Configuration สำหรับ TP management ของ 1 ticket."""
    mode: str = "off"               # "off" | "partial" | "dynamic" | "trailing_tp"
    # Partial TP tiers
    tiers: list[TPTier] = field(default_factory=lambda: [
        TPTier(r_target=1.0, close_pct=0.3, move_sl_to="be"),        # +1R → close 30% + BE
        TPTier(r_target=2.0, close_pct=0.3, move_sl_to="prev_tp"),   # +2R → close 30%
        TPTier(r_target=3.0, close_pct=1.0, move_sl_to=None),        # +3R → close all
    ])
    # Dynamic TP — adjust TP based on ATR
    atr_tp_multiplier: float = 3.0
    # Trailing TP — TP ถอยมาเมื่อราคาใกล้แล้วย้อน
    trailing_tp_atr_distance: float = 0.5  # TP จะถอยมา ATR × N เมื่อราคาเข้าใกล้
    created_at: float = field(default_factory=time.time)


@dataclass
class TPState:
    """State ปัจจุบันของ TP management ต่อ ticket."""
    highest_tier_hit: int = -1       # tier ล่าสุดที่ trigger
    original_volume: float = 0.0     # volume ตอนเปิด
    remaining_volume: float = 0.0    # volume ที่เหลือ
    closest_to_tp: float = 0.0       # ราคาที่ใกล้ TP ที่สุด (สำหรับ trailing_tp)
    tp_retracted: bool = False       # TP ถอยมาแล้วหรือยัง
    created_at: float = field(default_factory=time.time)


class TakeProfitManager:
    """
    บริหาร TP แบบ Dynamic — 3 modes.

    Modes:
        - partial:     ปิดบางส่วนตาม R-target + ย้าย SL อัตโนมัติ
        - dynamic:     ย้าย TP ตาม ATR × multiplier (ขยาย/ดึง)
        - trailing_tp: TP ถอยมาเมื่อราคาใกล้แล้วย้อน (กัน miss TP)
        - off:         ไม่ทำอะไร

    ตัวอย่าง Partial TP:
        entry=2000, SL=1990 (1R=10), volume=0.10
        +1R (2010) → ปิด 30% (0.03) + SL → 2000 (BE)
        +2R (2020) → ปิด 30% (0.03) + SL → 2010 (+1R)
        +3R (2030) → ปิดที่เหลือ 40% (0.04)

    กฎ:
        - SL ย้ายได้แค่ทิศทางดีขึ้น
        - Volume ที่ปิดต้อง >= volume_min ของ symbol
        - Bounded memory: cleanup entries เก่ากว่า 24 ชม.
    """

    def __init__(self, mt5_client=None) -> None:
        self.mt5 = mt5_client
        self._configs: dict[int, TPConfig] = {}    # ticket → config
        self._states: dict[int, TPState] = {}       # ticket → state

    def set_tp(self, ticket: int, config: TPConfig, volume: float = 0.0) -> None:
        """ตั้ง TP management config ให้ ticket."""
        # Sort tiers by r_target ascending
        if config.tiers:
            config.tiers.sort(key=lambda t: t.r_target)
        self._configs[ticket] = config
        self._states[ticket] = TPState(
            original_volume=volume,
            remaining_volume=volume,
        )
        logger.debug("tp_manager_set", extra={
            "ticket": ticket,
            "mode": config.mode,
            "tiers": len(config.tiers) if config.tiers else 0,
            "volume": volume,
        })

    def remove_tp(self, ticket: int) -> None:
        """ยกเลิก TP management สำหรับ ticket."""
        self._configs.pop(ticket, None)
        self._states.pop(ticket, None)
        logger.info("tp_manager_removed", extra={"ticket": ticket})

    def get_status(self, ticket: int) -> dict:
        """ดึงสถานะ TP management ของ ticket."""
        cfg = self._configs.get(ticket)
        state = self._states.get(ticket)
        if not cfg:
            return {"active": False, "mode": "off"}
        return {
            "active": True,
            "mode": cfg.mode,
            "highest_tier": state.highest_tier_hit if state else -1,
            "original_volume": state.original_volume if state else 0,
            "remaining_volume": state.remaining_volume if state else 0,
            "total_tiers": len(cfg.tiers) if cfg.tiers else 0,
        }

    def check_all(
        self,
        positions: list[dict],
        atr_values: dict[str, float] | None = None,
    ) -> list[dict]:
        """
        ตรวจทุก position ที่มี TP config → execute partial close / adjust TP.

        Args:
            positions: list ของ position dicts จาก MT5
            atr_values: {symbol: atr_value} สำหรับ dynamic/trailing_tp

        Returns:
            list ของ actions ที่ทำ [{ticket, action, ...}]
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
                continue

            entry = pos["price_open"]
            current = pos["price_current"]
            sl = pos["sl"]
            tp = pos.get("tp", 0.0)
            is_buy = pos["type"] == "BUY"
            symbol = pos["symbol"]
            current_volume = pos["volume"]

            # อัปเดต remaining_volume จาก MT5 จริง (อาจถูกปิด partial แล้ว)
            state.remaining_volume = current_volume

            sl_distance = abs(entry - sl) if sl > 0 else 0
            if sl_distance <= 0:
                continue

            if is_buy:
                profit_distance = current - entry
            else:
                profit_distance = entry - current

            # ========== PARTIAL TP ==========
            if cfg.mode == "partial" and cfg.tiers:
                tier_actions = self._check_partial_tp(
                    ticket=ticket, pos=pos, cfg=cfg, state=state,
                    sl_distance=sl_distance, profit_distance=profit_distance,
                )
                actions.extend(tier_actions)

            # ========== DYNAMIC TP ==========
            elif cfg.mode == "dynamic":
                dynamic_action = self._check_dynamic_tp(
                    ticket=ticket, pos=pos, cfg=cfg, state=state,
                    atr_values=atr_values,
                )
                if dynamic_action:
                    actions.append(dynamic_action)

            # ========== TRAILING TP ==========
            elif cfg.mode == "trailing_tp":
                trail_action = self._check_trailing_tp(
                    ticket=ticket, pos=pos, cfg=cfg, state=state,
                    atr_values=atr_values,
                )
                if trail_action:
                    actions.append(trail_action)

        self._cleanup_stale()
        return actions

    def _check_partial_tp(
        self, ticket: int, pos: dict, cfg: TPConfig,
        state: TPState, sl_distance: float, profit_distance: float,
    ) -> list[dict]:
        """ตรวจ partial TP tiers — ปิดบางส่วนตาม R-target."""
        actions = []
        entry = pos["price_open"]
        sl = pos["sl"]
        is_buy = pos["type"] == "BUY"
        symbol = pos["symbol"]

        for i, tier in enumerate(cfg.tiers):
            if i <= state.highest_tier_hit:
                continue  # tier นี้ trigger แล้ว

            required_profit = sl_distance * tier.r_target
            if profit_distance < required_profit:
                break  # ยังไม่ถึง tier นี้

            # ─── คำนวณ volume ที่จะปิด ───
            if tier.close_pct >= 1.0:
                # ปิดทั้งหมดที่เหลือ
                close_vol = state.remaining_volume
            else:
                close_vol = round(state.original_volume * tier.close_pct, 8)
                # ตรวจไม่ให้เกิน remaining
                close_vol = min(close_vol, state.remaining_volume)

            if close_vol <= 0:
                continue

            # ─── ส่ง partial close ───
            success = False
            if self.mt5:
                if close_vol >= state.remaining_volume:
                    success = self.mt5.close_position(
                        ticket, reason=f"TP_T{i+1}_{tier.r_target}R",
                    )
                else:
                    success = self.mt5.partial_close(
                        ticket, close_vol,
                        reason=f"TP_T{i+1}_{tier.r_target}R",
                    )

            if success:
                state.highest_tier_hit = i
                state.remaining_volume = round(
                    state.remaining_volume - close_vol, 8,
                )

                action_result = {
                    "ticket": ticket,
                    "symbol": symbol,
                    "action": "partial_tp_triggered",
                    "tier": i,
                    "r_target": tier.r_target,
                    "close_pct": tier.close_pct,
                    "closed_volume": close_vol,
                    "remaining_volume": state.remaining_volume,
                }

                # ─── ย้าย SL ตาม tier rule ───
                if tier.move_sl_to and state.remaining_volume > 0:
                    new_sl = None
                    if tier.move_sl_to == "be":
                        new_sl = entry
                    elif tier.move_sl_to == "prev_tp":
                        # SL → ราคาที่ = R ก่อนหน้า
                        prev_r = cfg.tiers[i-1].r_target if i > 0 else 0
                        lock_dist = sl_distance * prev_r
                        new_sl = (entry + lock_dist) if is_buy else (entry - lock_dist)

                    if new_sl is not None:
                        # ตรวจ SL ห้ามถอย
                        should_move = False
                        if is_buy and new_sl > sl:
                            should_move = True
                        elif not is_buy and (sl <= 0 or new_sl < sl):
                            should_move = True

                        if should_move:
                            digits = pos.get("digits", 2)
                            new_sl = round(new_sl, digits if isinstance(digits, int) else 2)
                            sl_success = self.mt5.modify_sl(
                                ticket, new_sl, pos.get("tp", 0.0),
                            )
                            if sl_success:
                                action_result["sl_moved_to"] = new_sl
                                action_result["sl_reason"] = tier.move_sl_to

                actions.append(action_result)
                logger.info("partial_tp_triggered", extra={
                    "ticket": ticket,
                    "symbol": symbol,
                    "tier": i,
                    "r_target": tier.r_target,
                    "closed_volume": close_vol,
                    "remaining": state.remaining_volume,
                    "sl_moved": action_result.get("sl_moved_to"),
                    "stage": "partial_tp",
                    "result": "ok",
                })

        return actions

    def _check_dynamic_tp(
        self, ticket: int, pos: dict, cfg: TPConfig,
        state: TPState, atr_values: dict[str, float],
    ) -> dict | None:
        """Dynamic TP — ย้าย TP ตาม ATR × multiplier."""
        symbol = pos["symbol"]
        entry = pos["price_open"]
        is_buy = pos["type"] == "BUY"
        current_tp = pos.get("tp", 0.0)

        atr = atr_values.get(symbol)
        if not atr or atr <= 0:
            return None

        tp_distance = atr * cfg.atr_tp_multiplier
        if is_buy:
            new_tp = entry + tp_distance
            # TP ห้ามถอย (ขยายได้อย่างเดียว)
            if current_tp > 0 and new_tp <= current_tp:
                return None
        else:
            new_tp = entry - tp_distance
            if current_tp > 0 and new_tp >= current_tp:
                return None

        digits = pos.get("digits", 2)
        new_tp = round(new_tp, digits if isinstance(digits, int) else 2)

        if self.mt5:
            sl = pos.get("sl", 0.0)
            success = self.mt5.modify_sl(ticket, sl, new_tp)
            if success:
                logger.info("dynamic_tp_adjusted", extra={
                    "ticket": ticket,
                    "symbol": symbol,
                    "old_tp": current_tp,
                    "new_tp": new_tp,
                    "atr": atr,
                    "mult": cfg.atr_tp_multiplier,
                    "stage": "dynamic_tp",
                    "result": "ok",
                })
                return {
                    "ticket": ticket,
                    "symbol": symbol,
                    "action": "dynamic_tp_adjusted",
                    "old_tp": current_tp,
                    "new_tp": new_tp,
                }

        return None

    def _check_trailing_tp(
        self, ticket: int, pos: dict, cfg: TPConfig,
        state: TPState, atr_values: dict[str, float],
    ) -> dict | None:
        """
        Trailing TP — TP ถอยมาเมื่อราคาใกล้ TP แล้วย้อน.

        Logic:
            1. Track ราคาที่ใกล้ TP มากที่สุด
            2. เมื่อราคาเริ่มย้อนจาก peak → ย้าย TP มาใกล้ขึ้น
            3. = กันไม่ให้ miss TP เมื่อราคาเกือบถึงแล้วกลับตัว
        """
        symbol = pos["symbol"]
        entry = pos["price_open"]
        current = pos["price_current"]
        is_buy = pos["type"] == "BUY"
        current_tp = pos.get("tp", 0.0)

        if current_tp <= 0:
            return None

        atr = atr_values.get(symbol)
        if not atr or atr <= 0:
            return None

        trailing_buffer = atr * cfg.trailing_tp_atr_distance

        # Track closest to TP
        if is_buy:
            distance_to_tp = current_tp - current
            if distance_to_tp < (state.closest_to_tp or float('inf')):
                state.closest_to_tp = distance_to_tp

            # ถ้าราคาเคยใกล้ TP แล้วย้อนลง → ย้าย TP มาใกล้ขึ้น
            if (state.closest_to_tp > 0
                and distance_to_tp > state.closest_to_tp + trailing_buffer
                and not state.tp_retracted):
                new_tp = current + trailing_buffer
                if new_tp < current_tp:
                    state.tp_retracted = True
                else:
                    return None
            else:
                return None
        else:
            distance_to_tp = current - current_tp
            if distance_to_tp < (state.closest_to_tp or float('inf')):
                state.closest_to_tp = distance_to_tp

            if (state.closest_to_tp > 0
                and distance_to_tp > state.closest_to_tp + trailing_buffer
                and not state.tp_retracted):
                new_tp = current - trailing_buffer
                if new_tp > current_tp:
                    state.tp_retracted = True
                else:
                    return None
            else:
                return None

        digits = pos.get("digits", 2)
        new_tp = round(new_tp, digits if isinstance(digits, int) else 2)

        if self.mt5:
            sl = pos.get("sl", 0.0)
            success = self.mt5.modify_sl(ticket, sl, new_tp)
            if success:
                logger.info("trailing_tp_retracted", extra={
                    "ticket": ticket,
                    "symbol": symbol,
                    "old_tp": current_tp,
                    "new_tp": new_tp,
                    "closest": state.closest_to_tp,
                    "stage": "trailing_tp",
                    "result": "ok",
                })
                return {
                    "ticket": ticket,
                    "symbol": symbol,
                    "action": "trailing_tp_retracted",
                    "old_tp": current_tp,
                    "new_tp": new_tp,
                }

        return None

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
            logger.debug("tp_manager_cleanup", extra={"removed": len(stale)})

    def reset(self) -> None:
        """รีเซ็ต TP management ทั้งหมด."""
        self._configs.clear()
        self._states.clear()
        logger.info("tp_manager_reset")
