"""
Position Guardian — ผู้พิทักษ์ทุก Position (Bot + Manual).

Unified coordinator ที่ดูแล SL/TP/Trailing Stop/Profit Lock ให้ทุก position
ที่เปิดอยู่ ไม่ว่าจะเป็น bot orders หรือ manual orders (magic=0).

Flow (เรียกทุก cycle จาก _check_positions()):
  1. Protect manual trades: ตั้ง SL/TP ให้ manual orders ที่ยังไม่มี
  2. Auto-enroll: ลงทะเบียน trailing + profit-lock ให้ position ที่ยังไม่มี config
  3. Trailing Stop: ลาก SL ตามกำไร (5 modes: atr/fixed/step/chandelier/adaptive)
  4. Profit Lock: ล็อคกำไรเป็นชั้น (multi-tier)
  5. TP Management: partial close / dynamic TP / trailing TP

กฎ:
  - SL ห้ามถอยกลับ (monotonic — enforced ใน managers)
  - ทำงานกับทุก mode (LIVE/DRY_RUN)
  - Bounded memory: cleanup stale entries อัตโนมัติ
  - Log ทุก action ชัดเจน (no silent blocks)

Performance (8GB RAM):
  - Per-ticket dict → bounded with 24h cleanup
  - No unbounded buffers
"""

import time
from typing import Optional

from app.core.logging import get_logger
from app.risk.trade_manager import (
    TrailingStopManager, TrailingConfig,
    ProfitLockManager, ProfitLockConfig, ProfitLockTier,
    ManualTradeProtector,
    TakeProfitManager, TPConfig, TPTier,
)

logger = get_logger(__name__)

# ─── Symbol detection helpers ───
_GOLD_KEYWORDS = ("XAU", "GOLD")
_JPY_KEYWORDS = ("JPY",)
_BTC_KEYWORDS = ("BTC",)
_SILVER_KEYWORDS = ("XAG", "SILVER")


def _get_digits(symbol: str) -> int:
    """ดึง digits ที่เหมาะกับ symbol สำหรับ SL rounding."""
    s = symbol.upper()
    if any(k in s for k in _GOLD_KEYWORDS):
        return 2
    if any(k in s for k in _SILVER_KEYWORDS):
        return 3
    if any(k in s for k in _BTC_KEYWORDS):
        return 2
    if any(k in s for k in _JPY_KEYWORDS):
        return 3
    return 5  # default forex 5 digits

# ─── Per-Symbol Optimized Trailing Configs ───
# ปรับ parameters ให้เหมาะสมกับลักษณะ volatility ของแต่ละคู่
# Key: keyword ใน symbol name → config dict
_SYMBOL_TRAILING_PROFILES: dict[str, dict] = {
    # ═══════════════════════════════════════════════════════════════
    # GOLD (XAUUSDc) — High volatility, fast moves, $X0/$X5 magnets
    # ═══════════════════════════════════════════════════════════════
    # วิ่งเร็ว wig เยอะ → dynamic mode เริ่มเร็ว (+0.5R)
    # Phase 1: ล็อค BE ทันที, Phase 2: step ทีละ 0.5R, Phase 3: ATR บีบ
    # anti_hunt_dodge_pts=2.0 (Gold ราคาแพง เลขกลม $10/$5 ดึงดูดมาก)
    "XAU": {
        "mode": "structure",
        "activation_r": 0.5,           # เริ่มเร็ว +0.5R (wig หลอกบ่อย ต้องล็อคไว)
        "step_r": 0.5,                 # Phase 2: ทีละ 0.5R
        "atr_multiplier": 2.0,         # Phase 3: ATR×2 (Gold ATR ~$15-25, trail ~$30-50)
        "dynamic_phase2_r": 1.0,       # เข้า step trailing ที่ +1R
        "dynamic_phase3_r": 2.0,       # เข้า ATR tightening ที่ +2R
        "adaptive_min_mult": 1.0,      # บีบสุด ATR×1
        "adaptive_max_mult": 2.5,      # กว้างสุด ATR×2.5
        "adaptive_ramp_r": 5.0,        # บีบเต็มที่ +5R
        "anti_hunt": True,
        "anti_hunt_dodge_pts": 2.0,    # Gold: dodge กว้างกว่า (เลขกลม $10/$5 ดึงดูดมาก)
    },
    "GOLD": {  # alias
        "mode": "structure", "activation_r": 0.5, "step_r": 0.5,
        "atr_multiplier": 2.0, "dynamic_phase2_r": 1.0, "dynamic_phase3_r": 2.0,
        "adaptive_min_mult": 1.0, "adaptive_max_mult": 2.5, "adaptive_ramp_r": 5.0,
        "anti_hunt": True, "anti_hunt_dodge_pts": 2.0,
    },

    # ═══════════════════════════════════════════════════════════════
    # SILVER (XAGUSDc) — คล้าย Gold แต่ ATR/ราคา ratio สูงกว่า
    # ═══════════════════════════════════════════════════════════════
    # Spread กว้างกว่า Gold → activation ช้าลงนิด (+0.7R)
    # Step ใหญ่กว่า (0.5R) เพราะ noise เยอะ
    "XAG": {
        "mode": "structure",
        "activation_r": 0.7,           # ช้ากว่า Gold (spread กว้างกว่า)
        "step_r": 0.5,                 # ทีละ 0.5R
        "atr_multiplier": 2.5,         # Silver volatility สูง → trail กว้างขึ้น
        "dynamic_phase2_r": 1.0,
        "dynamic_phase3_r": 2.5,       # เข้า Phase 3 ช้ากว่า (wick เยอะ)
        "adaptive_min_mult": 1.2,      # บีบสุด ATR×1.2 (Silver wig เยอะ ห้ามบีบแน่นเกิน)
        "adaptive_max_mult": 3.0,
        "adaptive_ramp_r": 5.0,
        "anti_hunt": True,
        "anti_hunt_dodge_pts": 1.5,
    },
    "SILVER": {  # alias
        "mode": "structure", "activation_r": 0.7, "step_r": 0.5,
        "atr_multiplier": 2.5, "dynamic_phase2_r": 1.0, "dynamic_phase3_r": 2.5,
        "adaptive_min_mult": 1.2, "adaptive_max_mult": 3.0, "adaptive_ramp_r": 5.0,
        "anti_hunt": True, "anti_hunt_dodge_pts": 1.5,
    },

    # ═══════════════════════════════════════════════════════════════
    # BTC (BTCUSDc) — Extreme volatility, huge wicks, 24/7
    # ═══════════════════════════════════════════════════════════════
    # Wick ใหญ่มาก → adaptive mode (ATR บีบตามกำไร แต่ไม่ใช้ step เพราะจะโดน wick กิน)
    # activation ช้า +1.5R (ป้องกัน wick หลอก)
    "BTC": {
        "mode": "adaptive",
        "activation_r": 1.5,           # เริ่มช้า (BTC wig 1-2% บ่อย)
        "atr_multiplier": 3.0,         # เริ่มกว้าง ATR×3 (safety margin)
        "adaptive_min_mult": 1.5,      # บีบสุด ATR×1.5 (BTC wig ใหญ่ห้ามแน่นเกิน)
        "adaptive_max_mult": 3.5,      # กว้างสุด ATR×3.5
        "adaptive_ramp_r": 4.0,        # บีบเต็มที่ +4R
        "anti_hunt": True,
        "anti_hunt_dodge_pts": 50.0,   # BTC: dodge $50 จากเลขกลม ($X000)
    },

    # ═══════════════════════════════════════════════════════════════
    # JPY Pairs (USDJPYc) — Medium volatility, JPY round numbers
    # ═══════════════════════════════════════════════════════════════
    # เลขกลม JPY (X.000, X.500) ดึงดูดมาก → dynamic + anti-hunt เฉพาะ JPY
    # ATR M15 ~0.15-0.30 → multiplier พอประมาณ
    "JPY": {
        "mode": "dynamic",
        "activation_r": 0.8,           # medium activation (JPY volatility ปานกลาง)
        "step_r": 0.5,
        "atr_multiplier": 2.5,
        "dynamic_phase2_r": 1.0,
        "dynamic_phase3_r": 2.0,
        "adaptive_min_mult": 1.0,
        "adaptive_max_mult": 2.5,
        "adaptive_ramp_r": 4.0,
        "anti_hunt": True,
        "anti_hunt_dodge_pts": 0.15,   # JPY: dodge 15 pips จาก .000/.500
    },

    # ═══════════════════════════════════════════════════════════════
    # EUR/USD (EURUSDc) — Low volatility, tight spread, precise
    # ═══════════════════════════════════════════════════════════════
    # Noise น้อย → dynamic mode ที่กระชับขึ้น (Phase 3 เร็วกว่า)
    # Step เล็กกว่า 0.3R (capture profit granular)
    "EUR": {
        "mode": "dynamic",
        "activation_r": 0.7,           # เริ่มเร็วพอ (vol ต่ำ wig เล็ก)
        "step_r": 0.3,                 # ทีละ 0.3R (granular — EUR vol ต่ำ)
        "atr_multiplier": 2.0,
        "dynamic_phase2_r": 0.8,       # เข้า step เร็วกว่า
        "dynamic_phase3_r": 1.5,       # เข้า ATR เร็วกว่า (trend ยาว EUR ค่อยๆ ไป)
        "adaptive_min_mult": 1.0,
        "adaptive_max_mult": 2.0,
        "adaptive_ramp_r": 3.0,
        "anti_hunt": True,
        "anti_hunt_dodge_pts": 0.0003, # EUR: dodge 3 pips จาก .X000/.X500
    },

    # ═══════════════════════════════════════════════════════════════
    # GBP Pairs — Higher volatility than EUR, wider wig
    # ═══════════════════════════════════════════════════════════════
    "GBP": {
        "mode": "dynamic",
        "activation_r": 0.8,           # กว่า EUR นิดนึง (GBP vol สูงกว่า)
        "step_r": 0.4,                 # ทีละ 0.4R
        "atr_multiplier": 2.5,
        "dynamic_phase2_r": 1.0,
        "dynamic_phase3_r": 2.0,
        "adaptive_min_mult": 1.0,
        "adaptive_max_mult": 2.5,
        "adaptive_ramp_r": 4.0,
        "anti_hunt": True,
        "anti_hunt_dodge_pts": 0.0004, # GBP: dodge 4 pips
    },
}


def _get_symbol_trailing_config(
    symbol: str,
    mode: str = "step",
    activation_r: float = 1.0,
    step_r: float = 0.5,
    atr_mult: float = 2.5,
    regime: str = "",
    structure_fractal_window: int = 5,
    structure_buffer_points: float = 1.0,
) -> TrailingConfig:
    """
    สร้าง TrailingConfig ที่ปรับให้เหมาะสมที่สุดกับ symbol + regime.

    ลำดับค้นหา:
      1. ค้นจาก _SYMBOL_TRAILING_PROFILES (per-symbol optimized)
      2. Fallback → dynamic mode + anti-hunt (default ปลอดภัย)
      3. Apply Regime Override (adjust activation/atr per market state)

    Parameters:
      mode, activation_r, step_r, atr_mult = fallback defaults
      regime = current market regime (TRENDING_UP, RANGING, VOLATILE, etc.)
    """
    s = symbol.upper()

    # ─── ค้นจาก profile table ───
    cfg_dict = {}
    for keyword, profile in _SYMBOL_TRAILING_PROFILES.items():
        if keyword in s:
            cfg_dict = dict(profile)
            break

    # ─── Build base config ───
    if cfg_dict:
        final_activation_r = cfg_dict.get("activation_r", activation_r)
        final_atr_mult = cfg_dict.get("atr_multiplier", atr_mult)
        final_anti_hunt = cfg_dict.get("anti_hunt", True)
    else:
        cfg_dict = {
            "mode": "dynamic",
            "step_r": step_r,
            "dynamic_phase2_r": 1.0,
            "dynamic_phase3_r": 2.0,
            "adaptive_min_mult": 1.0,
            "adaptive_max_mult": 3.0,
            "adaptive_ramp_r": 5.0,
            "anti_hunt_dodge_pts": 1.5,
        }
        final_activation_r = activation_r
        final_atr_mult = atr_mult
        final_anti_hunt = True

    # ─── Regime Override Layer ───
    regime_upper = regime.upper() if regime else ""
    regime_adjusted = False

    if "TREND" in regime_upper:
        # Trending → เร่ง lock profit (activation เร็วขึ้น)
        final_activation_r *= 0.7
        regime_adjusted = True
    elif "RANG" in regime_upper or "SIDEWAYS" in regime_upper:
        # Ranging → กว้างขึ้น ลดโดน stop
        final_activation_r *= 1.3
        regime_adjusted = True
    elif "VOLATI" in regime_upper or "BREAKOUT" in regime_upper:
        # Volatile → ATR กว้างขึ้น + anti-hunt บังคับ
        final_atr_mult *= 1.3
        final_anti_hunt = True
        regime_adjusted = True

    if regime_adjusted:
        logger.info("trailing_regime_adjusted", extra={
            "symbol": symbol, "regime": regime,
            "activation_r": round(final_activation_r, 3),
            "atr_multiplier": round(final_atr_mult, 2),
            "anti_hunt": final_anti_hunt,
        })

    return TrailingConfig(
        mode=cfg_dict.get("mode", "dynamic"),
        activation_r=final_activation_r,
        step_r=cfg_dict.get("step_r", step_r),
        atr_multiplier=final_atr_mult,
        dynamic_phase2_r=cfg_dict.get("dynamic_phase2_r", 1.0),
        dynamic_phase3_r=cfg_dict.get("dynamic_phase3_r", 2.0),
        adaptive_min_mult=cfg_dict.get("adaptive_min_mult", 1.0),
        adaptive_max_mult=cfg_dict.get("adaptive_max_mult", 3.0),
        adaptive_ramp_r=cfg_dict.get("adaptive_ramp_r", 5.0),
        structure_fractal_window=structure_fractal_window,
        structure_buffer_points=structure_buffer_points,
        anti_hunt=final_anti_hunt,
        anti_hunt_dodge_pts=cfg_dict.get("anti_hunt_dodge_pts", 1.5),
    )


def _parse_profit_lock_tiers(tiers_str: str) -> list[ProfitLockTier]:
    """
    Parse profit lock tiers จาก config string.

    Format: "1.0:0.0,2.0:0.5,3.0:0.75"
    = +1R lock 0% (BE), +2R lock 50%, +3R lock 75%
    """
    tiers = []
    for pair in tiers_str.split(","):
        parts = pair.strip().split(":")
        if len(parts) == 2:
            try:
                r_mult = float(parts[0])
                lock_pct = float(parts[1])
                tiers.append(ProfitLockTier(r_multiple=r_mult, lock_pct=lock_pct))
            except (ValueError, TypeError):
                continue
    if not tiers:
        # Default fallback
        tiers = [
            ProfitLockTier(r_multiple=1.0, lock_pct=0.0),
            ProfitLockTier(r_multiple=2.0, lock_pct=0.5),
            ProfitLockTier(r_multiple=3.0, lock_pct=0.75),
        ]
    return tiers


class PositionGuardian:
    """
    Unified Position Guardian — ดูแล SL/TP/Trailing/Profit-Lock ให้ทุก position.

    ใช้ TrailingStopManager, ProfitLockManager, ManualTradeProtector, TakeProfitManager
    จาก trade_manager.py เป็น engine ภายใน

    LifeCycle:
      - สร้าง 1 ครั้ง ใน MasterLoop.__init__()
      - เรียก process_all() ทุก cycle จาก _check_positions()
    """

    def __init__(
        self,
        mt5_client=None,
        settings=None,
    ) -> None:
        self.mt5 = mt5_client
        self.settings = settings

        # ─── Sub-managers (engine) ───
        self.trailing_mgr = TrailingStopManager(mt5_client)  # profit_lock_mgr ตั้งทีหลัง
        self.profit_lock_mgr = ProfitLockManager(mt5_client)
        # เชื่อม trailing กับ profit lock เพื่อให้ SL ห้ามต่ำกว่า locked tier
        self.trailing_mgr._profit_lock_mgr = self.profit_lock_mgr
        self.tp_mgr = TakeProfitManager(mt5_client)
        self.manual_protector = ManualTradeProtector(
            mt5_client,
            emergency_sl_pct=getattr(settings, 'manual_sl_pct', 0.02),
            sl_atr_multiplier=getattr(settings, 'sl_atr_multiplier', 2.0),
            tp_rr_ratio=getattr(settings, 'manual_tp_rr', 2.0),
        )

        # ─── Config from settings ───
        self._trailing_mode = getattr(settings, 'guardian_trailing_mode', 'step')
        self._trailing_activation_r = getattr(settings, 'guardian_trailing_activation_r', 1.0)
        self._trailing_step_r = getattr(settings, 'guardian_trailing_step_r', 0.5)
        self._trailing_atr_mult = getattr(settings, 'guardian_trailing_atr_mult', 2.5)
        self._trailing_structure_fractal_window = getattr(settings, 'guardian_trailing_structure_fractal_window', 5)
        self._trailing_structure_buffer_points = getattr(settings, 'guardian_trailing_structure_buffer_points', 1.0)
        self._profit_lock_enabled = getattr(settings, 'guardian_profit_lock_enabled', True)
        self._profit_lock_tiers_str = getattr(
            settings, 'guardian_profit_lock_tiers', '1.0:0.0,2.0:0.5,3.0:0.75'
        )
        self._manual_trailing = getattr(settings, 'guardian_manual_trailing', True)
        self._protect_manual = getattr(settings, 'protect_manual_trades', True)

        # ─── TP Management config ───
        self._tp_mode = getattr(settings, 'tp_management_mode', 'off')

        # ─── Track enrolled tickets → avoid re-enrollment spam ───
        self._enrolled_trailing: set[int] = set()
        self._enrolled_lock: set[int] = set()
        self._enrolled_tp: set[int] = set()

        # ─── Cleanup tracking ───
        self._enrollment_times: dict[int, float] = {}  # ticket → timestamp
        self._cleanup_interval = 500
        self._call_counter = 0
        self._max_entries = 10_000
        self._cleanup_age = 86_400  # 24h

        logger.info("position_guardian_initialized", extra={
            "trailing_mode": self._trailing_mode,
            "activation_r": self._trailing_activation_r,
            "step_r": self._trailing_step_r,
            "profit_lock_enabled": self._profit_lock_enabled,
            "manual_trailing": self._manual_trailing,
            "tp_mode": self._tp_mode,
        })

    # ────────────────────────────────────────────────────────────
    # Main Entry Point
    # ────────────────────────────────────────────────────────────

    def process_all(
        self,
        positions: list[dict],
        atr_values: dict[str, float] | None = None,
        candles_cache: dict | None = None,
        regime_map: dict[str, str] | None = None,
    ) -> dict:
        """
        Main entry point — เรียกจาก _check_positions() ทุก cycle.

        Flow:
          1. Protect manual trades (ตั้ง SL/TP ให้ manual ที่ไม่มี)
          2. Auto-enroll ทุก position เข้า trailing + profit-lock + TP
          3. Run trailing stop (ลาก SL)
          4. Run profit lock (ล็อคกำไร)
          5. Run TP management (partial/dynamic/trailing TP)

        Args:
            positions: list ของ position dicts จาก MT5
            atr_values: {symbol: atr_value}
            candles_cache: {symbol: DataFrame}
            regime_map: {symbol: regime_string} for regime-adaptive trailing

        Returns:
            summary dict: counts of each action type
        """
        self._call_counter += 1
        atr_values = atr_values or {}
        regime_map = regime_map or {}
        candles_cache = candles_cache or {}

        result = {
            "enrolled_trailing": 0,
            "enrolled_lock": 0,
            "enrolled_tp": 0,
            "trailing_actions": 0,
            "lock_actions": 0,
            "tp_actions": 0,
            "manual_protected": 0,
        }

        if not positions:
            return result

        # ─── Step 1: Protect manual trades (ตั้ง SL/TP ให้ manual ที่ไม่มี) ───
        if self._protect_manual:
            protect_actions = self.manual_protector.protect_unguarded(
                positions, candles_cache
            )
            result["manual_protected"] = len(protect_actions)

            for a in protect_actions:
                logger.info("guardian_manual_protected", extra={
                    "ticket": a.get("ticket"),
                    "symbol": a.get("symbol"),
                    "sl": a.get("sl_set"),
                    "tp": a.get("tp_set"),
                    "method": a.get("method"),
                    "stage": "guardian",
                    "result": "ok",
                })

        # ─── Step 2: Auto-enroll all positions ───
        enrolled = self._auto_enroll_all(positions, regime_map)
        result["enrolled_trailing"] = enrolled["trailing"]
        result["enrolled_lock"] = enrolled["lock"]
        result["enrolled_tp"] = enrolled["tp"]

        # ─── Step 3: Trailing Stop (ลาก SL ตามกำไร) ───
        trailing_actions = self.trailing_mgr.update_all(
            positions, atr_values, candles_cache
        )
        result["trailing_actions"] = len(trailing_actions)

        # ─── Step 4: Profit Lock (ล็อคกำไรเป็นชั้น) ───
        if self._profit_lock_enabled:
            lock_actions = self.profit_lock_mgr.check_all(positions)
            result["lock_actions"] = len(lock_actions)

        # ─── Step 5: TP Management ───
        tp_actions = self.tp_mgr.check_all(positions, atr_values)
        result["tp_actions"] = len(tp_actions)

        # ─── Periodic cleanup ───
        if self._call_counter % self._cleanup_interval == 0:
            self._cleanup_stale(positions)

        # ─── Summary log (every 100 calls) ───
        if self._call_counter % 100 == 0:
            total_actions = (
                result["trailing_actions"]
                + result["lock_actions"]
                + result["tp_actions"]
                + result["manual_protected"]
            )
            logger.info("guardian_summary", extra={
                "cycle": self._call_counter,
                "positions": len(positions),
                "enrolled": enrolled,
                "actions": total_actions,
                "tracking": {
                    "trailing": len(self._enrolled_trailing),
                    "lock": len(self._enrolled_lock),
                    "tp": len(self._enrolled_tp),
                },
            })

        return result

    # ────────────────────────────────────────────────────────────
    # Auto-Enrollment
    # ────────────────────────────────────────────────────────────

    def _auto_enroll_all(self, positions: list[dict], regime_map: dict[str, str] | None = None) -> dict:
        """
        Auto-enroll ทุก position ที่ยังไม่มี trailing / profit-lock / TP config.

        กฎ:
          - ทุก position ที่มี SL > 0 จะถูก enroll (ไม่ว่า magic เท่าไหร่)
          - Manual trades (magic=0) ก็ได้ trailing + profit-lock (ถ้า config enable)
          - Position ที่ไม่มี SL จะถูกข้าม (safety — ต้องตั้ง SL ก่อน)
        """
        counts = {"trailing": 0, "lock": 0, "tp": 0}

        for pos in positions:
            ticket = pos["ticket"]
            sl = pos.get("sl", 0.0)
            magic = pos.get("magic", -1)

            # ─── ข้าม position ที่ไม่มี SL ───
            if sl <= 0:
                continue

            # ─── Check if manual trade and manual trailing is disabled ───
            is_manual = (magic == 0)
            if is_manual and not self._manual_trailing:
                continue

            symbol = pos["symbol"]

            # ─── Enroll trailing stop ───
            if ticket not in self._enrolled_trailing:
                sym_regime = (regime_map or {}).get(symbol, "")
                cfg = _get_symbol_trailing_config(
                    symbol,
                    mode=self._trailing_mode,
                    activation_r=self._trailing_activation_r,
                    step_r=self._trailing_step_r,
                    atr_mult=self._trailing_atr_mult,
                    regime=sym_regime,
                    structure_fractal_window=self._trailing_structure_fractal_window,
                    structure_buffer_points=self._trailing_structure_buffer_points,
                )
                self.trailing_mgr.set_trailing(ticket, cfg)
                self._enrolled_trailing.add(ticket)
                self._enrollment_times[ticket] = time.time()
                counts["trailing"] += 1

                logger.debug("guardian_enrolled_trailing", extra={
                    "ticket": ticket,
                    "symbol": symbol,
                    "mode": cfg.mode,
                    "manual": is_manual,
                    "stage": "guardian",
                    "result": "ok",
                })

            # ─── Enroll profit lock ───
            if self._profit_lock_enabled and ticket not in self._enrolled_lock:
                tiers = _parse_profit_lock_tiers(self._profit_lock_tiers_str)
                lock_cfg = ProfitLockConfig(tiers=tiers)
                self.profit_lock_mgr.set_profit_lock(ticket, lock_cfg)
                self._enrolled_lock.add(ticket)
                counts["lock"] += 1

                logger.debug("guardian_enrolled_lock", extra={
                    "ticket": ticket,
                    "symbol": symbol,
                    "tiers": len(tiers),
                    "manual": is_manual,
                    "stage": "guardian",
                    "result": "ok",
                })

            # ─── Enroll TP management ───
            if self._tp_mode != "off" and ticket not in self._enrolled_tp:
                tp_cfg = self._build_tp_config()
                volume = pos.get("volume", 0.0)
                self.tp_mgr.set_tp(ticket, tp_cfg, volume=volume)
                self._enrolled_tp.add(ticket)
                counts["tp"] += 1

                logger.debug("guardian_enrolled_tp", extra={
                    "ticket": ticket,
                    "symbol": symbol,
                    "mode": self._tp_mode,
                    "manual": is_manual,
                    "stage": "guardian",
                    "result": "ok",
                })

        return counts

    def _build_tp_config(self) -> TPConfig:
        """สร้าง TPConfig จาก settings."""
        s = self.settings

        tiers = [
            TPTier(
                r_target=getattr(s, 'tp_partial_tier1_r', 1.0),
                close_pct=getattr(s, 'tp_partial_tier1_pct', 0.3),
                move_sl_to="be",
            ),
            TPTier(
                r_target=getattr(s, 'tp_partial_tier2_r', 2.0),
                close_pct=getattr(s, 'tp_partial_tier2_pct', 0.3),
                move_sl_to="prev_tp",
            ),
            TPTier(
                r_target=getattr(s, 'tp_partial_tier3_r', 3.0),
                close_pct=getattr(s, 'tp_partial_tier3_pct', 1.0),
                move_sl_to=None,
            ),
        ]

        return TPConfig(
            mode=self._tp_mode,
            tiers=tiers,
            atr_tp_multiplier=getattr(s, 'tp_dynamic_atr_mult', 3.0),
            trailing_tp_atr_distance=getattr(s, 'tp_trailing_distance_atr', 0.5),
        )

    # ────────────────────────────────────────────────────────────
    # Cleanup
    # ────────────────────────────────────────────────────────────

    def _cleanup_stale(self, positions: list[dict]) -> None:
        """
        Cleanup:
          1. ลบ ticket ที่ปิดแล้ว (ไม่อยู่ใน active positions)
          2. ลบ entries เก่ากว่า 24 ชม.
          3. Hard cap ที่ 10,000 entries
        """
        active_tickets = {p["ticket"] for p in positions}

        # ลบ ticket ที่ปิดแล้ว
        closed = self._enrolled_trailing - active_tickets
        for t in closed:
            self._enrolled_trailing.discard(t)
            self._enrolled_lock.discard(t)
            self._enrolled_tp.discard(t)
            self._enrollment_times.pop(t, None)
            self.trailing_mgr.remove_trailing(t)
            self.profit_lock_mgr.remove_profit_lock(t)
            self.tp_mgr.remove_tp(t)

        if closed:
            logger.debug("guardian_cleanup_closed", extra={
                "removed": len(closed),
                "remaining": len(self._enrolled_trailing),
            })

        # ลบ entries เก่ากว่า 24 ชม.
        if len(self._enrollment_times) > self._max_entries:
            cutoff = time.time() - self._cleanup_age
            stale = [t for t, ts in self._enrollment_times.items() if ts < cutoff]
            for t in stale:
                self._enrolled_trailing.discard(t)
                self._enrolled_lock.discard(t)
                self._enrolled_tp.discard(t)
                self._enrollment_times.pop(t, None)
            if stale:
                logger.debug("guardian_cleanup_stale", extra={"removed": len(stale)})

    # ────────────────────────────────────────────────────────────
    # Status API
    # ────────────────────────────────────────────────────────────

    def get_status(self, ticket: int) -> dict:
        """ดึงสถานะการดูแลของ ticket."""
        return {
            "enrolled": ticket in self._enrolled_trailing,
            "trailing": self.trailing_mgr.get_status(ticket),
            "profit_lock": self.profit_lock_mgr.get_status(ticket),
            "tp": self.tp_mgr.get_status(ticket),
        }

    def get_summary(self) -> dict:
        """ดึง summary ของ Guardian ทั้งหมด."""
        return {
            "enrolled_trailing": len(self._enrolled_trailing),
            "enrolled_lock": len(self._enrolled_lock),
            "enrolled_tp": len(self._enrolled_tp),
            "trailing_mode": self._trailing_mode,
            "profit_lock_enabled": self._profit_lock_enabled,
            "tp_mode": self._tp_mode,
            "manual_trailing": self._manual_trailing,
        }

    def reset(self) -> None:
        """รีเซ็ตทุกอย่าง (ใช้ตอนเริ่มวันใหม่)."""
        self._enrolled_trailing.clear()
        self._enrolled_lock.clear()
        self._enrolled_tp.clear()
        self._enrollment_times.clear()
        self.manual_protector.reset()
        self.tp_mgr.reset()
        logger.info("guardian_reset")
