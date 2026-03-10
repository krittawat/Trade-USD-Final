"""
Market Personality Engine — วิเคราะห์นิสัยเฉพาะตัวของตลาด (Behavioral DNA).

หน้าที่หลัก:
1. คำนวณ Volatility Profile (ATR, Session Volatility)
2. วิเคราะห์ Liquidity Behavior (Wick Ratios, Fakeout Frequency)
3. ตรวจสอบ Trend Persistence (Hurst Exponent)
4. เก็บสถิติ Time-of-Day Sensitivity
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Dict, List, Optional

from app.core.logging import get_logger
from app.domain.enums import MarketSession, RegimeType

logger = get_logger(__name__)


@dataclass
class PersonalityProfile:
    """ข้อมูลนิสัยของคู่เงิน (Behavioral DNA)."""
    symbol: str = ""
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    # --- Volatility Stats ---
    avg_atr_14: float = 0.0          # ATR 14 เฉลี่ย
    volatility_score: float = 0.0    # 0-100 (เทียบกับตัวเองในอดีต)
    session_volatility: Dict[str, float] = field(default_factory=dict)  # Volatility per session
    
    # --- Behavior Stats ---
    fakeout_probability: float = 0.0  # ความน่าจะเป็นที่จะเกิด Fakeout (0.0 - 1.0)
    trend_persistence: float = 0.5    # Hurst Exponent (0.5=Random, >0.5=Trend, <0.5=Mean Revert)
    wick_ratio_avg: float = 0.0       # สัดส่วนไส้เทียนเฉลี่ย (บอกความผันผวนในแท่ง)
    
    # --- Time Stats ---
    active_hours: List[int] = field(default_factory=list)  # ชั่วโมงที่วิ่งแรงสุด
    best_sessions: List[str] = field(default_factory=list) # session ที่น่าเทรดสุด

    # --- Summary ---
    thai_summary: str = ""  # สรุปนิสัยตลาดภาษาไทย


class PersonalityEngine:
    """
    เครื่องยนต์วิเคราะห์นิสัยตลาด.
    ใช้คำนวณ stats จากข้อมูลย้อนหลังเพื่อสร้าง PersonalityProfile.
    """
    
    def __init__(self):
        self._profiles: Dict[str, PersonalityProfile] = {}

    def analyze_symbol(self, symbol: str, candles: pd.DataFrame) -> PersonalityProfile:
        """
        วิเคราะห์นิสัยของ symbol จากข้อมูลแท่งเทียน.
        
        Args:
            symbol: ชื่อคู่เงิน
            candles: DataFrame [time, open, high, low, close, volume]
        
        Returns:
            PersonalityProfile: ผลลัพธ์การวิเคราะห์
        """
        if candles is None or len(candles) < 100:
            logger.warning(f"Not enough data to analyze personality for {symbol}")
            return PersonalityProfile(symbol=symbol)

        # 1. Volatility Profile
        # Ensure 'high', 'low', 'close' are numeric
        high = candles['high'].astype(float)
        low = candles['low'].astype(float)
        close = candles['close'].astype(float)
        open_price = candles['open'].astype(float) # Renamed to avoid keyword conflict

        atr_14_series = self._calculate_atr(high, low, close, period=14)
        avg_atr = float(atr_14_series.mean()) if not atr_14_series.empty else 0.0
        
        # Volatility Score: เทียบ ATR ปัจจุบันกับ ATR เฉลี่ย 300 แท่ง
        current_atr = atr_14_series.iloc[-1] if not atr_14_series.empty else 0.0
        
        long_term = atr_14_series.rolling(window=300).mean()
        long_term_atr = long_term.iloc[-1] if not long_term.empty and not pd.isna(long_term.iloc[-1]) else avg_atr
        
        vol_score = min(100.0, (current_atr / long_term_atr) * 50.0) if long_term_atr > 0 else 50.0

        # Session Volatility (คร่าวๆ จากชั่วโมง)
        # Session Volatility (approximate from hour)
        # Handle time column or index
        if 'time' in candles.columns:
            ts = candles['time']
        elif isinstance(candles.index, pd.DatetimeIndex):
            ts = candles.index.to_series()
        else:
            ts = pd.Series(pd.to_datetime(candles.index), index=candles.index)

        # Ensure datetime
        if not pd.api.types.is_datetime64_any_dtype(ts):
             ts = pd.to_datetime(ts)

        hour = ts.dt.hour
        range_series = high - low
        hourly_range = range_series.groupby(hour).mean()
        
        # Map hour to session (UTC) - This is approximate
        # Asia: 0-8, London: 7-16, NY: 12-21
        def get_session_vol(start, end):
            # Handle wrap around if needed, but safe here
            if start <= end:
                mask = (hourly_range.index >= start) & (hourly_range.index < end)
            else:
                mask = (hourly_range.index >= start) | (hourly_range.index < end)
            
            subset = hourly_range[mask]
            return float(subset.mean()) if not subset.empty else 0.0

        asia_vol = get_session_vol(0, 8)
        london_vol = get_session_vol(7, 16)
        ny_vol = get_session_vol(12, 21)
        
        session_vol = {
            "ASIA": asia_vol,
            "LONDON": london_vol,
            "NEW_YORK": ny_vol
        }

        # 2. Wick Analysis (Liquidity)
        # Wick ratio = (Upper Wick + Lower Wick) / High-Low
        range_len = high - low
        body_len = (close - open_price).abs()
        wick_len = range_len - body_len
        # Avoid division by zero
        wick_ratio_series = wick_len / range_len.replace(0, 1e-9)
        wick_ratio = float(wick_ratio_series.mean())
        
        # 3. Trend Persistence (Hurst Exponent simplified)
        # ใช้ ADX trend strength เป็น proxy ชั่วคราว หรือคำนวณ Hurst จริง
        # ที่นี่ใช้ Hurst แบบง่าย: log(R/S) / log(N)
        hurst = self._calculate_hurst(close.values)

        # 4. Fakeout Probability (Placeholder logic)
        # ถ้า Wick Ratio สูง และ Hurst ต่ำ (Mean Revert) -> Fakeout สูง
        fakeout_prob = 0.0
        if wick_ratio > 0.5:
            fakeout_prob += 0.3
        if hurst < 0.45:
            fakeout_prob += 0.3
        
        # Active Hours (Top 5 hours with max range)
        active_hours = hourly_range.nlargest(5).index.tolist()
        
        # Sort sessions
        sorted_sessions = sorted(session_vol.items(), key=lambda item: item[1], reverse=True)
        best_sessions = [k for k, v in sorted_sessions]

        profile = PersonalityProfile(
            symbol=symbol,
            avg_atr_14=avg_atr,
            volatility_score=vol_score,
            session_volatility=session_vol,
            fakeout_probability=min(1.0, fakeout_prob),
            trend_persistence=hurst,
            wick_ratio_avg=wick_ratio,
            active_hours=active_hours,
            best_sessions=best_sessions
        )
        
        # Generate Thai Summary
        vol_desc = "ผันผวนสูง (High Vol)" if vol_score > 70 else "ผันผวนต่ำ (Low Vol)" if vol_score < 30 else "ผันผวนปานกลาง"
        trend_desc = "เป็นเทรนด์ชัดเจน (Trendy)" if hurst > 0.55 else "ชอบไวด์เวย์ (Mean Revert)" if hurst < 0.45 else "ไร้ทิศทาง (Random)"
        fake_desc = "ระวัง Fakeout บ่อย" if fakeout_prob > 0.4 else "กราฟค่อนข้างเคารพแนวรับต้าน"
        
        best_sess_str = ", ".join([s for s in best_sessions[:2]])
        
        profile.thai_summary = (
            f"คู่เงิน {symbol} มีนิสัย '{vol_desc}' และ '{trend_desc}'. "
            f"{fake_desc}. "
            f"ช่วงเวลาที่วิ่งดีที่สุดคือ {best_sess_str} (Active Hours: {active_hours[:3]})."
        )
        
        self._profiles[symbol] = profile
        logger.info(f"Analyzed personality for {symbol}", extra=profile.__dict__)
        return profile

    def get_profile(self, symbol: str) -> Optional[PersonalityProfile]:
        return self._profiles.get(symbol)

    def _calculate_atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()

    def _calculate_hurst(self, prices: np.ndarray) -> float:
        """
        Calculate Hurst Exponent to determine trend persistence.
        H < 0.5: Mean Reverting
        H = 0.5: Random Walk (Geometric Brownian Motion)
        H > 0.5: Trending
        """
        if len(prices) < 100:
            return 0.5
            
        # Standard deviation of prices is not Rescaled Range, but for simplified Hurst
        # let's use a simpler heuristic or just return 0.5 if too complex for now
        # Creating a proper R/S analysis requires loop.
        
        try:
           # Simple R/S analysis
           ts = prices
           n = len(ts)
           if n == 0: return 0.5
           
           # Calculate R/S for the whole series (simplified, usually done over multiple windows)
           mean = np.mean(ts)
           deviations = ts - mean
           cum_deviations = np.cumsum(deviations)
           
           R = np.max(cum_deviations) - np.min(cum_deviations)
           S = np.std(ts)
           
           if S == 0: return 0.5
           
           # log(R/S) = H * log(n) + c
           # H ~ log(R/S) / log(n)
           
           hurst = np.log(R/S) / np.log(n) if n > 1 else 0.5
           return float(hurst)

        except Exception:
            return 0.5
