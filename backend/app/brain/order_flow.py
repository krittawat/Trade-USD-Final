"""
Order Flow & Volume Profile Intelligence
สร้าง Volume Profile (VP) จากราคาและ tick volume/ticks 
เพื่อหา Point of Control (POC), Value Area High (VAH), และ Value Area Low (VAL).

หลักการ:
1. แบ่งช่วงราคาของแท่งเทียนที่ดึงมาให้เป็น Bins (ระดับราคา)
2. กระจาย Volume ของแต่ละแท่งเทียนลงไปใน Bins ตามแนวตั้ง
3. POC คือ Bin ที่มี Volume สะสมมากที่สุด
4. VA (Value Area) ปกติ 70% ของ Volume ทั้งหมด 
   - VAH = จุดสูงสุดของ VA 
   - VAL = จุดต่ำสุดของ VA
"""

import numpy as np
import pandas as pd
from typing import Optional, Dict

from app.core.logging import get_logger

logger = get_logger(__name__)

class OrderFlowAnalyzer:
    """
    วิเคราะห์ Order Flow และสร้าง Volume Profile จาก DataFrame ของ MT5.
    """
    
    def __init__(self, num_bins: int = 50, value_area_pct: float = 0.70):
        """
        Args:
            num_bins: จำนวนช่อง (bins) ของราคาที่จะแบ่งในการพล็อตกราฟ Volume Profile
            value_area_pct: เปอร์เซ็นต์ปริมาณการซื้อขายที่จะใช้หาความกว้างของ Value Area (ปกติ 70%)
        """
        self.num_bins = num_bins
        self.value_area_pct = value_area_pct

    def calculate_volume_profile(self, df: pd.DataFrame) -> Optional[Dict[str, float]]:
        """
        คำนวณ Volume Profile จากแท่งเทียน (OHLCV) เพื่อหา POC, VAH, VAL.
        
        Args:
            df: DataFrame แท่งเทียน OHLCV ที่ดึงมาจาก fetch_candles() 
                ต้องมีคอลัมน์ ['high', 'low', 'close', 'volume' หรือ 'tick_volume']
                
        Returns:
            Dict: { 'poc': float, 'vah': float, 'val': float, 'high_node': float, 'low_node': float }
            หรือ None หากคำนวณไม่ได้
        """
        if df is None or df.empty:
            return None

        # หาคอลัมน์ volume
        vol_col = 'volume' if 'volume' in df.columns else 'tick_volume'
        if vol_col not in df.columns:
            logger.warning("volume_col_missing", extra={"columns": list(df.columns)})
            return None

        # หาราคาสูงสุดและต่ำสุดในกรอบเวลา
        high_price = df['high'].max()
        low_price = df['low'].min()
        
        if high_price == low_price:
           return None

        # สร้าง Price Bins
        bins = np.linspace(low_price, high_price, self.num_bins)
        vol_profile = np.zeros(self.num_bins - 1)

        # Iterate ทุกแท่งเทียนและกระจาย Volume
        for _, row in df.iterrows():
            c_high = row['high']
            c_low = row['low']
            c_vol = row[vol_col]
            
            if c_vol <= 0 or c_high == c_low:
                continue

            # หา bin indices ที่แท่งเทียนตัวนี้พาดผ่าน
            idx_start = np.searchsorted(bins, c_low, side='right') - 1
            idx_end = np.searchsorted(bins, c_high, side='left')
            
            # บังคับ ขอบเขตให้อยู่ใน Bins
            idx_start = max(0, idx_start)
            idx_end = min(self.num_bins - 2, idx_end)

            if idx_start == idx_end:
                vol_profile[idx_start] += c_vol
            elif idx_end > idx_start:
                # แบ่ง volume ให้แต่ละ bin ตามสัดส่วนความกว้างของแท่งเทียน
                vol_per_bin = c_vol / (idx_end - idx_start + 1)
                for i in range(idx_start, idx_end + 1):
                    vol_profile[i] += vol_per_bin
                    
        total_vol = np.sum(vol_profile)
        if total_vol <= 0:
            return None

        # หา Point of Control (POC)
        poc_idx = np.argmax(vol_profile)
        # POC price = (ขอบล่างของ bin + ขอบบนของ bin) / 2
        poc_price = (bins[poc_idx] + bins[poc_idx + 1]) / 2.0

        # หา Value Area (VA) 
        target_vol = total_vol * self.value_area_pct
        current_vol = vol_profile[poc_idx]
        
        idx_up = poc_idx + 1
        idx_down = poc_idx - 1
        
        while current_vol < target_vol and (idx_up < len(vol_profile) or idx_down >= 0):
            vol_up = vol_profile[idx_up] if idx_up < len(vol_profile) else 0
            vol_down = vol_profile[idx_down] if idx_down >= 0 else 0
            
            # ขยาย Value Area ไปทางที่มีปริมาณการซึ้อขายมากกว่า
            if vol_up >= vol_down and idx_up < len(vol_profile):
                current_vol += vol_up
                idx_up += 1
            elif idx_down >= 0:
                current_vol += vol_down
                idx_down -= 1
            else:
                break # ควรมี fallback ถ้า error logic (ไม่ควรเกิดขึ้น)
                
        # idx_up และ idx_down คลาดเคลื่อนไป 1 ช่องเนื่องจากการบวก/ลบครั้งสุดท้าย    
        vah_price = bins[min(idx_up, len(bins) - 1)]
        val_price = bins[max(0, idx_down + 1)]

        return {
            'poc': float(poc_price),
            'vah': float(vah_price),
            'val': float(val_price),
            'high_node': float(high_price),
            'low_node': float(low_price),
            'total_volume': float(total_vol)
        }

    def analyze_order_flow(self, symbol: str, df: pd.DataFrame, direction: str) -> dict:
        """
        ประเมิน Order Flow เพื่อบอกว่าการเทรดในทิศทาง (BUY/SELL) ได้เปรียบหรือไม่ 
        เทียบกับตำแหน่งปัจจุบันและ POC ของช่วงราคา
        
        Returns: 
           dict แสดงคำแนะนำ (is_favorable, reason, distances)
        """
        vp = self.calculate_volume_profile(df)
        if not vp:
            return {"is_favorable": True, "reason": "no_vp_data_available", "poc": 0}
            
        current_price = df.iloc[-1]['close']
        poc = vp['poc']
        vah = vp['vah']
        val = vp['val']
        
        # กฎพื้นฐาน Order Flow:
        # ถ้า BUY ให้ระวังแนวต้านปริมาณมหาศาล (POC ที่อยู่ข้างบน)
        # ถ้าราคาอยู่เหนือ POC -> ลอยตัวอิสระ เป็น Favorable สำหรับ BUY
        # ถ้าราคาอยู่ใต้ POC -> POC ทำตัวเป็นกรอบต้าน เป็น Unfavorable สำหรับ BUY หากอยู่ใกล้มาก
        
        is_favorable = True
        reason = "clear_flow"
        dist_to_poc = abs(current_price - poc)
        
        if direction == "BUY":
            if current_price < poc:
                # ราคาอยู่ใต้ POC (ซื้อชนต้าน)
                if current_price >= val: 
                    # ซื้อภายใน Value area ใต้ POC = เสียเปรียบ โดนกด
                    is_favorable = False
                    reason = "buy_into_resistance_poc"
                else: 
                     reason = "buy_below_value_area"  # ซื้อต่ำกว่ากรอบราคา อาจจะเป็น Mean Reversion ได้
            else:
                if current_price <= vah:
                    reason = "buy_inside_value_area_above_poc"
                else:
                    reason = "buy_above_value_area_breakout"

        elif direction == "SELL":
            if current_price > poc:
                # ราคาอยู่เหนือ POC (ขายยัดรับ)
                if current_price <= vah:
                    # ขายใน Value area เหนือ POC = เสียเปรียบ โดนดัน
                    is_favorable = False
                    reason = "sell_into_support_poc"
                else:
                    reason = "sell_above_value_area" # Mean reversion ลงมา
            else:
                if current_price >= val:
                    reason = "sell_inside_value_area_below_poc"
                else:
                    reason = "sell_below_value_area_breakout"
                    
        return {
            "is_favorable": is_favorable,
            "reason": reason,
            "poc": poc,
            "vah": vah,
            "val": val,
            "dist_to_poc": dist_to_poc
        }
