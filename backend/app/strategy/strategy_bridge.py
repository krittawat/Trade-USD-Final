"""
Strategy Bridge — ชั้น Adapter ที่เชื่อม template strategies เข้ากับ pipeline.

ปัญหา:
    Template strategies ใน templates/ มี 3 แบบ interface:
    1. Pipeline-native: ใช้ app.strategy.base.BaseStrategy → return Decision (ใช้ได้เลย)
    2. Old StrategyDecision: ใช้ templates/base_strategy.BaseStrategy → return StrategyDecision
    3. Standalone: ไม่มี base class, return custom signal objects

    Pipeline ต้องการ: app.strategy.base.BaseStrategy.analyze(candles, profile, regime) → Decision

วิธีแก้:
    Bridge adapters ที่ wrap template strategy → pipeline-compatible interface.
    แปลง StrategyDecision / custom signal → Decision object.

โครงสร้าง:
    _parse_signal()      — แปลง string/enum signal เป็น Action enum (BUY/SELL/HOLD)
    _clamp_confidence()  — Normalize confidence ให้อยู่ในช่วง 0.0 - 1.0
    TemplateBridge       — Adapter สำหรับ strategies ที่ return StrategyDecision
    StandaloneBridge     — Adapter สำหรับ strategies ที่ return custom objects (dict/attrs)
"""

import logging

import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)


# ====================================================================
# Helper Functions — แปลง signal + normalize confidence
# ====================================================================

def _parse_signal(raw_signal: str) -> Action:
    """
    แปลง signal ดิบ → Action enum.

    รองรับหลายรูปแบบ:
        - string: "BUY", "SELL", "HOLD", "WAIT", "NO_TRADE"
        - enum ที่มี .value attribute
    ถ้าแปลงไม่ได้ → default เป็น HOLD (ปลอดภัย)
    """
    # แมป string → Action enum
    mapping = {
        "BUY": Action.BUY,
        "SELL": Action.SELL,
        "HOLD": Action.HOLD,
        "WAIT": Action.HOLD,       # WAIT = ไม่เทรด = HOLD
        "NO_TRADE": Action.HOLD,   # NO_TRADE = ไม่เทรด = HOLD
    }
    # แปลง input เป็น uppercase string
    val = raw_signal.upper() if isinstance(raw_signal, str) else str(raw_signal).upper()
    # ถ้าเป็น enum → ดึง .value มาแปลง
    if hasattr(raw_signal, 'value'):
        val = raw_signal.value.upper()
    return mapping.get(val, Action.HOLD)  # default HOLD ถ้าไม่รู้จัก


def _clamp_confidence(val: float) -> float:
    """
    Normalize confidence ให้อยู่ในช่วง 0.0 - 1.0.

    กรณี:
        val > 1.0  → สมมุติว่าเป็นสเกล 0-100, หาร 100 แล้ว clamp ที่ 1.0
        val <= 1.0 → clamp ระหว่าง 0.0 ถึง 1.0
    """
    if val > 1.0:
        return min(val / 100.0, 1.0)  # แปลงจากสเกล 0-100 → 0-1
    return max(0.0, min(1.0, val))     # clamp ในช่วง [0, 1]


# ====================================================================
# TemplateBridge — Adapter สำหรับ template strategies แบบเก่า
# ====================================================================

class TemplateBridge(BaseStrategy):
    """
    Bridge สำหรับ template strategies ที่ใช้ StrategyDecision interface เก่า.

    แปลงจาก:  analyze(df, direction="AUTO", **kwargs) → StrategyDecision
    เป็น:      analyze(candles, profile, regime) → Decision

    Template strategies ที่ใช้ bridge นี้:
        antichop, gold_sniper_mini, gold_scalp_pro, gold_scalp_daily,
        btc_ultimate_strategy, predicta_strategy, smart_fusion,
        universal_hybrid, vfinal_strategy

    Safety Invariant:
        ❌ ถ้า strategy return BUY/SELL โดยไม่มี SL → BLOCK ทันที (return HOLD)
    """

    def __init__(
        self,
        template_instance,          # instance ของ template strategy เดิม
        strategy_name: str,         # ชื่อ strategy (ใช้เป็น key ลงทะเบียน)
        timeframe: str = "M5",      # timeframe ที่ strategy ใช้วิเคราะห์
        regimes: list[RegimeType] | None = None,  # สภาวะตลาดที่เหมาะ
    ) -> None:
        self._template = template_instance  # เก็บ instance เดิมไว้เรียก analyze()
        self.name = strategy_name
        self.timeframe = timeframe
        # สภาวะตลาดที่ strategy นี้เหมาะสม (ถ้าไม่ระบุ → default trending)
        self.suitable_regimes = regimes or [
            RegimeType.TRENDING_UP,
            RegimeType.TRENDING_DOWN,
        ]

    def analyze(
        self,
        candles: pd.DataFrame,            # ข้อมูลแท่งเทียน OHLCV
        profile: SymbolProfile,           # ข้อมูลสัญลักษณ์ (spread, contract size ฯลฯ)
        regime: RegimeType = RegimeType.UNKNOWN,  # สภาวะตลาดปัจจุบัน
        **kwargs,                         # รับ extra params (session, h1_candles ฯลฯ)
    ) -> Decision:
        """
        วิเคราะห์ตลาดผ่าน template strategy เดิม แล้วแปลงผลเป็น Decision.

        ขั้นตอน:
            1. เรียก template.analyze(df, direction="AUTO")
            2. ดึง signal, confidence, SL, TP จาก StrategyDecision
            3. ตรวจสอบ SL (ต้องมีเสมอ — safety invariant)
            4. คำนวณ R:R ratio ถ้ามี entry + SL + TP
            5. สร้าง Decision object ส่งกลับ
        """
        try:
            # --- เรียก template strategy เดิม ---
            # ลอง signatures หลายแบบ (แต่ละ template อาจรับ args ต่างกัน)
            result = None
            try:
                result = self._template.analyze(df=candles, direction="AUTO")
            except TypeError:
                try:
                    result = self._template.analyze(df=candles, direction_mode="AUTO")
                except TypeError:
                    try:
                        result = self._template.analyze(df=candles)
                    except TypeError:
                        result = self._template.analyze(candles)

            # ถ้า template return None → ไม่มีสัญญาณ
            if result is None:
                return self._hold(profile.symbol, "Template returned None")

            # --- ดึงข้อมูลจาก StrategyDecision (Pydantic model) ---
            raw_signal = getattr(result, 'signal', 'HOLD')  # signal: BUY/SELL/HOLD
            action = _parse_signal(raw_signal)

            # ถ้าเป็น HOLD → ไม่ต้องทำอะไร
            if action == Action.HOLD:
                reason = getattr(result, 'reason', '') or "No actionable signal"
                return self._hold(profile.symbol, reason)

            # --- ดึง confidence + SL/TP/entry ---
            confidence = _clamp_confidence(getattr(result, 'confidence', 0.0))
            sl = getattr(result, 'sl', None) or getattr(result, 'stop_loss', None)  # SL อาจอยู่ในชื่อต่างกัน
            tp = getattr(result, 'tp', None) or getattr(result, 'take_profit', None)
            entry = getattr(result, 'entry_price', None)
            reason = getattr(result, 'reason', '') or f"{self.name} signal"

            # --- ❌ Safety Invariant: BUY/SELL ต้องมี SL เสมอ ---
            if sl is None or sl == 0:
                return self._hold(
                    profile.symbol,
                    f"Template {self.name} returned {action.value} without SL — BLOCKED",
                )

            # --- คำนวณ Risk:Reward ratio ---
            rr = None
            if entry and sl and tp:
                sl_dist = abs(entry - sl)       # ระยะ SL จาก entry
                tp_dist = abs(tp - entry)       # ระยะ TP จาก entry
                rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else None

            # --- สร้าง Decision object มาตรฐาน ---
            return Decision(
                symbol=profile.symbol,
                action=action,
                confidence=confidence,
                reason=reason,
                stop_loss=sl,
                take_profit=tp,
                risk_reward_ratio=rr,
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=[self.name, "bridged"],  # tag ว่าผ่าน bridge มา
            )

        except Exception as e:
            # ❌ ห้าม silent fail — log error + return HOLD
            logger.error("bridge_analyze_error", extra={
                "strategy": self.name,
                "symbol": profile.symbol,
                "error": str(e),
                "type": type(e).__name__,
            }, exc_info=True)
            return self._hold(profile.symbol, f"Bridge error: {e}")

    def _hold(self, symbol: str, reason: str) -> Decision:
        """สร้าง Decision HOLD พร้อมเหตุผล (ใช้เป็น safe default)."""
        return Decision(
            symbol=symbol,
            action=Action.HOLD,
            confidence=0.0,
            reason=reason,
            strategy_name=self.name,
            timeframe=self.timeframe,
        )


# ====================================================================
# StandaloneBridge — Adapter สำหรับ standalone strategies
# ====================================================================

class StandaloneBridge(TemplateBridge):
    """
    Bridge สำหรับ standalone strategies ที่ไม่มี base class.

    Standalone strategies return:
        - dict: {"signal": "BUY", "sl": 2000.0, "confidence": 0.8, ...}
        - custom object: EntrySignal, HyperSignal (มี .signal, .sl, .confidence attrs)

    Strategies ที่ใช้ bridge นี้:
        hyper_scalp, institutional_scalp, sniper_pro, antigravity,
        asian_range_breakout, kill_zone_strategy

    Safety Invariant:
        ❌ BUY/SELL ไม่มี SL → BLOCK ทันที
    """

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,                         # รับ extra params (session, h1_candles ฯลฯ)
    ) -> Decision:
        """
        วิเคราะห์ผ่าน standalone strategy แล้วแปลง custom signal → Decision.

        ขั้นตอน:
            1. ลองเรียก analyze() ด้วย signature หลายแบบ (df+symbol, df เฉยๆ)
            2. ตรวจว่า result เป็น dict หรือ object
            3. ดึง signal, confidence, SL, TP ตามรูปแบบ
            4. ตรวจ SL (safety invariant)
            5. สร้าง Decision object
        """
        try:
            # --- ลองเรียก analyze() ด้วย signatures ที่ต่างกัน ---
            # แต่ละ standalone strategy อาจรับ arguments ไม่เหมือนกัน
            result = None
            try:
                result = self._template.analyze(df=candles, symbol=profile.symbol)
            except TypeError:
                try:
                    result = self._template.analyze(candles)  # positional arg
                except TypeError:
                    result = self._template.analyze(df=candles)  # keyword only

            # ถ้า return None → ไม่มีสัญญาณ
            if result is None:
                return self._hold(profile.symbol, "Standalone returned None")

            # --- แยกกรณี: dict vs object ---
            if isinstance(result, dict):
                # กรณี return dict: {"signal": "BUY", "sl": 2000, ...}
                raw_signal = result.get('signal', result.get('action', 'HOLD'))
                confidence = _clamp_confidence(result.get('confidence', 0.0))
                sl = result.get('sl', result.get('stop_loss'))
                tp = result.get('tp', result.get('take_profit'))
                reason = result.get('reason', '') or f"{self.name} signal"
            else:
                # กรณี return object (EntrySignal, HyperSignal, ฯลฯ)
                # ลองอ่าน attrs หลายชื่อเพราะแต่ละ strategy ตั้งชื่อต่างกัน
                raw_signal = getattr(result, 'signal', getattr(result, 'direction', 'HOLD'))
                confidence = _clamp_confidence(
                    getattr(result, 'confidence', getattr(result, 'score', 0.0))
                )
                sl = getattr(result, 'sl', getattr(result, 'stop_loss', None))
                tp = getattr(result, 'tp', getattr(result, 'take_profit', None))
                reason = getattr(result, 'reason', '') or f"{self.name} signal"

            # แปลง signal string → Action enum
            action = _parse_signal(raw_signal)

            # ถ้า HOLD → ส่ง HOLD กลับ
            if action == Action.HOLD:
                return self._hold(profile.symbol, reason)

            # --- ❌ Safety Invariant: ต้องมี SL ---
            if sl is None or sl == 0:
                return self._hold(
                    profile.symbol,
                    f"Standalone {self.name} returned {action.value} without SL — BLOCKED",
                )

            # --- สร้าง Decision มาตรฐาน ---
            return Decision(
                symbol=profile.symbol,
                action=action,
                confidence=confidence,
                reason=reason,
                stop_loss=sl,
                take_profit=tp,
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=[self.name, "bridged", "standalone"],  # tag ระบุว่ามาจาก standalone
            )

        except Exception as e:
            # ❌ ห้าม silent fail — log + return HOLD
            logger.error("standalone_bridge_error", extra={
                "strategy": self.name,
                "symbol": profile.symbol,
                "error": str(e),
            }, exc_info=True)
            return self._hold(profile.symbol, f"Standalone bridge error: {e}")
