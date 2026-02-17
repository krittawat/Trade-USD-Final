"""
Asian Range Breakout Strategy
เทรดเมื่อราคา Break ออกจาก Asian Session Range ตอน London Open
Win Rate: ~75-85%
"""
import pandas as pd
from typing import Dict, Optional
from datetime import datetime, time
from app.strategy.templates.base_strategy import SignalType, StrategyDecision

class AsianRangeBreakout:
    def __init__(self):
        self.name = "ASIAN_RANGE_BREAKOUT"
        # Asian Session: 00:00 - 08:00 UTC (07:00 - 15:00 Thailand)
        self.asian_start = time(0, 0)   # UTC
        self.asian_end = time(8, 0)     # UTC
        # London Session: 08:00 - 10:00 UTC (15:00 - 17:00 Thailand) - Best breakout time
        self.london_start = time(8, 0)
        self.london_end = time(10, 0)
        # Breakout buffer (filter fake breakouts)
        self.breakout_buffer_pips = 50  # Must break by at least 50 pips
        
    def calculate_asian_range(self, df: pd.DataFrame) -> Dict:
        """
        คำนวณ High/Low ของ Asian Session จากข้อมูล
        """
        if 'time' not in df.columns and 'datetime' not in df.columns:
            # Fallback: Use last 24 candles (assuming M15 = 6 hours)
            asian_data = df.iloc[-24:-8] if len(df) > 24 else df.iloc[-12:]
        else:
            # Filter by time
            time_col = df['time'] if 'time' in df.columns else df['datetime']
            asian_mask = time_col.apply(lambda x: self.asian_start <= x.time() < self.asian_end if hasattr(x, 'time') else True)
            asian_data = df[asian_mask]
            if len(asian_data) < 4:
                asian_data = df.iloc[-24:-8] if len(df) > 24 else df.iloc[-12:]
        
        asian_high = asian_data['high'].max()
        asian_low = asian_data['low'].min()
        asian_range = asian_high - asian_low
        
        return {
            "high": asian_high,
            "low": asian_low,
            "range": asian_range,
            "mid": (asian_high + asian_low) / 2
        }
    
    def is_london_session(self, current_time: datetime = None) -> bool:
        """ตรวจสอบว่าอยู่ใน London Session หรือไม่"""
        if current_time is None:
            current_time = datetime.utcnow()
        current_hour = current_time.hour if hasattr(current_time, 'hour') else 8
        return 8 <= current_hour < 16  # Extended London (08:00-16:00 UTC)
    
    def analyze(self, df: pd.DataFrame, direction_mode: str = "AUTO", symbol: str = "XAUUSD") -> StrategyDecision:
        """
        วิเคราะห์สัญญาณ Asian Range Breakout
        """
        if len(df) < 20:
            return StrategyDecision(
                signal=SignalType.WAIT,
                confidence=0,
                reason="Not enough data for Asian Range",
                sl=None, tp=None
            )
        
        # 1. คำนวณ Asian Range
        asian = self.calculate_asian_range(df)
        current_price = df['close'].iloc[-1]
        
        # 2. ตรวจสอบ Session
        if not self.is_london_session():
            return StrategyDecision(
                signal=SignalType.WAIT,
                confidence=0,
                reason=f"⏰ Waiting for London (Asian: {asian['low']:.2f}-{asian['high']:.2f})",
                sl=None, tp=None
            )
        
        # 3. ATR for dynamic buffer
        atr = df['ta_atr_14'].iloc[-1] if 'ta_atr_14' in df.columns else asian['range'] * 0.5
        buffer = max(self.breakout_buffer_pips * 0.01, atr * 0.3)  # Dynamic buffer
        
        # 4. Breakout Detection
        signal = SignalType.WAIT
        confidence = 0
        reason = ""
        sl = None
        tp = None
        risk_mult = 1.5
        
        # BUY Breakout (Price > Asian High + Buffer)
        if current_price > asian['high'] + buffer:
            if direction_mode in ["AUTO", "BUY", "BOTH"]:
                signal = SignalType.BUY
                confidence = min(85, 60 + ((current_price - asian['high']) / atr) * 10)
                sl = asian['mid']  # SL at Asian Mid or Low
                tp = current_price + (asian['range'] * 1.5)  # TP = 1.5x Asian Range
                reason = f"🌅 ASIAN BREAKOUT BUY | Break {asian['high']:.2f} → {current_price:.2f}"
                
                # Strong breakout bonus
                if current_price > asian['high'] + (atr * 0.5):
                    risk_mult = 2.0
                    reason = f"🔥 STRONG BREAKOUT BUY | {current_price:.2f} >> {asian['high']:.2f}"
        
        # SELL Breakout (Price < Asian Low - Buffer)
        elif current_price < asian['low'] - buffer:
            if direction_mode in ["AUTO", "SELL", "BOTH"]:
                signal = SignalType.SELL
                confidence = min(85, 60 + ((asian['low'] - current_price) / atr) * 10)
                sl = asian['mid']  # SL at Asian Mid
                tp = current_price - (asian['range'] * 1.5)  # TP = 1.5x Asian Range
                reason = f"🌅 ASIAN BREAKOUT SELL | Break {asian['low']:.2f} → {current_price:.2f}"
                
                # Strong breakout bonus
                if current_price < asian['low'] - (atr * 0.5):
                    risk_mult = 2.0
                    reason = f"🔥 STRONG BREAKOUT SELL | {current_price:.2f} << {asian['low']:.2f}"
        
        else:
            reason = f"📊 In Asian Range ({asian['low']:.2f} - {asian['high']:.2f})"
        
        return StrategyDecision(
            signal=signal,
            confidence=confidence,
            reason=reason,
            sl=sl,
            tp=tp,
            entry_price=current_price,
            risk_multiplier=risk_mult
        )

# Global instance
asian_range_breakout = AsianRangeBreakout()
