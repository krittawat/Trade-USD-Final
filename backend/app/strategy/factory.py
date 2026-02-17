"""
Strategy Factory — โรงงานผลิต Strategy (เลือกและปรับแต่ง strategy ตามสภาวะตลาด).

หน้าที่:
    - Auto-register ทุก strategy จาก templates/ เมื่อเริ่มระบบ
    - เลือก strategy ที่เหมาะกับ symbol + regime + session
    - ใช้ข้อมูลจาก AI Brain เพื่อเลือก strategy ที่ performance ดีที่สุด
    - Validate ทุก strategy ก่อนใช้งาน
    - Return Decision object เท่านั้น — ไม่ส่งออเดอร์เอง

การเลือก strategy (ลำดับความสำคัญ):
    1. AI Brain recommendation → "อะไรที่เคยใช้ได้ผลดีในสภาวะนี้?"
    2. Regime match → หา strategy ที่เหมาะกับ regime (trending/ranging/high-vol)
    3. Session match → ดูจาก session (Asia/London/NY)
    4. Default fallback → ใช้ strategy แรกที่ลงทะเบียน

Components:
    TEMPLATE_REGISTRY  — รายการ template ทั้งหมด + metadata (class, key, tf, regimes, bridge_type)
    StrategyFactory    — เลือก/สร้าง/จัดการ strategies
"""

from typing import Optional

import pandas as pd

from app.domain.enums import Action

from app.core.logging import get_logger
from app.domain.enums import RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)


# ====================================================================
# Template Registry — รายการ strategies ทั้งหมดพร้อม metadata
# ====================================================================
# รูปแบบ: module_name → (class_name, strategy_key, timeframe, regimes, bridge_type)
#
# bridge_type อธิบายว่า strategy ใช้ interface แบบไหน:
#   "native"     → ใช้ BaseStrategy ของ pipeline โดยตรง (ไม่ต้อง bridge)
#   "template"   → ใช้ StrategyDecision interface เก่า (wrap ด้วย TemplateBridge)
#   "standalone"  → ไม่มี base class, return custom objects (wrap ด้วย StandaloneBridge)

TEMPLATE_REGISTRY = {
    # ─── Pipeline-native: ใช้ได้เลย ไม่ต้อง bridge ───
    "scalping": ("ScalpingStrategy", "scalping", "M5",
                 [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN, RegimeType.HIGH_VOLATILITY],
                 "native"),
    "sniper": ("SniperStrategy", "sniper", "M15",
               [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN],
               "native"),
    "trend_rider": ("TrendRiderStrategy", "trend_rider", "H1",
                    [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN],
                    "native"),

    # ─── Old StrategyDecision interface: ใช้ TemplateBridge ───
    "gold_scalp_pro": ("GoldScalpProStrategy", "gold_scalp_pro", "M5",
                       [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN, RegimeType.HIGH_VOLATILITY],
                       "template"),
    "gold_scalp_daily": ("GoldScalpDailyStrategy", "gold_scalp_daily", "M5",
                         [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN],
                         "template"),
    "gold_sniper_mini": ("GoldSniperMiniStrategy", "gold_sniper_mini", "M5",
                         [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN],
                         "template"),
    "antichop": ("AntiChopStrategy", "antichop", "M5",
                 [RegimeType.RANGING, RegimeType.LOW_VOLATILITY],
                 "template"),
    "btc_ultimate_strategy": ("OmniscientOracleStrategy", "btc_ultimate", "M15",
                              [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN],
                              "template"),
    "predicta_strategy": ("PredictaStrategy", "predicta", "M5",
                          [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN],
                          "template"),
    "smart_fusion": ("SmartFusionStrategy", "smart_fusion", "M5",
                     [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN, RegimeType.HIGH_VOLATILITY],
                     "template"),
    "universal_hybrid": ("UniversalHybridStrategy", "universal_hybrid", "M5",
                         [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN, RegimeType.HIGH_VOLATILITY],
                         "template"),
    "vfinal_strategy": ("VFinalStrategy", "vfinal", "M5",
                        [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN],
                        "template"),

    # ─── Standalone: ไม่มี base class → ใช้ StandaloneBridge ───
    "hyper_scalp": ("HyperScalpStrategy", "hyper_scalp", "M1",
                    [RegimeType.HIGH_VOLATILITY, RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN],
                    "standalone"),
    "institutional_scalp": ("InstitutionalScalpStrategy", "institutional_scalp", "M5",
                            [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN],
                            "standalone"),
    "sniper_pro": ("SniperProStrategy", "sniper_pro", "M15",
                   [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN],
                   "standalone"),
    "antigravity": ("AntigravityStrategy", "antigravity", "M5",
                    [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN, RegimeType.HIGH_VOLATILITY],
                    "standalone"),
    "asian_range_breakout": ("AsianRangeBreakout", "asian_breakout", "M15",
                             [RegimeType.RANGING],
                             "standalone"),
    "kill_zone_strategy": ("KillZoneStrategy", "kill_zone", "M15",
                           [RegimeType.HIGH_VOLATILITY],
                           "standalone"),
}


# ====================================================================
# StrategyFactory — โรงงานเลือก + สร้าง strategy
# ====================================================================

class StrategyFactory:
    """
    โรงงานผลิต Strategy — เลือกและสร้าง strategy ที่เหมาะสม.

    วิธีใช้:
        factory = StrategyFactory()
        factory.auto_register()  # สแกนและลงทะเบียน templates ทั้งหมด
        decision = factory.get_decision(candles, profile, regime)

    ภายใน:
        _strategies  — dict เก็บ strategy instances ทั้งหมดที่ลงทะเบียนแล้ว
        _default     — ชื่อ strategy ตัว default (ตัวแรกที่ลงทะเบียน)
    """

    def __init__(self) -> None:
        self._strategies: dict[str, BaseStrategy] = {}  # ชื่อ → instance
        self._default: str | None = None                 # strategy ตัว default

    # ────────────────────────────────────────────────────────────────
    # Auto Register — สแกน templates/ แล้วลงทะเบียนทั้งหมด
    # ────────────────────────────────────────────────────────────────

    def auto_register(self) -> int:
        """
        สแกนและลงทะเบียน strategies ทั้งหมดจาก TEMPLATE_REGISTRY อัตโนมัติ.

        ขั้นตอน:
            1. วนลูปทุก entry ใน TEMPLATE_REGISTRY
            2. Dynamic import module จาก app.strategy.templates.{module_name}
            3. สร้าง instance ของ class
            4. เลือก bridge ตาม bridge_type:
               - "native"     → ลงทะเบียนตรงๆ
               - "template"   → wrap ด้วย TemplateBridge
               - "standalone" → wrap ด้วย StandaloneBridge
            5. ลงทะเบียน strategy
            6. ถ้า import ไม่ได้ → log warning (ไม่ crash ระบบ)

        Returns:
            int — จำนวน strategies ที่ลงทะเบียนสำเร็จ
        """
        from app.strategy.strategy_bridge import TemplateBridge, StandaloneBridge

        registered = 0
        for module_name, (class_name, key, tf, regimes, bridge_type) in TEMPLATE_REGISTRY.items():
            try:
                # --- Dynamic import: โหลด module ตามชื่อ ---
                mod = __import__(
                    f"app.strategy.templates.{module_name}",
                    fromlist=[class_name],
                )
                cls = getattr(mod, class_name)   # ดึง class จาก module
                instance = cls()                  # สร้าง instance

                # --- เลือก bridge ตามประเภท ---
                if bridge_type == "native":
                    # Pipeline-native: ลงทะเบียนตรง ไม่ต้อง wrap
                    self.register(instance)
                elif bridge_type == "template":
                    # StrategyDecision interface เก่า → wrap ด้วย TemplateBridge
                    bridge = TemplateBridge(instance, key, timeframe=tf, regimes=regimes)
                    self.register(bridge)
                elif bridge_type == "standalone":
                    # ไม่มี base class → wrap ด้วย StandaloneBridge
                    bridge = StandaloneBridge(instance, key, timeframe=tf, regimes=regimes)
                    self.register(bridge)

                registered += 1
                logger.info("strategy_auto_registered", extra={
                    "strategy_module": module_name,
                    "key": key,
                    "bridge": bridge_type,
                })

            except Exception as e:
                # ⚠️ import ไม่ได้ → log warning แต่ไม่หยุดระบบ
                logger.warning("strategy_auto_register_failed", extra={
                    "strategy_module": module_name,
                    "class_name": class_name,
                    "error": str(e),
                    "type": type(e).__name__,
                })

        logger.info("auto_register_complete", extra={
            "total_registered": registered,
            "total_available": len(TEMPLATE_REGISTRY),
        })
        return registered

    # ────────────────────────────────────────────────────────────────
    # Register — ลงทะเบียน strategy เดี่ยว
    # ────────────────────────────────────────────────────────────────

    def register(self, strategy: BaseStrategy) -> None:
        """
        ลงทะเบียน strategy ใหม่เข้า factory.

        Args:
            strategy: instance ของ BaseStrategy (หรือ Bridge)

        หมายเหตุ:
            - strategy ตัวแรกที่ลงทะเบียนจะเป็น default อัตโนมัติ
        """
        self._strategies[strategy.name] = strategy
        logger.info("strategy_registered", extra={"strategy_name": strategy.name})

        # strategy แรกที่ลงทะเบียน → ตั้งเป็น default
        if self._default is None:
            self._default = strategy.name

    # ────────────────────────────────────────────────────────────────
    # Select Strategy — เลือก strategy ที่เหมาะที่สุด
    # ────────────────────────────────────────────────────────────────

    def select_strategy(
        self,
        regime: RegimeType = RegimeType.UNKNOWN,
        session: str = "CLOSED",
        brain_recommendation: str | None = None,
    ) -> Optional[BaseStrategy]:
        """
        เลือก strategy ที่เหมาะกับสถานการณ์ปัจจุบัน.

        ลำดับความสำคัญ:
            1. AI Brain recommendation (ถ้ามีและอยู่ใน _strategies)
            2. Strategy ที่มี regime ตรงกับปัจจุบัน
            3. Default strategy (ตัวแรกที่ลงทะเบียน)
            4. None (ถ้าไม่มี strategy เลย)
        """
        # --- 1. ลำดับแรก: AI Brain แนะนำ ---
        if brain_recommendation and brain_recommendation in self._strategies:
            logger.info("strategy_selected_by_brain", extra={
                "strategy": brain_recommendation,
            })
            return self._strategies[brain_recommendation]

        # --- 2. หา strategy ที่ regime ตรง ---
        for name, strategy in self._strategies.items():
            if regime in strategy.suitable_regimes:
                logger.info("strategy_selected_by_regime", extra={
                    "strategy": name,
                    "regime": regime.value,
                })
                return strategy

        # --- 3. Fallback: ใช้ default ---
        if self._default and self._default in self._strategies:
            logger.info("strategy_selected_default", extra={
                "strategy": self._default,
            })
            return self._strategies[self._default]

        # ⚠️ ไม่มี strategy เลย
        logger.warning("no_strategy_available")
        return None

    # ────────────────────────────────────────────────────────────────
    # Get Decision — เลือก strategy แล้ววิเคราะห์ → Decision
    # ────────────────────────────────────────────────────────────────

    def get_decision(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        session: str = "CLOSED",
        brain_recommendation: str | None = None,
    ) -> Decision:
        """
        เลือก strategy → วิเคราะห์ตลาด → return Decision.

        Performance:
            - Brain rec ถูกใช้เลย ไม่ลองตัวอื่น
            - HOLD decisions log ที่ debug level (ลด log noise)
            - Error → HOLD (ห้าม silent fail)
        """
        strategy = self.select_strategy(regime, session, brain_recommendation)

        # ถ้าไม่มี strategy ที่เหมาะ → HOLD
        if strategy is None:
            return Decision(
                symbol=profile.symbol,
                action=Action.HOLD,
                confidence=0.0,
                reason="ไม่มี strategy ที่เหมาะสมกับสภาวะตลาดปัจจุบัน",
            )

        try:
            # วิเคราะห์ตลาดผ่าน strategy ที่เลือก
            decision = strategy.analyze(candles, profile, regime)

            # ─── Performance: HOLD → debug level (ลด log noise 80%+) ───
            if decision.action == Action.HOLD:
                logger.debug("strategy_hold", extra={
                    "symbol": profile.symbol,
                    "strategy": strategy.name,
                    "reason": decision.reason[:80],
                })
            else:
                logger.info("strategy_decision", extra={
                    "symbol": profile.symbol,
                    "strategy": strategy.name,
                    "action": decision.action.value,
                    "confidence": decision.confidence,
                    "stage": "signal",
                    "result": "ok",
                })
            return decision
        except Exception as e:
            # ❌ ห้าม silent fail — log error แล้ว return HOLD
            logger.error("strategy_error", extra={
                "symbol": profile.symbol,
                "strategy": strategy.name,
                "error": str(e),
                "type": type(e).__name__,
                "stage": "signal",
                "result": "error",
            }, exc_info=True)
            return Decision(
                symbol=profile.symbol,
                action=Action.HOLD,
                confidence=0.0,
                reason=f"Strategy error: {e}",
                strategy_name=strategy.name,
            )
