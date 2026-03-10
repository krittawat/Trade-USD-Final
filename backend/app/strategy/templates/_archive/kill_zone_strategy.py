"""
Kill Zone Strategy
เทรดเฉพาะช่วงเวลาที่สถาบันเคลื่อนไหว (High Volume)
- London Kill Zone: 08:00-10:00 UTC (15:00-17:00 TH)
- NY Kill Zone: 13:00-15:00 UTC (20:00-22:00 TH)
- Power Hour: 19:00-20:00 UTC (02:00-03:00 TH next day)
Win Rate: ~70-80% when combined with other confluences
"""
import pandas as pd
from typing import Dict, Optional
from datetime import datetime, time
from app.strategy.templates.base_strategy import SignalType, StrategyDecision

class KillZoneStrategy:
    def __init__(self):
        self.name = "KILL_ZONE"
        
        # Define Kill Zones (UTC)
        self.kill_zones = {
            "LONDON_OPEN": {"start": time(8, 0), "end": time(10, 0), "weight": 2.0},
            "NY_OPEN": {"start": time(13, 0), "end": time(15, 0), "weight": 2.0},
            "LONDON_NY_OVERLAP": {"start": time(13, 0), "end": time(17, 0), "weight": 2.5},  # Best time
            "POWER_HOUR": {"start": time(19, 0), "end": time(20, 0), "weight": 1.5},
        }
        
        # Dead Zones (avoid trading)
        self.dead_zones = {
            "ASIAN_LOW": {"start": time(2, 0), "end": time(6, 0)},  # Low volume
            "LUNCH_LONDON": {"start": time(11, 0), "end": time(12, 0)},
        }
    
    def get_current_zone(self, current_time: datetime = None) -> Dict:
        """ตรวจสอบ Zone ปัจจุบัน"""
        if current_time is None:
            current_time = datetime.utcnow()
        
        current_t = current_time.time() if hasattr(current_time, 'time') else time(14, 0)
        
        # Check if in Kill Zone
        for zone_name, zone_info in self.kill_zones.items():
            if zone_info["start"] <= current_t < zone_info["end"]:
                return {
                    "name": zone_name,
                    "type": "KILL",
                    "weight": zone_info["weight"],
                    "active": True
                }
        
        # Check if in Dead Zone
        for zone_name, zone_info in self.dead_zones.items():
            if zone_info["start"] <= current_t < zone_info["end"]:
                return {
                    "name": zone_name,
                    "type": "DEAD",
                    "weight": 0.0,
                    "active": False
                }
        
        return {
            "name": "NEUTRAL",
            "type": "NEUTRAL",
            "weight": 0.5,
            "active": True
        }
    
    def detect_liquidity_grab(self, df: pd.DataFrame) -> Dict:
        """
        ตรวจจับ Liquidity Grab (Stop Hunt) Pattern
        - Wick ยาวกว่า 60% ของเทียน
        - Price กลับมาปิดในโซนเดิม
        """
        if len(df) < 3:
            return {"detected": False}
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        body = abs(last['close'] - last['open'])
        total_range = last['high'] - last['low']
        upper_wick = last['high'] - max(last['close'], last['open'])
        lower_wick = min(last['close'], last['open']) - last['low']
        
        if total_range == 0:
            return {"detected": False}
        
        wick_ratio = max(upper_wick, lower_wick) / total_range
        
        # Bullish Liquidity Grab (Long lower wick, closed up)
        if lower_wick > upper_wick and wick_ratio > 0.6 and last['close'] > last['open']:
            return {
                "detected": True,
                "type": "BULLISH_GRAB",
                "signal": SignalType.BUY,
                "confidence": min(85, 60 + wick_ratio * 30)
            }
        
        # Bearish Liquidity Grab (Long upper wick, closed down)
        if upper_wick > lower_wick and wick_ratio > 0.6 and last['close'] < last['open']:
            return {
                "detected": True,
                "type": "BEARISH_GRAB",
                "signal": SignalType.SELL,
                "confidence": min(85, 60 + wick_ratio * 30)
            }
        
        return {"detected": False}
    
    def detect_fair_value_gap(self, df: pd.DataFrame) -> Dict:
        """
        ตรวจจับ Fair Value Gap (FVG)
        - Gap ระหว่าง High ของเทียน 1 กับ Low ของเทียน 3
        """
        if len(df) < 5:
            return {"detected": False}
        
        c1 = df.iloc[-3]  # Candle before gap
        c2 = df.iloc[-2]  # Gap candle (momentum)
        c3 = df.iloc[-1]  # Current candle
        
        # Bullish FVG: c1.high < c3.low (gap up)
        if c1['high'] < c3['low'] and c2['close'] > c2['open']:
            gap_size = c3['low'] - c1['high']
            atr = df['ta_atr_14'].iloc[-1] if 'ta_atr_14' in df.columns else gap_size * 2
            if gap_size > atr * 0.2:  # Significant gap
                return {
                    "detected": True,
                    "type": "BULLISH_FVG",
                    "signal": SignalType.BUY,
                    "fvg_low": c1['high'],
                    "fvg_high": c3['low'],
                    "confidence": 70
                }
        
        # Bearish FVG: c1.low > c3.high (gap down)
        if c1['low'] > c3['high'] and c2['close'] < c2['open']:
            gap_size = c1['low'] - c3['high']
            atr = df['ta_atr_14'].iloc[-1] if 'ta_atr_14' in df.columns else gap_size * 2
            if gap_size > atr * 0.2:
                return {
                    "detected": True,
                    "type": "BEARISH_FVG",
                    "signal": SignalType.SELL,
                    "fvg_low": c3['high'],
                    "fvg_high": c1['low'],
                    "confidence": 70
                }
        
        return {"detected": False}
    
    def analyze(self, df: pd.DataFrame, direction_mode: str = "AUTO", symbol: str = "XAUUSD") -> StrategyDecision:
        """
        วิเคราะห์สัญญาณ Kill Zone
        """
        if len(df) < 10:
            return StrategyDecision(
                signal=SignalType.WAIT,
                confidence=0,
                reason="Not enough data",
                sl=None, tp=None
            )
        
        current_price = df['close'].iloc[-1]
        atr = df['ta_atr_14'].iloc[-1] if 'ta_atr_14' in df.columns else (df['high'] - df['low']).mean()
        
        # 1. Check Kill Zone
        zone = self.get_current_zone()
        
        if zone["type"] == "DEAD":
            return StrategyDecision(
                signal=SignalType.WAIT,
                confidence=0,
                reason=f"💤 Dead Zone: {zone['name']} - No Trading",
                sl=None, tp=None
            )
        
        # 2. Detect Patterns
        liquidity = self.detect_liquidity_grab(df)
        fvg = self.detect_fair_value_gap(df)
        
        # 3. Decision Logic
        signal = SignalType.WAIT
        confidence = 0
        reason = ""
        sl = None
        tp = None
        risk_mult = 1.0
        
        # Liquidity Grab in Kill Zone = HIGH CONFIDENCE
        if liquidity["detected"] and zone["type"] == "KILL":
            signal = liquidity["signal"]
            confidence = liquidity["confidence"] * zone["weight"] / 2
            confidence = min(95, confidence)
            
            if signal == SignalType.BUY:
                sl = current_price - (atr * 1.5)
                tp = current_price + (atr * 2.0)
                reason = f"🎯 KILL ZONE BUY | {liquidity['type']} in {zone['name']}"
            else:
                sl = current_price + (atr * 1.5)
                tp = current_price - (atr * 2.0)
                reason = f"🎯 KILL ZONE SELL | {liquidity['type']} in {zone['name']}"
            
            risk_mult = zone["weight"]
        
        # FVG in Kill Zone
        elif fvg["detected"] and zone["type"] == "KILL":
            signal = fvg["signal"]
            confidence = fvg["confidence"] * zone["weight"] / 2
            confidence = min(90, confidence)
            
            if signal == SignalType.BUY:
                sl = fvg["fvg_low"] - (atr * 0.5)
                tp = current_price + (atr * 2.0)
                reason = f"📊 FVG BUY | {fvg['type']} in {zone['name']}"
            else:
                sl = fvg["fvg_high"] + (atr * 0.5)
                tp = current_price - (atr * 2.0)
                reason = f"📊 FVG SELL | {fvg['type']} in {zone['name']}"
            
            risk_mult = zone["weight"] * 0.8
        
        # Neutral Zone - Patterns still valid but lower weight
        elif liquidity["detected"] or fvg["detected"]:
            pattern = liquidity if liquidity["detected"] else fvg
            signal = pattern["signal"]
            confidence = pattern["confidence"] * 0.6  # Reduced
            
            if signal == SignalType.BUY:
                sl = current_price - (atr * 1.5)
                tp = current_price + (atr * 1.5)
                reason = f"⚡ Pattern BUY (Neutral Zone)"
            else:
                sl = current_price + (atr * 1.5)
                tp = current_price - (atr * 1.5)
                reason = f"⚡ Pattern SELL (Neutral Zone)"
            
            risk_mult = 0.7
        
        else:
            reason = f"🔍 Scanning {zone['name']}..." if zone["type"] == "KILL" else "📊 Waiting for Pattern"
        
        # Direction Filter
        if signal == SignalType.BUY and direction_mode == "SELL":
            signal = SignalType.WAIT
            reason = "Direction filtered (SELL only)"
        elif signal == SignalType.SELL and direction_mode == "BUY":
            signal = SignalType.WAIT
            reason = "Direction filtered (BUY only)"
        
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
kill_zone_strategy = KillZoneStrategy()
