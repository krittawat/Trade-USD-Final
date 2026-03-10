"""
Smart Strategy Base — Base class ที่ฉลาดขึ้นด้วย Regime & Audit Feedback.

Features:
1. Regime Awareness: รู้สภาวะตลาด (Trend/Sideway/Volatile) จาก RegimeFilter
2. Audit Feedback: อ่านไฟล์ audit_data.json เพื่อเรียนรู้จากความผิดพลาดในอดีต (Bad Session/Loss Streaks)
3. Dynamic Guard: บล็อกการเทรดในสภาวะที่ไม่เหมาะสมโดยอัตโนมัติ
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd
import app.analysis.indicators as ind

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.enums import RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)

class SmartStrategy(BaseStrategy):
    """
    Base Class สำหรับ Strategy ยุคใหม่ (Pro Version).
    เพิ่มความสามารถในการ "รักตัวกลัวตาย" (Risk Averse) และ "เรียนรู้" (Adaptive).
    """

    def __init__(self, settings: Optional[Settings] = None):
        if settings is None:
            from app.core.config import get_settings
            self.settings = get_settings()
        else:
            self.settings = settings
            
        self.audit_data: Dict[str, Any] = {}
        self._load_audit_data()

    def _load_audit_data(self):
        """โหลดข้อมูล Audit จากไฟล์ json เพื่อใช้ในการตัดสินใจ"""
        try:
            # Path to backend/reports/forensics/audit_data.json
            # Assming running from project root or backend dir
            # Try to find the file relative to current file or project root
            
            # Construct path: backend/reports/forensics/audit_data.json
            # This file is in backend/app/strategy/smart_base.py
            # So go up 3 levels to backend/
            
            base_dir = Path(__file__).parent.parent.parent
            audit_path = base_dir / "reports" / "forensics" / "audit_data.json"
            
            if audit_path.exists():
                with open(audit_path, "r", encoding="utf-8") as f:
                    self.audit_data = json.load(f)
                logger.info("smart_strategy_audit_loaded", extra={"path": str(audit_path)})
            else:
                logger.warning("smart_strategy_audit_not_found", extra={"path": str(audit_path)})
        except Exception as e:
            logger.error("smart_strategy_audit_load_error", extra={"error": str(e)})

    def get_regime_metrics(self, candles: pd.DataFrame) -> Dict[str, float]:
        """
        คำนวณ Metrics สำคัญสำหรับ Regime (ADX, ATR Ratio, Range%).
        """
        if len(candles) < 20:
            return {"adx": 0.0, "atr_ratio": 0.0, "range_pct": 0.0}

        # ADX
        adx_df = ta.adx(candles["high"], candles["low"], candles["close"], length=14)
        adx = adx_df["ADX_14"].iloc[-1] if adx_df is not None and not adx_df.empty else 0.0

        # ATR Ratio (Current ATR / SMA(ATR, 20))
        atr = ta.atr(candles["high"], candles["low"], candles["close"], length=14)
        atr_val = atr.iloc[-1] if atr is not None else 0.0
        atr_ma = atr.rolling(20).mean().iloc[-1] if atr is not None else 0.0
        atr_ratio = atr_val / atr_ma if atr_ma > 0 else 1.0

        # Range % (High-Low / Open)
        current_candle = candles.iloc[-1]
        range_pct = (current_candle["high"] - current_candle["low"]) / current_candle["open"] * 100

        return {
            "adx": round(adx, 2),
            "atr_ratio": round(atr_ratio, 2),
            "range_pct": round(range_pct, 4)
        }

    def get_audit_feedback(self, symbol: str) -> Dict[str, Any]:
        """
        ดึงข้อมูล Feedback จาก Audit สำหรับ Symbol นี้
        """
        # Audit data structure logic depends on how audit_agent.py saves it.
        # Assuming simple structure or just global stats for now.
        # If per-symbol is not available, return empty or global.
        
        # NOTE: audit_data.json structure from previous context:
        # { "summary": {...}, "root_causes": { "session_bleed": {...} } }
        
        return self.audit_data

    def is_safe_session(self, current_session: str) -> bool:
        """
        ตรวจสอบว่า Session นี้ปลอดภัยหรือไม่ (ดูจาก Audit Session Bleed).
        ถ้าขาดทุนหนักใน Session นี้ -> ถือว่าไม่ปลอดภัย
        """
        if not self.audit_data:
            return True # No data = Safe (Default)

        root_causes = self.audit_data.get("root_causes", {})
        session_bleed = root_causes.get("session_bleed", {})
        
        # Map session name to typical hours (heuristic)
        # ASIA: 0-8 UTC roughly
        # LONDON: 8-16 UTC roughly
        # NY: 13-21 UTC roughly
        
        session_upper = current_session.upper()
        hours_to_check = []
        
        if "ASIA" in session_upper:
            hours_to_check = [0, 1, 2, 3, 4, 5, 6, 7]
        elif "LONDON" in session_upper:
            hours_to_check = [8, 9, 10, 11, 12, 13, 14, 15]
        elif "NY" in session_upper or "NEW YORK" in session_upper:
            hours_to_check = [13, 14, 15, 16, 17, 18, 19, 20]
            
        # Check if cumulative bleed in these hours is severe (e.g., < -$100)
        total_bleed = 0.0
        for h in hours_to_check:
            # key in json might be string "0", "1", etc.
            pnl = session_bleed.get(str(h), 0.0)
            total_bleed += pnl
            
        # Threshold: -$50 (Configurable ideally, but hardcoded for safety based on typical micro acc)
        if total_bleed < -50.0:
            logger.warning("smart_strategy_unsafe_session", extra={
                "session": current_session,
                "bleed": total_bleed,
                "threshold": -50.0
            })
            return False
            
        return True
