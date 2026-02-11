"""
Broker Specs — ข้อมูลเฉพาะโบรกเกอร์สำหรับคำนวณ lot size.

หน้าที่:
    - แปลง contract size, volume step, stops level
    - ปัดเศษ lot size ตาม volume step ของแต่ละสัญลักษณ์
    - ตรวจสอบ lot size อยู่ในช่วงที่อนุญาต
"""

import math

from app.core.logging import get_logger
from app.domain.models import SymbolProfile

logger = get_logger(__name__)


def round_lot(lot: float, profile: SymbolProfile) -> float:
    """
    ปัดเศษ lot size ตาม volume step ของสัญลักษณ์.
    
    เช่น step=0.01 → lot=0.157 จะถูกปัดเป็น 0.15 (ปัดลง)
    """
    step = profile.volume_step
    if step <= 0:
        return lot
    rounded = math.floor(lot / step) * step
    # ปัดให้อยู่ในช่วง min-max
    rounded = max(profile.volume_min, min(rounded, profile.volume_max))
    return round(rounded, 8)  # ตัดเลขทศนิยมลอย


def calculate_pip_value(profile: SymbolProfile, lot: float = 1.0) -> float:
    """
    คำนวณมูลค่าต่อ pip.
    
    pip value = lot × contract_size × point
    (ปรับตาม account currency ถ้าจำเป็น)
    """
    return lot * profile.contract_size * profile.point


def is_lot_valid(lot: float, profile: SymbolProfile) -> bool:
    """ตรวจสอบว่า lot size อยู่ในช่วงที่โบรกเกอร์อนุญาต."""
    return profile.volume_min <= lot <= profile.volume_max
