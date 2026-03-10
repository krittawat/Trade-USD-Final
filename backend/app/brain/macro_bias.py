"""
Macro-Economic Directional Bias
ใช้สำหรับวิเคราะห์ข่าวเศรษฐกิจที่มีผลกระทบแรง (High Impact)
แล้วแปลงเป็น Directional Bias (LONG_ONLY / SHORT_ONLY / ANY)
เพื่อให้บอทเลือกเล่นฝั่งที่ได้เปรียบทาง Macro เท่านั้น
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.core.logging import get_logger
from app.services.news_filter import SYMBOL_CURRENCIES

logger = get_logger(__name__)

# คำศัพท์ที่หากปรากฏในชื่อข่าวแล้ว ค่า Actual > Forecast จะส่งผล "ลบ" ต่อสกุลเงินนั้นๆ
# ปกติแล้ว Actual > Forecast ส่งผล "บวก" (เช่น GDP, CPI, Non-Farm)
INVERSE_KEYWORDS = [
    "unemployment",
    "jobless",
    "claims",
    "deficit",
    "trade balance" # Usually negative numbers, we need to be careful, but we'll try basic parsing
]

class MacroBiasEngine:
    """ประเมินทิศทางหลักจากข่าว Macro เศรษฐกิจที่เพิ่งประกาศใน 4-8 ชั่วโมงที่ผ่านมา"""

    def __init__(self, impact_hours_validity: int = 4):
        self.impact_hours = impact_hours_validity

    def _parse_value(self, val_str: str) -> Optional[float]:
        """แปลง String เช่น '3.4%', '200K', '-4.5B' เป็นตัวเลขเพียวๆ เพื่อเปรียบเทียบ"""
        if not val_str or isinstance(val_str, float) or val_str.strip() == "":
            return None
            
        # ลบ comma และตัวอักษรบางตัว
        cleaned = re.sub(r'[^\d\.\-]', '', str(val_str).replace(',', ''))
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _evaluate_event(self, event: dict) -> int:
        """
        ประเมิน 1 ข่าว ว่าตีความเข้าข้างสกุลเงิน (+1) หรือเป็นผลเสียต่อสกุลเงิน (-1)
        ถ้าไม่แน่ใจหรือยังไม่ประกาศ หรือไม่มีตัวเลข ให้คืนค่า 0
        """
        actual_str = event.get("actual", "")
        forecast_str = event.get("forecast", "")
        # ถ้าไม่มี actual แปลว่าข่าวยังไม่ออก
        if not actual_str:
            return 0
            
        actual_val = self._parse_value(actual_str)
        forecast_val = self._parse_value(forecast_str)
        
        # กรณีไม่มี Forecast ให้เปรียบเทียบกับ Previous แทน
        if forecast_val is None:
            forecast_val = self._parse_value(event.get("previous", ""))

        if actual_val is None or forecast_val is None:
            return 0
            
        title = event.get("title", "").lower()
        
        # Invert Logic
        is_inverse = any(kw in title for kw in INVERSE_KEYWORDS)
        
        diff = actual_val - forecast_val
        if abs(diff) < 1e-6:
            return 0 # เท่ากับที่คาดการณ์ ไม่มีผล
            
        # ถ้า Actual สูงกว่าคาด
        if actual_val > forecast_val:
            return -1 if is_inverse else 1
        # ถ้า Actual ต่ำกว่าคาด
        else:
            return 1 if is_inverse else -1

    def determine_bias(self, symbol: str, news_events: list[dict], now: datetime = None) -> str:
        """
        วิเคราะห์ Bias ของสัญลักษณ์การเทรด (เช่น XAUUSD) จากรายการข่าว (ดึงจาก NewsFilter)
        
        Returns:
            "LONG_ONLY" หรือ "SHORT_ONLY" หรือ "ANY"
        """
        if now is None:
            now = datetime.now(timezone.utc)
            
        currencies = SYMBOL_CURRENCIES.get(symbol, ["USD"])
        
        # ค้นหา base และ quote currency
        # เช่น EURUSD -> base=EUR, quote=USD
        # XAUUSDc -> base=XAU, quote=USD
        base_cur = symbol[:3].upper()
        quote_cur = "USD"
        if len(currencies) > 1:
             # สำหรับ Forex ปกติ
             base_cur = currencies[0]
             quote_cur = currencies[1]
             
        # รวมคะแนน Bias
        bias_score = 0
        events_scored = 0
        
        for event in news_events:
            # เฉพาะข่าว HIGH impact
            if event.get("impact", "").upper() != "HIGH":
                continue
                
            event_currency = event.get("currency", "").upper()
            if event_currency not in currencies:
                continue
                
            event_time = event.get("time")
            if not event_time:
                continue
                
            # เอาเฉพาะข่าวที่ออกไปแล้ว และไม่เกินหน้าต่างเวลา (เช่น 4 ชั่วโมงที่ผ่านมา)
            time_since = (now - event_time).total_seconds()
            if time_since < 0 or time_since > (self.impact_hours * 3600):
                continue
                
            score = self._evaluate_event(event)
            if score == 0:
                continue
                
            # แปลงคะแนนของแต่ละสกุลเงินให้เป็นผลกระทบต่อทิศทางหลักตัวคู่เทรด (Symbol)
            # ตัวอย่าง: สินทรัพย์มี quote เป็น USD (เช่น XAUUSD)
            # ถ้า USD แข็งค่า (score=+1 ของ USD) แปลว่าราคาทอง XAUUSD จะร่วงง -> Symbol Bias = -1
            if event_currency == base_cur:
                bias_score += score
            elif event_currency == quote_cur:
                bias_score -= score
            else:
                # กรณีอื่นๆ ให้ลบ (-) อัตโนมัติเพราะปกติจะอิง USD เป็นหลัก
                bias_score -= score
                
            events_scored += 1
            
        if events_scored == 0 or bias_score == 0:
            return "ANY"
            
        if bias_score > 0:
            return "LONG_ONLY"
        else:
            return "SHORT_ONLY"
