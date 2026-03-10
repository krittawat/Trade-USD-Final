"""
Behavioral Pattern Detector — ตรวจจับพฤติกรรมตลาดระดับสูง (Context-Aware Patterns).

ต่างจาก PatternDetector ปกติ (ที่เป็นแค่แท่งเทียน) ตรงที่:
1. ใช้ **Time** (Session Open/Close)
2. ใช้ **Personality** (Volatility, Fakeout prob)
3. ใช้ **Regime** (Market State)

Patterns:
- NY_OPEN_REVERSAL: กวาดกินสภาพคล่องช่วงเปิด NY (8:00-9:30 EST)
- LONDON_BREAKOUT_FADE: Breakout หลอกช่วง London Open
- BTC_MOMENTUM: โมเมนตัมต่อเนื่องของ BTC
"""

import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import List, Optional

from app.core.logging import get_logger
from app.domain.models import PatternSignal, PersonalityProfile
from app.domain.enums import RegimeType, Action

logger = get_logger(__name__)


@dataclass
class BehavioralSignal:
    """สัญญาณพฤติกรรมที่ตรวจพบ."""
    name: str                 # ชื่อพฤติกรรม
    action: Action            # BUY/SELL
    confidence: float         # ความั่นใจ 0.0-1.0
    bar_index: int            # แท่งที่เจอ
    description: str          # คำอธิบาย
    timestamp: datetime       # เวลาที่เจอ


class BehavioralPatternDetector:
    """
    ตรวจจับพฤติกรรมตลาดโดยใช้ Context รอบด้าน.
    """
    
    def detect(self, 
               symbol: str, 
               candles: pd.DataFrame, 
               profile: PersonalityProfile,
               regime: RegimeType,
               base_patterns: List[PatternSignal]) -> List[BehavioralSignal]:
        """
        ตรวจจับ Behavioral Patterns.
        
        Args:
            symbol: ชื่อคู่เงิน
            candles: DataFrame
            profile: ข้อมูลนิสัยตลาด
            regime: สภาวะตลาดปัจจุบัน
            base_patterns: patterns พื้นฐานที่เจอ (จาก PatternDetector)
            
        Returns:
            List[BehavioralSignal]
        """
        if candles is None or len(candles) < 20:
            return []

        signals = []
        current_time = candles.iloc[-1]['time']
        if not isinstance(current_time, datetime):
            current_time = pd.to_datetime(current_time)
            
        # --- 1. NY Open Reversal (Gold/Indices) ---
        # เวลา 13:00 - 14:30 UTC (8:00 - 9:30 EST) โดยประมาณ
        # เช็คว่ามีการกวาด High/Low ก่อนหน้าแล้วทิ้งไส้
        if 13 <= current_time.hour <= 14:
            sig = self._detect_ny_reversal(candles, base_patterns, profile)
            if sig: signals.append(sig)

        # --- 2. London Breakout Fade (EURUSD/GBPUSD) ---
        # เวลา 07:00 - 09:00 UTC
        # เช็ค Fakeout breakout
        if 7 <= current_time.hour <= 9:
            sig = self._detect_london_fade(candles, base_patterns, regime)
            if sig: signals.append(sig)
            
        # --- 3. Momentum Continuation (Crypto/Strong Trend) ---
        if regime in [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN]:
            sig = self._detect_momentum_continuation(candles, regime, symbol)
            if sig: signals.append(sig)

        return signals

    def _detect_ny_reversal(self, candles: pd.DataFrame, patterns: List[PatternSignal], profile: PersonalityProfile) -> Optional[BehavioralSignal]:
        """NY Open Reversal: Stop hunt then reverse."""
        # ต้องมี Pin Bar หรือ Engulfing ในทิศตรงข้าม
        reversal_pattern = next((p for p in patterns if p.name in ['hammer', 'shooting_star', 'bullish_engulfing', 'bearish_engulfing']), None)
        
        if not reversal_pattern:
            return None
            
        # ถ้า profile บอกว่าชอบ fakeout ให้เพิ่มน้ำหนัก
        confidence = 0.6
        if profile.fakeout_probability > 0.5:
            confidence += 0.2
            
        direction = Action.BUY if reversal_pattern.direction == 'bullish' else Action.SELL
        
        return BehavioralSignal(
            name="NY_OPEN_REVERSAL",
            action=direction,
            confidence=confidence,
            bar_index=len(candles)-1,
            description=f"NY Open Reversal detected with {reversal_pattern.name}",
            timestamp=candles.iloc[-1]['time']
        )

    def _detect_london_fade(self, candles: pd.DataFrame, patterns: List[PatternSignal], regime: RegimeType) -> Optional[BehavioralSignal]:
        """London Breakout Fade: หาก Breakout แล้วเกิด Reversal ทันที."""
        # ตรวจสอบว่าก่อนหน้านี้มี Breakout ไหม
        # นี่เป็น logic อย่างง่าย
        last_candle = candles.iloc[-1]
        prev_candle = candles.iloc[-2]
        
        # สมมติเช็คแค่ Pinbar ที่เกิดหลังแท่งยาว
        pinbar = next((p for p in patterns if 'pin_bar' in p.name), None)
        if pinbar:
            direction = Action.BUY if pinbar.direction == 'bullish' else Action.SELL
            return BehavioralSignal(
                name="LONDON_BREAKOUT_FADE",
                action=direction,
                confidence=0.75,
                bar_index=len(candles)-1,
                description="Potential fakeout at London Open",
                timestamp=last_candle['time']
            )
        return None

    def _detect_momentum_continuation(self, candles: pd.DataFrame, regime: RegimeType, symbol: str) -> Optional[BehavioralSignal]:
        """Momentum Continuation: เข้าเมื่อย่อตัวในเทรนด์แข็งแกร่ง."""
        # MACD หรือ RSI เช็คประกอบได้
        # ตรงนี้ดูแค่แท่งเทียนเล็กๆ (Pullback) ในเทรนด์
        
        body_sizes = (candles['close'] - candles['open']).abs()
        avg_body = body_sizes.rolling(20).mean().iloc[-1]
        current_body = body_sizes.iloc[-1]
        
        # ถ้าแท่งปัจจุบันเล็ก (พักตัว) และเทรนด์ชัด
        if current_body < avg_body * 0.5:
            if regime == RegimeType.TRENDING_UP:
                 return BehavioralSignal(
                    name=f"{symbol}_MOMENTUM_PULLBACK",
                    action=Action.BUY,
                    confidence=0.65,
                    bar_index=len(candles)-1,
                    description="Bullish pullback in strong trend",
                    timestamp=candles.iloc[-1]['time']
                )
            elif regime == RegimeType.TRENDING_DOWN:
                 return BehavioralSignal(
                    name=f"{symbol}_MOMENTUM_PULLBACK",
                    action=Action.SELL,
                    confidence=0.65,
                    bar_index=len(candles)-1,
                    description="Bearish pullback in strong trend",
                    timestamp=candles.iloc[-1]['time']
                )
        return None
