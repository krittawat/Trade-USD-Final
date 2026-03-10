"""
Gold Scalp Pro Strategy (High Win Rate Edition)
======================================================
Optimized for High Probability Setups (Win Rate > 70%)
Focus: Trend Following + Pullbacks (No Breakout Chasing)

Strategy Logic:
1. Trend: Price > EMA 200 & SuperTrend Bullish
2. Entry: Pullback to VWAP or EMA Zone + StochRSI Oversold (<20)
3. Exit: 
   - TP: Quick Scalp (1.2 ATR)
   - SL: Wide Safety Net (3.0 ATR)
   - BE: Aggressive (0.5 ATR) to protect capital
"""
import pandas as pd
try:
    import app.analysis.indicators as ind
except ImportError:
    from app.utils.safe_ta import SafeTA as ta
import numpy as np
import logging
from typing import Optional, Dict, Any
from .base_strategy import BaseStrategy, StrategyDecision
import MetaTrader5 as mt5
from app.core.config_service import config_service

logger = logging.getLogger("GoldScalpPro")

class GoldScalpProStrategy(BaseStrategy):
    """
    Pro-Level Gold Scalping Strategy (High Win Rate Edition)
    เน้น Win Rate สูง: รอพักตัว (Pullback) แล้วเข้าตามเทรนด์ใหญ่
    """
    
    # การตั้งค่า (Configuration)
    SUPERTREND_LEN = 10
    SUPERTREND_MUL = 3.0
    RSI_PERIOD = 14
    ADX_PERIOD = 14
    ATR_PERIOD = 14
    
    # การบริหารความเสี่ยง (1:1 Risk Reward Mode)
    SL_ATR_MULT = 1.5      # SL เท่ากับ TP (1:1)
    TP_ATR_MULT = 1.5      # TP เท่าเดิม (High Win Rate)
    MIN_SL_DISTANCE = 2.0  # SL ขั้นต่ำ $2.00 (ลดลงเพื่อให้ SL แคบลงได้จริง)
    
    # การตั้งค่า Trailing (ปล่อยกำไรวิ่ง - Let Profits Run)
    CHANDELIER_MULT = 3.0  # Trailing ห่างขึ้นบาน (แทบไม่ใช้จนกว่าจะกำไรเยอะ)
    PROFIT_LOCK_1 = 1.0    # ล็อกทุนเมื่อบวก 1.0 ATR (มั่นใจแล้วค่อยกันทุน)
    PROFIT_LOCK_2 = 2.0    # ล็อกกำไรไม้ใหญ่ (Bonus Win)
    
    # การปรับความเสี่ยง (HEAVY MODE: MAX AGGRESSION)
    RISK_SNIPER = 0.05    # 5% สำหรับเกรด A+ (Sniper Setup)
    RISK_STANDARD = 0.03  # 3% สำหรับเกรด A (Standard)
    RISK_LOW = 0.01       # 1% สำหรับเกรด B (Low Risk)
    
    def __init__(self):
        self.name = "GOLD_SCALP_PRO"
        self.params = {}
        self._load_config()
        
    def _load_config(self):
        # โหลดค่าจาก Config Service (ถ้ามี) หรือใช้ค่า Default
        self.SUPERTREND_LEN = config_service.get("SUPERTREND_LEN", "STRATEGY_GOLD_SCALP_PRO", 10)
        self.SUPERTREND_MUL = config_service.get("SUPERTREND_MUL", "STRATEGY_GOLD_SCALP_PRO", 3.0)
        
        # Risk Management (High Win Rate Config)
        self.SL_ATR_MULT = config_service.get("SL_ATR_MULT", "STRATEGY_GOLD_SCALP_PRO", 3.0)
        self.TP_ATR_MULT = config_service.get("TP_ATR_MULT", "STRATEGY_GOLD_SCALP_PRO", 1.2)
        
        # Stop Hunt Protection
        self.STOP_HUNT_BUFFER = config_service.get("STOP_HUNT_BUFFER", "STRATEGY_GOLD_SCALP_PRO", 0.5) # ATR buffer below structure
        self.STRUCTURE_LOOKBACK = config_service.get("STRUCTURE_LOOKBACK", "STRATEGY_GOLD_SCALP_PRO", 20)
        
        # Adaptive Risk
        self.RISK_SNIPER = config_service.get("RISK_SNIPER", "STRATEGY_GOLD_SCALP_PRO", 0.02)
    
    def update_parameters(self, params: dict):
        self.params.update(params)
    
    def get_status(self):
        return {
            "name": self.name,
            "style": "High Win-Rate Sniper (Pullback)",
            "indicators": ["EMA200", "SuperTrend", "StochRSI", "VWAP"],
            "features": ["Pullback Entry", "Wide SL / Tight TP", "Aggressive BE", "Stop Hunt Protection"]
        }
    
    def analyze(self, df: pd.DataFrame, symbol: str = "XAUUSD", **kwargs) -> StrategyDecision:
        """
        วิเคราะห์ตลาดโดยใช้ตรรกะ Pullback + Trend Following
        """
        if df is None or len(df) < 100:
            return StrategyDecision(signal="NO_TRADE", reason="ข้อมูลไม่เพียงพอ (Insufficient data)")
        
        # คำนวณอินดิเคเตอร์
        df = self._ensure_indicators(df)
        
        # ดึงแท่งล่าสุด
        r = df.iloc[-1]
        
        # ค่าสำคัญ (Key Values)
        close = float(r['close'])
        high = float(r['high'])
        low = float(r['low'])
        
        ema200 = float(r.get('EMA_200', close)) # Trend Filter หลัก
        vwap = float(r.get('vwap', close))
        st_dir = float(r.get(f'SUPERT_d_{self.SUPERTREND_LEN}_{self.SUPERTREND_MUL}', 0)) 
        
        # StochRSI (ใช้จับจุดกลับตัวในย่อ)
        stoch_k = float(r.get('STOCHRSIk_14_14_3_3', 50))
        stoch_d = float(r.get('STOCHRSId_14_14_3_3', 50))
        
        atr = float(r.get('atr', 3.0)) 
        vitality = float(r.get('Vitality', 50))
        
        # ตรวจสอบความถูกต้องของอินดิเคเตอร์
        valid_indicators = not (pd.isna(vwap) or pd.isna(st_dir) or pd.isna(stoch_k))
        if not valid_indicators:
            return StrategyDecision(signal="NO_TRADE", reason="กำลังโหลดอินดิเคเตอร์... (Loading indicators)")
        
        # ========================================
        # ระบบให้คะแนน (SCORING SYSTEM) - High Win Rate
        # ========================================
        buy_score = 0
        sell_score = 0
        reasons = []
        
        # 1. Trend Filter (EMA 200) - MANDATORY (30 คะแนน)
        # ต้องเทรดตามเทรนด์ใหญ่เท่านั้น ห้ามสวน
        trend_bullish = close > ema200
        trend_bearish = close < ema200
        
        if trend_bullish:
            buy_score += 30
            reasons.append("Major Trend UP (EMA200)")
        elif trend_bearish:
            sell_score += 30
            reasons.append("Major Trend DOWN (EMA200)")
            
        # 2. SuperTrend Filter (Trend ย่อย) - (20 คะแนน)
        # ช่วยยืนยันว่าเทรนด์ยังไม่จบ
        if st_dir == 1 and trend_bullish:
            buy_score += 20
            reasons.append("SuperTrend BULL")
        elif st_dir == -1 and trend_bearish:
            sell_score += 20
            reasons.append("SuperTrend BEAR")
            
        # 3. Pullback Signal (StochRSI) - (30 คะแนน) *หัวใจสำคัญ*
        # BUY เมื่อ StochRSI Oversold (<20) แล้วเริ่มงัดขึ้น (k > d) ในขาขึ้น
        # SELL เมื่อ StochRSI Overbought (>80) แล้วเริ่มหักลง (k < d) ในขาลง
        
        stoch_oversold = 20
        stoch_overbought = 80
        
        if trend_bullish:
            if stoch_k < stoch_oversold: 
                buy_score += 15 # Oversold Zone
                if stoch_k > stoch_d: # Golden Cross in Oversold
                    buy_score += 25  # สัญญาณแรง!
                    reasons.append(f"StochRSI Oversold Cross ({stoch_k:.1f})")
                else:
                    reasons.append("Wait StochRSI Cross")
        
        if trend_bearish:
            if stoch_k > stoch_overbought:
                sell_score += 15 # Overbought Zone
                if stoch_k < stoch_d: # Death Cross in Overbought
                    sell_score += 25 # สัญญาณแรง!
                    reasons.append(f"StochRSI Overbought Cross ({stoch_k:.1f})")
                else:
                    reasons.append("Wait StochRSI Cross")

        # 4. Value Area (VWAP) - (20 คะแนน)
        # ซื้อเมื่อราคาอยู่ใกล้ VWAP หรือต่ำกว่า VWAP (ของดีราคาถูก) ในเทรนด์ขาขึ้น
        if trend_bullish:
            # ยิ่งใกล้ VWAP หรือต่ำกว่า ยิ่งดี (Discount)
            dist_to_vwap_pct = (close - vwap) / vwap * 100
            if dist_to_vwap_pct < 0.2: # อยู่โซน Discount หรือใกล้เส้น
                buy_score += 20
                reasons.append("VWAP Discount Zone")
        elif trend_bearish:
            # ยิ่งใกล้ VWAP หรือสูงกว่า ยิ่งดี (Premium)
            dist_to_vwap_pct = (close - vwap) / vwap * 100
            if dist_to_vwap_pct > -0.2: 
                sell_score += 20
                reasons.append("VWAP Premium Zone")

        # 5. Vitality Confirmation (Optional Boost)
        if vitality > 40:
             if buy_score > 50: buy_score += 5
             if sell_score > 50: sell_score += 5

        # ========================================
        # DECISION LOGIC
        # ========================================
        signal = "NO_TRADE"
        sl = 0.0
        tp = 0.0
        min_score = 75 # ต้องการความมั่นใจสูง (Trend + SuperTrend + Stoch/VWAP)
        
        # ========================================
        # 6. VOLUME FILTER (New in V2)
        # ========================================
        # ต้องมี Volume สนับสนุน (กันหลอกช่วงตลาดวาย)
        volume = float(r.get('tick_volume', 0))
        vol_ma = float(r.get('VOL_MA_20', volume * 0.8)) # Fallback if no MA
        
        if volume < vol_ma * 0.8:
            return StrategyDecision(signal="NO_TRADE", reason="Volume Too Low (Wait for Activity)")
            
        # ========================================
        # 7. SESSION FILTER (New in V2)
        # ========================================
        # เน้นเทรดช่วง London/NY (14:00 - 03:00 ICT)
        # Server Time (MT5) usually UTC+2 or UTC+3. Let's use local logic if possible or hour check.
        # Assuming Data has 'time' as unix timestamp.
        import datetime
        dt = datetime.datetime.fromtimestamp(r['time'])
        # Convert to roughly ICT (UTC+7) or just use Strategy Hour if known.
        # Let's assume input df has localized time or we check hour directly.
        # For simplicity in this version, we boost score in active hours.
        
        # Active Hours: 07:00 - 20:00 UTC (London to NY Close)
        # UTC Hour:
        utc_hour = dt.utcfromtimestamp(r['time']).hour
        
        is_london = 7 <= utc_hour < 16
        is_ny = 12 <= utc_hour < 21
        is_dead_zone = 21 <= utc_hour or utc_hour < 6

        if is_dead_zone:
             # Reduce score or Block
             buy_score -= 30
             sell_score -= 30
             reasons.append("Dead Zone (Low Liq)")
        elif is_london or is_ny:
             # Boost
             buy_score += 10
             sell_score += 10
             reasons.append("Active Session 🟢")
        
        # Direction Filter Override
        direction_mode = kwargs.get('direction', 'AUTO')
        if direction_mode == "BUY_ONLY": sell_score = 0
        if direction_mode == "SELL_ONLY": buy_score = 0
        
        active_score = 0
        
        if buy_score >= min_score and buy_score > sell_score:
            signal = "BUY"
            active_score = buy_score
            
            # SL/TP Calculation (High Win Rate Model)
            # 1. Standard ATR SL
            standard_sl_dist = max(atr * self.SL_ATR_MULT, self.MIN_SL_DISTANCE)
            standard_sl = close - standard_sl_dist
            
            # 2. Structural SL (Stop Hunt Protection)
            # Find lowest low in lookback period
            lookback = self.STRUCTURE_LOOKBACK
            swing_low = df['low'].tail(lookback).min()
            structural_sl_dist = (close - swing_low) + (atr * self.STOP_HUNT_BUFFER)
            structural_sl = swing_low - (atr * self.STOP_HUNT_BUFFER)
            
            # Use the WIDER SL for safety (Lower price for BUY)
            final_sl = min(standard_sl, structural_sl)
            
            # Re-calculate distance based on final SL
            final_sl_dist = close - final_sl
            
            # TP Target (Fixed Reward Ratio or ATR)
            tp_dist = atr * self.TP_ATR_MULT
            
            sl = final_sl
            tp = close + tp_dist
            
            if final_sl < structural_sl + 0.001:
                 reasons.append(f"🛡️ Stop Hunt Protected (Below Low {swing_low:.2f})")
            
        elif sell_score >= min_score and sell_score > buy_score:
            signal = "SELL"
            active_score = sell_score
            
            # 1. Standard ATR SL
            standard_sl_dist = max(atr * self.SL_ATR_MULT, self.MIN_SL_DISTANCE)
            standard_sl = close + standard_sl_dist
            
            # 2. Structural SL (Stop Hunt Protection)
            # Find highest high in lookback period
            lookback = self.STRUCTURE_LOOKBACK
            swing_high = df['high'].tail(lookback).max()
            structural_sl_dist = (swing_high - close) + (atr * self.STOP_HUNT_BUFFER)
            structural_sl = swing_high + (atr * self.STOP_HUNT_BUFFER)
            
            # Use the WIDER SL for safety (Higher price for SELL)
            final_sl = max(standard_sl, structural_sl)
            
            # Re-calculate distance based on final SL
            final_sl_dist = final_sl - close
            
            tp_dist = atr * self.TP_ATR_MULT
            
            sl = final_sl
            tp = close - tp_dist
            
            if final_sl > structural_sl - 0.001:
                 reasons.append(f"🛡️ Stop Hunt Protected (Above High {swing_high:.2f})")
            
        if signal != "NO_TRADE":
            # Risk Sizing
            risk_pct = self.RISK_STANDARD
            if active_score >= 90: # A+ Setup
                 risk_pct = self.RISK_SNIPER
                 reasons.append("Sniper Setup 🎯")
            
            conf = min(1.0, active_score / 100.0)
            reason_str = f"[WINRATE_MODE] Score:{active_score} | {' '.join(reasons)}"
            
            return StrategyDecision(
                signal=signal,
                entry_price=close,
                sl=sl,
                tp=tp,
                reason=reason_str,
                confidence=conf,
                risk_pct=risk_pct
            )
            
        return StrategyDecision(signal="WAIT", reason=f"Score B{buy_score}/S{sell_score} (Need {min_score}) | {' '.join(reasons[-2:])}", confidence=0)

    def check_exit(self, df: pd.DataFrame, position: Any, **kwargs) -> Optional[Dict[str, Any]]:
        """
        High Win Rate Exit Logic:
        - เน้นล็อกทุนเร็ว (Aggressive BE)
        - ไม่ Trailing บีบเกินไป (ปล่อยให้ราคาแกว่งได้ตราบใดที่ไม่ชน SL/BE)
        """
        if df is None or len(df) < 50: return None

        # Position Info
        direction = "BUY" if position.type == mt5.ORDER_TYPE_BUY else "SELL"
        entry_price = position.price_open
        current_sl = position.sl
        
        # Market Data
        last = df.iloc[-1]
        current_price = last['close']
        atr = last.get('atr', 3.0)
        
        dist_from_entry = (current_price - entry_price) if direction == "BUY" else (entry_price - current_price)
        dist_atr = dist_from_entry / atr
        
        new_sl = current_sl
        action = None
        reason = ""

        # 1. Aggressive Break-Even (ตีตั๋วฟรีเร็วๆ)
        # ถ้ากำไรระยะสั้น > 0.5 ATR ให้เลื่อน SL มากันทุนเลย
        if dist_atr >= self.PROFIT_LOCK_1:
            be_price = entry_price + (atr * 0.1) if direction == "BUY" else entry_price - (atr * 0.1)
            
            if direction == "BUY":
                if new_sl < be_price:
                    new_sl = be_price
                    action = "MODIFY_SL"
                    reason = "Fast Break-Even (Ticket Free)"
            else:
                if (new_sl == 0) or (new_sl > be_price):
                    new_sl = be_price
                    action = "MODIFY_SL"
                    reason = "Fast Break-Even (Ticket Free)"

        # 2. Trailing Stop (Chandelier) - ทำงานเมื่อกำไรเยอะแล้วเท่านั้น (> 1.5 ATR)
        # เพื่อไม่ให้ขายหมูในเทรนด์ยาว แต่ก็ไม่โดนสะบัดออกง่ายๆ
        if dist_atr >= self.PROFIT_LOCK_2:
             # Lookback for Chandelier
             lookback = 20
             recent_high = df['high'].tail(lookback).max()
             recent_low = df['low'].tail(lookback).min()
             chandelier_dist = atr * self.CHANDELIER_MULT
             
             if direction == "BUY":
                 proposed_sl = recent_high - chandelier_dist
                 if proposed_sl > current_sl and proposed_sl < current_price:
                     new_sl = proposed_sl
                     action = "MODIFY_SL"
                     reason = "Chandelier Trail"
             elif direction == "SELL":
                 proposed_sl = recent_low + chandelier_dist
                 if (current_sl == 0 or proposed_sl < current_sl) and proposed_sl > current_price:
                     new_sl = proposed_sl
                     action = "MODIFY_SL"
                     reason = "Chandelier Trail"

        if action == "MODIFY_SL" and abs(new_sl - current_sl) > 0.01:
             return {"action": "MODIFY_SL", "sl": new_sl, "reason": reason}

        return None

    def _ensure_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """คำนวณอินดิเคเตอร์ที่จำเป็น"""
        # EMA 200
        if 'EMA_200' not in df.columns:
            df['EMA_200'] = df['close'].ewm(span=200).mean()

        # Vitality
        if 'Vitality' not in df.columns and 'ADX_14' in df.columns:
             df['Vitality'] = (df['ADX_14'] / 100 * 50) + 50
             
        # SuperTrend (Standard)
        target_col = f'SUPERT_{self.SUPERTREND_LEN}_{self.SUPERTREND_MUL}.0'
        if target_col not in df.columns:
             # พยายามหา column ที่ใกล้เคียง
             cols = [c for c in df.columns if c.startswith(f'SUPERT_{self.SUPERTREND_LEN}_{self.SUPERTREND_MUL}')]
             if cols:
                 df[target_col] = df[cols[0]]
             else:
                 st = ta.supertrend(df['high'], df['low'], df['close'], length=self.SUPERTREND_LEN, multiplier=self.SUPERTREND_MUL)
                 if st is not None: df = pd.concat([df, st], axis=1)
        
        # VWAP
        if 'vwap' not in df.columns:
             df['vwap'] = (df['high'] + df['low'] + df['close']) / 3 # Approximation if native fail
        
        # StochRSI (New Core Indicator)
        # Using check for stochrsi columns. pandas_ta usually produces STOCHRSIk_... and STOCHRSId_...
        if not any(c.startswith('STOCHRSIk') for c in df.columns):
            stochrsi = ta.stochrsi(df['close'], length=14, rsi_length=14, k=3, d=3)
            if stochrsi is not None:
                df = pd.concat([df, stochrsi], axis=1)

        # ATR
        if 'atr' not in df.columns:
            df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)

        # Volume MA
        if 'VOL_MA_20' not in df.columns and 'tick_volume' in df.columns:
            df['VOL_MA_20'] = df['tick_volume'].rolling(window=20).mean()
            
        return df

gold_scalp_pro_strategy = GoldScalpProStrategy()
