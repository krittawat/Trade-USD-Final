"""
Smart Trailing Stop by Market Structure (Phase 4).

วัตถุประสงค์:
    - จับโครงสร้างราคา Swing High / Swing Low (Fractals) จากแท่งเทียน
    - เลื่อน Trailing Stop Loss ไปซ่อนไว้หลังโครงสร้างล่าสุด แทนที่จะใช้ % หรือ ATR แค่ตัวเดียว
    - ทำให้โดนสะบัดกิน SL ยากขึ้น และสามารถ run trend ได้จนสุด
"""

import pandas as pd
from typing import Optional, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)


class StructuralTrailingGuard:
    """
    วิเคราะห์ Market Structure (Fractals) เพื่อหาจุดวาง SL ที่ปลอดภัยที่สุด.
    """

    def __init__(self, settings):
        self.settings = settings
        # จำนวนแท่งซ้าย-ขวาที่ใช้ยืนยัน Swing Point (ค่ายอดฮิตของ Bill Williams คือ 2)
        # ถ้าน้อยไปจะโดนสะบัดง่าย ถ้ามากไป SL จะตามไม่ทันราคา
        self.fractal_window = getattr(settings, 'trailing_fractal_window', 3) 
        self.buffer_points = getattr(settings, 'trailing_structure_buffer', 20) # เผื่อบัฟเฟอร์กัน spread ถ่าง

    def _find_recent_swings(self, df: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
        """
        แสกนหา Recent Swing Low (สำหรับออเดอร์ BUY) และ Recent Swing High (สำหรับออเดอร์ SELL)
        
        Args:
            df: DataFrame ของหน้าต่างแท่งเทียน (ปกติคือ M5 หรือ M15) - ต้องมี col 'high', 'low'
            
        Returns:
            (swing_high, swing_low) เป็นราคาล่าสุดที่ยืนยันโครงสร้างแล้ว
        """
        w = self.fractal_window
        if df is None or len(df) < w * 2 + 1:
            return None, None

        # หา Fractal High (Swing High)
        # แท่งตรงกลาง(i) ต้องมีจุด high สูงกว่าแท่งซ้าย(w แท่ง) และขวา(w แท่ง)
        df['is_swing_high'] = False
        df['is_swing_low'] = False
        
        # วนลูปหลีกเลี่ยงขอบซ้าย/ขวา
        for i in range(w, len(df) - w):
            # ตรวจ Swing High
            is_highest = True
            for j in range(1, w + 1):
                if df['high'].iloc[i] <= df['high'].iloc[i-j] or df['high'].iloc[i] <= df['high'].iloc[i+j]:
                    is_highest = False
                    break
            if is_highest:
                df.iloc[i, df.columns.get_loc('is_swing_high')] = True
                
            # ตรวจ Swing Low
            is_lowest = True
            for j in range(1, w + 1):
                if df['low'].iloc[i] >= df['low'].iloc[i-j] or df['low'].iloc[i] >= df['low'].iloc[i+j]:
                    is_lowest = False
                    break
            if is_lowest:
                df.iloc[i, df.columns.get_loc('is_swing_low')] = True

        # ดึงราคา Swing ล่าสุดที่เพิ่งเกิดขึ้น (เอาจุดที่ใกล้ปัจจุบันที่สุด แต่ข้าม w แท่งสุดท้ายที่ยังไม่คอนเฟิร์ม)
        swing_highs = df[df['is_swing_high'] == True]
        swing_lows = df[df['is_swing_low'] == True]

        recent_sh = swing_highs['high'].iloc[-1] if not swing_highs.empty else None
        recent_sl = swing_lows['low'].iloc[-1] if not swing_lows.empty else None

        return recent_sh, recent_sl

    def calculate_structural_sl(
        self,
        is_buy: bool,
        current_sl: float,
        entry_price: float,
        candles_df: pd.DataFrame,
        point_scale: float,
    ) -> float:
        """
        คำนวณเป้าหมายระดับ SL ใหม่ หากโครงสร้างราคาอนุญาตให้ดึงขึ้นมาบังได้.
        (SL จะสามารถขยับให้แคบลงได้เท่านั้น ห้ามขยับกว้างขึ้น)
        
        Args:
            is_buy: True ถ้ามีออเดอร์ BUY, False ถ้าออเดอร์ SELL
            current_sl: Stop loss เดิมจาก MT5 ตอนนี้
            entry_price: ราคาที่เปิดออเดอร์
            candles_df: ข้อมูลแท่งเทียนปัจจุบัน
            point_scale: ขนาด 1 point ของสัญลักษณ์นี้ (เช่น 0.001 หรือ 0.01)
            
        Returns:
            ราคา SL ใหม่ที่ปลอดภัยตามโครงสร้าง หรือ current_sl เดิมถ้าไม่ต้องเปลี่ยน
        """
        swing_high, swing_low = self._find_recent_swings(candles_df)
        buffer = self.buffer_points * point_scale
        
        proposed_sl = current_sl

        if is_buy:
            # BUY → ต้องไล่ SL ขึ้นตาม Swing Low ใหม่ที่ยกตัวสูงขึ้น (Higher Lows)
            if swing_low is not None:
                # วาง SL ไว้ใต้ฐาน swing low นิดนึง
                structural_sl = swing_low - buffer
                
                # กฎ 1: SL ใหม่ต้องทำกำไรได้มากกว่า SL เก่า (ห้ามดึงถอยหลัง)
                # กฎ 2: โครงสร้างใหม่อย่างน้อยต้องอยู่เหนือจุดเข้า (ปกป้องทุนแล้ว)
                if structural_sl > current_sl and structural_sl > entry_price:
                    proposed_sl = structural_sl
        else:
            # SELL → ต้องกด SL ลงตาม Swing High ใหม่ที่กดตัวต่ำลง (Lower Highs)
            if swing_high is not None:
                structural_sl = swing_high + buffer
                
                # กฎ 1: SL ใหม่ต้องทำกำไรได้มากกว่า SL เก่า (ห้ามดึงถอยหลัง - ราคาต่ำลงคือกำไรเพิ่ม)
                # กฎ 2: โครงสร้างใหม่ต้องอยู่ต่ำกว่าจุดเข้า
                # Note: current_sl อาจะเป็น 0 ถ้ายอมรับว่าไม่มี SL ตอนแรก หรือตั้งไว้ไกลมาก
                if (current_sl == 0.0) or (structural_sl < current_sl and structural_sl < entry_price):
                    proposed_sl = structural_sl
                    
        return round(proposed_sl, 5) # ปัดทศนิยมให้เนียนกับ MT5
