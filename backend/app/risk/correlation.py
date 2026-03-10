"""
Portfolio Correlation Guard — โมดูลป้องกันความเสี่ยงพอร์ตระดับระบบ (Phase 4).

วัตถุประสงค์:
    - ป้องกันการเปิด Position ในทิศทางเดียวกันสำหรับสินทรัพย์ที่เคลื่อนไหวคล้ายกัน
    - ลด Drawdown เวลาเกิด Macro Shock
"""

import pandas as pd
from typing import Dict, List, Tuple
import MetaTrader5 as mt5

from app.core.logging import get_logger
from app.mt5.market_data import fetch_candles
from app.domain.enums import Action

logger = get_logger(__name__)

class CorrelationGuard:
    """
    คำนวณและตรวจสอบ Asset Correlation.
    """
    def __init__(self, settings):
        self.settings = settings
        self.max_allowed_correlation = getattr(settings, 'max_allowed_correlation', 0.8)
        self.correlation_window = getattr(settings, 'correlation_window', 60) # จำนวนแท่งเทียนที่ใช้คำนวณ H1
        self._correlation_cache: Dict[Tuple[str, str], float] = {}
        
    def _fetch_close_prices(self, symbol: str, count: int) -> pd.Series:
        """ดึงราคาปิดมาทำ Series"""
        df = fetch_candles(symbol, timeframe="H1", count=count)
        if df is None or df.empty:
            return pd.Series(dtype=float)
        return df['close']

    def get_correlation(self, symbol_a: str, symbol_b: str) -> float:
        """
        หาค่า Pearson Correlation ระหว่าง 2 Symbol 
        (จะดึงข้อมูลใหม่หรือใช้ใน Memory แล้วแต่ออกแบบ, ตัวนี้ดึงสดเพื่อความแม่นยำ)
        """
        # เรียงชื่อ Symbol เพื่อเป็น Key สลับไปมาได้
        cache_key = tuple(sorted([symbol_a, symbol_b]))
        
        # TODO: Caching if fetched recently in this cycle
        
        series_a = self._fetch_close_prices(symbol_a, self.correlation_window)
        series_b = self._fetch_close_prices(symbol_b, self.correlation_window)
        
        if series_a.empty or series_b.empty or len(series_a) != len(series_b):
            return 0.0 # ตัดสินใจไม่ได้ ถือว่า 0
            
        # สร้าง DataFrame ชั่วคราวมาหา Correlation
        df = pd.DataFrame({symbol_a: series_a.values, symbol_b: series_b.values})
        # คำนวณ % Return แทนราคาตรงๆ เพื่อความแม่นยำทางสถิติ
        returns = df.pct_change().dropna()
        if returns.empty:
            return 0.0
            
        corr = returns[symbol_a].corr(returns[symbol_b])
        
        self._correlation_cache[cache_key] = corr
        return corr

    def is_exposure_allowed(self, target_symbol: str, target_action: Action, open_positions: list) -> Tuple[bool, str]:
        """
        ตรวจสอบว่า Target Symbol ที่กำลังจะเปิด (พร้อมทิศทาง Action)
        สัมพันธ์กับ Open Positions ที่มีอยู่จนเกินค่า Max Allowed หรือไม่
        
        Args:
            target_symbol: สัญลักษณ์ที่กำลังประเมิน (e.g., XAUUSDc)
            target_action: โดนสั่งให้เข้า BUY หรือ SELL
            open_positions: List ของออเดอร์ใน MT5 ปัจจุบัน (ดึงจาก mt5.positions_get())
            
        Returns:
            (Allowed?, Blocking Reason String)
        """
        if not open_positions:
            return True, ""
            
        for pos in open_positions:
            pos_symbol = pos.symbol
            
            # ข้ามตัวเอง
            if pos_symbol == target_symbol:
                continue
                
            pos_action = Action.BUY if pos.type == mt5.ORDER_TYPE_BUY else Action.SELL
            
            # คำนวณ Correlation
            corr = self.get_correlation(target_symbol, pos_symbol)
            
            # กฎเหล็ก:
            # 1. ถ้า Correlation ระหว่าง A กับ B > 0.8 (วิ่งตามกัน)
            #    จะห้ามเปิดหน้าเดียวกัน! (e.g. BUY A + BUY B == ไม่อนุญาต)
            if corr > self.max_allowed_correlation:
                if target_action == pos_action:
                    return False, f"Highly correlated ({corr:.2f}) with open {pos_symbol} {pos_action.value}"
                    
            # 2. ถ้า Correlation ติดลบหนัก < -0.8 (วิ่งสวนกัน)
            #    จะห้ามเปิดหน้าตรงข้ามกัน! (e.g. BUY A + SELL B == ไม่อนุญาต เพราะมันคือการแทงฝั่งเดียวกันโดยพฤตินัย)
            if corr < -self.max_allowed_correlation:
                if target_action != pos_action:
                    return False, f"Inversely correlated ({corr:.2f}) with open {pos_symbol} {pos_action.value}"
                    
        return True, ""
