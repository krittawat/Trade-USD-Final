"""
Re-export คลาส TP management จาก trade_manager.

หลีกเลี่ยง circular imports และทำให้ position_health.py สะอาด.
"""

from app.risk.trade_manager import TPTier, TPConfig, TPState, TakeProfitManager

__all__ = ["TPTier", "TPConfig", "TPState", "TakeProfitManager"]
