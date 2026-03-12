"""
Gold Scalp Pro Strategy (Sniper Mode with Haikin Ashi)
======================================================
High-Frequency Scalping for XAUUSD M5
"The Pro's Choice" for riding institutional trends.

God-Tier Enhancements (v5.0):
1. Premium Chandelier Exit: Dynamic Trailing Stop based on Highest High / Lowest Low.
2. Multi-Tier Profit Locking:
   - Level 1: Break-even at +1 ATR
   - Level 2: Lock 50% profits at +2 ATR
3. Vitality Integration: Uses Vitality Score to boost confidence.
"""
import pandas as pd

import app.analysis.indicators as ind
import logging
from typing import Optional, Dict, Any
from .base_strategy import BaseStrategy, StrategyDecision
import pandas_ta as ta
import MetaTrader5 as mt5
from app.risk.anti_hunt_sl import apply_anti_hunt_sl

# Optional config service (graceful fallback to hardcoded defaults)
_config_service = None
try:
    from app.core.config_service import config_service as _config_service
except ImportError:
    pass

logger = logging.getLogger("GoldScalpPro")

class GoldScalpProStrategy(BaseStrategy):
    """
    Pro-Level Gold Scalping Strategy (Sniper Edition)
    Combines Institutional VWAP, Retail SuperTrend, and Haikin Ashi
    """
    
    # Configuration
    SUPERTREND_LEN = 10
    SUPERTREND_MUL = 3.0
    RSI_PERIOD = 14
    ADX_PERIOD = 14
    ATR_PERIOD = 14
    
    # Risk Management (BALANCED RR MODE)
    SL_ATR_MULT = 2.0      # SL: 2x ATR (was 2.5 — reduce max loss size)
    TP_ATR_MULT = 3.0      # TP: 3x ATR (was 1.5 — FIX inverted RR, now RR=1.5)
    MIN_SL_DISTANCE = 3.0  # Minimum $3.00 SL

    # God-Tier Trailing Settings
    CHANDELIER_MULT = 2.0  # Tighter trailing
    PROFIT_LOCK_1 = 1.0    # Break-Even at +1 ATR (was 0.5 — too early, cut winners)
    PROFIT_LOCK_2 = 2.0    # Lock 50% at +2 ATR (was 1.0 — let winners run more)
    
    # Adaptive Risk Settings
    RISK_SNIPER = 0.02    # 2% for Grade A setups
    RISK_STANDARD = 0.01  # 1% for Grade B setups
    RISK_LOW = 0.005      # 0.5% for weak setups
    
    
    def __init__(self):
        self.name = "GOLD_SCALP_PRO"
        self.params = {}
        self._load_config()
        
    def _load_config(self):
        def _cfg(key, section, default):
            if _config_service is not None:
                return _config_service.get(key, section, default)
            return default

        s = "STRATEGY_GOLD_SCALP_PRO"
        # Configuration
        self.SUPERTREND_LEN = _cfg("SUPERTREND_LEN", s, 10)
        self.SUPERTREND_MUL = _cfg("SUPERTREND_MUL", s, 3.0)
        self.RSI_PERIOD = _cfg("RSI_PERIOD", s, 14)
        self.ADX_PERIOD = _cfg("ADX_PERIOD", s, 14)
        self.ATR_PERIOD = _cfg("ATR_PERIOD", s, 14)
        
        # Risk Management (BALANCED RR MODE)
        self.SL_ATR_MULT = _cfg("SL_ATR_MULT", s, 2.0)
        self.TP_ATR_MULT = _cfg("TP_ATR_MULT", s, 3.0)
        self.MIN_SL_DISTANCE = _cfg("MIN_SL_DISTANCE", s, 3.0)
    
        # God-Tier Trailing Settings
        self.CHANDELIER_MULT = _cfg("CHANDELIER_MULT", s, 2.0)
        self.PROFIT_LOCK_1 = _cfg("PROFIT_LOCK_1", s, 1.0)
        self.PROFIT_LOCK_2 = _cfg("PROFIT_LOCK_2", s, 2.0)
        
        # Adaptive Risk Settings
        self.RISK_SNIPER = _cfg("RISK_SNIPER", s, 0.02)
        self.RISK_STANDARD = _cfg("RISK_STANDARD", s, 0.01)
        self.RISK_LOW = _cfg("RISK_LOW", s, 0.005)
    
    def update_parameters(self, params: dict):
        self.params.update(params)
    
    def get_status(self):
        return {
            "name": self.name,
            "style": "God-Tier Sniper",
            "indicators": ["Haikin Ashi", "VWAP", "SuperTrend", "ADX", "RSI"],
            "features": ["Chandelier Exit", "Multi-Tier Profit Lock", "Vitality Boost"]
        }
    
    def analyze(self, candles: pd.DataFrame, profile=None, regime=None, **kwargs) -> StrategyDecision:
        """
        Analyze market using VWAP + SuperTrend + Haikin Ashi Logic
        """
        if candles is None or len(candles) < 100:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient data")

        symbol = "XAUUSD"
        if hasattr(profile, 'symbol'):
            symbol = profile.symbol
        elif isinstance(profile, str):
            symbol = profile
            
        if regime is not None and 'regime_context' not in kwargs:
            kwargs['regime_context'] = regime

        # --- Unified Brain Check (Phase A) ---
        regime_context = kwargs.get("regime_context")
        if regime_context and hasattr(regime_context, 'actionable') and not regime_context.actionable:
            return StrategyDecision(signal="NO_TRADE", reason=f"Brain Block: {getattr(regime_context, 'reason', '')}")
        
        # Risk Parameters
        self.risk_per_trade = 0.02  # 2% equity risk
        self.max_daily_loss = 0.05
        self.max_drawdown = 0.10
        
        # --- NEW: Volume Filter (Allow Brain Override) ---
        # Default to class attributes if not in kwargs
        self.use_volume_filter = kwargs.get('use_volume_filter', getattr(self, 'use_volume_filter', True))
        self.min_rvol = float(kwargs.get('min_rvol', getattr(self, 'min_rvol', 1.0)))
        
        # Toggles
        self.use_supertrend = True
        self.use_vwap = True
        
        # Ensure Indicators
        df = self._ensure_indicators(candles)
        
        # Get Current Candle
        r = df.iloc[-1]
        
        # Key Values
        close = float(r['close'])
        
        vwap = float(r.get('vwap', close))
        st_dir_raw = r.get(
            f'SUPERT_d_{self.SUPERTREND_LEN}_{self.SUPERTREND_MUL}',
            r.get(f'SUPERTd_{self.SUPERTREND_LEN}_{self.SUPERTREND_MUL}', 0),
        )
        # Handle both string ('up'/'down') and numeric (1/-1) formats
        if isinstance(st_dir_raw, str):
            st_dir = 1.0 if st_dir_raw.lower() == 'up' else -1.0
        else:
            st_dir = float(st_dir_raw) if st_dir_raw is not None else 0.0
        rsi = float(r.get('rsi', 50))
        adx = float(r.get('adx', 0))
        atr = float(r.get('atr', 3.0)) 
        vitality = float(r.get('Vitality', 50))
        
        # Haikin Ashi
        ha_close = float(r.get('HA_close', close))
        ha_open = float(r.get('HA_open', close))
        ha_bullish = ha_close > ha_open
        ha_bearish = ha_close < ha_open
        
        # Validate Indicators
        valid_indicators = not (pd.isna(vwap) or pd.isna(st_dir) or pd.isna(rsi))
        if not valid_indicators:
            return StrategyDecision(signal="NO_TRADE", reason="Loading indicators...")
            
        # ========================================
        # ADAPTIVE MARKET REGIME DETECTION
        # ========================================
        regime = "UNKNOWN"
        atr_avg = df['atr'].rolling(20).mean().iloc[-1] if 'atr' in df.columns else atr
        if pd.isna(atr_avg) or atr_avg <= 0:
            atr_avg = atr
        
        # Override with Unified Brain Context if available
        if regime_context:
            regime = getattr(regime_context.regime, "value", str(regime_context.regime))
            
            # 1. BLOCK HIGH VOLATILITY (Phase F Optimization)
            if regime == "HIGH_VOLATILITY":
                 return StrategyDecision(signal="NO_TRADE", reason="High Volatility Block")

        # ... (rest of logic) ...

        atr_ratio = atr / atr_avg if atr_avg > 0 else 1.0
        
        is_trending = adx > 25
        is_volatile = atr_ratio > 1.2
        
        if is_trending and is_volatile:
            regime = "TRENDING_VOLATILE"
            adaptive_sl_mult = 2.5
            adaptive_tp_mult = 5.0   # was 4.0 — let winners run in strong trends
            adaptive_min_score = 65
        elif is_trending and not is_volatile:
            regime = "TRENDING_STABLE"
            adaptive_sl_mult = 2.0
            adaptive_tp_mult = 4.0   # was 3.5
            adaptive_min_score = 60
        elif not is_trending and is_volatile:
            regime = "CHOPPY"
            adaptive_sl_mult = 3.0   # was 4.0 — reduce max loss in chop
            adaptive_tp_mult = 3.0   # was 1.5 — FIX terrible RR in chop (was <1)
            adaptive_min_score = 90  # was 85 — even stricter to avoid chop traps
        else:
            regime = "FLAT"
            adaptive_sl_mult = 2.0   # was 2.5
            adaptive_tp_mult = 3.0   # was 2.0
            adaptive_min_score = 80  # was 75 — stricter in flat

        # ========================================
        # SESSION-AWARE PARAMETER TUNING (System #3)
        # ========================================
        # ปรับ params ตาม session — ASIA vol ต่ำ ต้อง strict กว่า
        reasons = []
        session = kwargs.get('session', '')
        if session == 'ASIA':
            # ASIA: vol ต่ำ, spread กว้าง → strict filter, SL แคบ
            adaptive_sl_mult *= 0.8
            adaptive_tp_mult *= 0.7
            adaptive_min_score = max(adaptive_min_score, 80)  # ยก min score
            reasons.append("[ASIA:tight]")
        elif session == 'OVERLAP':
            # London-NY Overlap: vol สูงสุด → aggressive
            adaptive_sl_mult *= 1.2
            adaptive_tp_mult *= 1.3
            adaptive_min_score = max(adaptive_min_score - 10, 50)  # ลด threshold
            reasons.append("[OVERLAP:aggressive]")
        elif session in ('LONDON', 'NEW_YORK'):
            # Major sessions: ปกติ แต่ TP กว้างขึ้นเล็กน้อย
            adaptive_tp_mult *= 1.1
            reasons.append(f"[{session}]")

        # ========================================
        # MTF CONFIRMATION — H1 EMA FILTER (System #4)
        # ========================================
        # ถ้า M5 BUY แต่ H1 downtrend → ลด score 30%
        h1_candles = kwargs.get('h1_candles', None)
        h1_trend = "NEUTRAL"  # default: ไม่มีข้อมูล H1 → ไม่ penalty

        if h1_candles is not None and len(h1_candles) >= 200:
            try:
                ema_50 = ind.ema(h1_candles['close'], length=50)
                ema_200 = ind.ema(h1_candles['close'], length=200)
                if ema_50 is not None and ema_200 is not None:
                    e50 = float(ema_50.iloc[-1])
                    e200 = float(ema_200.iloc[-1])
                    if not (pd.isna(e50) or pd.isna(e200)):
                        if e50 > e200:
                            h1_trend = "UP"
                        elif e50 < e200:
                            h1_trend = "DOWN"
            except Exception:
                pass  # H1 filter fail → ไม่ block trading

        # (MTF penalty applied after scoring below)
            
        # Scoring System
        buy_score = 0
        sell_score = 0
        
        # 1. SuperTrend Filter (Main Trend) - 25 points
        if st_dir == 1:
            buy_score += 25
            reasons.append("ST Bull")
        elif st_dir == -1:
            sell_score += 25
            reasons.append("ST Bear")
            
        # 2. VWAP Filter (Institutional Value) - 25 points
        if close > vwap:
            buy_score += 25
            reasons.append("Above VWAP")
        elif close < vwap:
            sell_score += 25
            reasons.append("Below VWAP")
            
        # 3. Haikin Ashi (Momentum/Noise Filter) - 20 points
        if ha_bullish:
            buy_score += 20
        elif ha_bearish:
            sell_score += 20
            
        # 4. Momentum (RSI) - 15 points
        if 50 < rsi < 70:
            buy_score += 15
        elif rsi < 30:
            buy_score += 10 # Oversold bounce
        if 30 < rsi < 50:
            sell_score += 15
        elif rsi > 70:
            sell_score += 10 # Overbought drop
            
        # 5. Trend Strength (ADX) - 15 points
        if adx > 25:
            if buy_score > sell_score:
                buy_score += 15
                reasons.append(f"Strong Trend ({adx:.0f})")
            elif sell_score > buy_score:
                sell_score += 15
                reasons.append(f"Strong Trend ({adx:.0f})")

        # 6. Vitality Boost (God-Tier Addition) - 10 points
        # If market vitality is high, we are more confident in follow-through
        if vitality > 60:
             if buy_score > sell_score: buy_score += 10
             elif sell_score > buy_score: sell_score += 10
             reasons.append(f"High Vitality ({vitality:.0f})")

        # 7. MACD Confirmation
        macd_line = r.get('MACD_12_26_9', 0)
        macd_signal = r.get('MACDs_12_26_9', 0)
        if macd_line > macd_signal:
             buy_score += 10
        elif macd_line < macd_signal:
             sell_score += 10

        # ----------------------------------------
        # 1. Volume Filter (Gatekeeper)
        # ----------------------------------------
        # RVOL = Current Volume / Average Volume
        vol_col = 'tick_volume' if 'tick_volume' in df.columns else 'volume'
        if vol_col not in df.columns:
            return StrategyDecision(signal="NO_TRADE", reason="No volume data", confidence=0.0)

        vol_avg = float(df['vol_avg_20'].iloc[-1]) if 'vol_avg_20' in df.columns else float(df[vol_col].rolling(20).mean().iloc[-1])
        if pd.isna(vol_avg) or vol_avg <= 0:
            vol_avg = float(df[vol_col].tail(min(len(df), 20)).mean())
        if pd.isna(vol_avg) or vol_avg <= 0:
            return StrategyDecision(signal="NO_TRADE", reason="Volume average invalid", confidence=0.0)
        curr_vol = float(r.get(vol_col, 0.0))
        rvol = curr_vol / (vol_avg + 1e-9)
        
        # If Volume is too low, we are in "Dead Market" -> BLOCK ENTRY
        if self.use_volume_filter and rvol < self.min_rvol:
            # We allow holding existing positions, but NO NEW ENTRIES
            # Unless it's a very strong trend continuation (optional, but keep simple for now)
            return StrategyDecision(
                signal="NO_TRADE",
                reason=f"LowVol(RVOL={rvol:.2f}<{self.min_rvol})",
                confidence=0.0
            )

        # ----------------------------------------
        # 2. Smart Money / Volume Spike Boost
        # ----------------------------------------
        # If High Volume + Directional -> Boost Confidence
        open_ = float(r['open']) # Define open_ for use in this block
        if rvol > 2.0:
            if close > open_:
                buy_score += 15
                reasons.append(f"SmartMoney(Vol x{rvol:.1f})")
            else:
                sell_score += 15
                reasons.append(f"SmartMoney(Vol x{rvol:.1f})")

        # ─── MTF Penalty: ลด score counter-trend ตาม H1 EMA ───
        if h1_trend == "DOWN":
            buy_score = int(buy_score * 0.7)
            reasons.append("[H1:↓ penalty]")
        elif h1_trend == "UP":
            sell_score = int(sell_score * 0.7)
            reasons.append("[H1:↑ penalty]")

        # ─── Phase F: Gold Bias Correction (Apply after scoring) ───
        # Gold has natural bullish bias. In "TRENDING_DOWN", we only sell if momentum is VERY strong.
        is_down_regime = (regime == "TRENDING_DOWN")
        strong_momentum = (adx > 30)
        rsi_bearish = (rsi < 50)
        
        if is_down_regime:
             # Gold Bias: Moderate sell penalty (0.5x) — best tested config
             if not (strong_momentum and rsi_bearish):
                 sell_score = int(sell_score * 0.5) 
                 reasons.append("[WeakDown:SellPenalty]")

        # ---------------------------------------------------------
        # 8. CASH COW MODE (Efficiency Boost)
        # ---------------------------------------------------------
        # If in Stable Trend + Low Volatility, use tighter TP for high win rate
        is_cash_cow = (regime == "TRENDING_STABLE") and (adx > 20 and adx < 30)
        
        if is_cash_cow:
             adaptive_tp_mult = 1.5 # Quick Scalp (Cash Cow)
             reasons.append("[CashCow]")
             
        # Decision Logic
        signal = "NO_TRADE"
        sl = 0.0
        tp = 0.0
        
        min_score = adaptive_min_score
        
        direction_mode = kwargs.get('direction', 'AUTO')
        if direction_mode == "BUY_ONLY": sell_score = 0
        if direction_mode == "SELL_ONLY": buy_score = 0
        
        active_score = 0
        
        if buy_score >= min_score and buy_score > sell_score:
            signal = "BUY"
            active_score = buy_score
            sl = apply_anti_hunt_sl(
                df=df, close=close, atr=atr,
                direction="BUY",
                atr_mult=adaptive_sl_mult,
                min_sl_distance=self.MIN_SL_DISTANCE,
            )
            tp = close + (atr * adaptive_tp_mult)
            reasons.append(f"[{regime}][AntiHunt]")
            
        elif sell_score >= min_score and sell_score > buy_score:
            signal = "SELL"
            active_score = sell_score
            sl = apply_anti_hunt_sl(
                df=df, close=close, atr=atr,
                direction="SELL",
                atr_mult=adaptive_sl_mult,
                min_sl_distance=self.MIN_SL_DISTANCE,
            )
            tp = close - (atr * adaptive_tp_mult)
            reasons.append(f"[{regime}][AntiHunt]")
            
        if signal != "NO_TRADE":
            # Adaptive Risk Sizing
            risk_pct = self.RISK_LOW
            if active_score >= 90:
                 risk_pct = self.RISK_SNIPER
                 reasons.append("GOD SNIPER 🎯")
            elif active_score >= 80:
                 risk_pct = self.RISK_STANDARD
            
            # --- 9. RSI PULSE EXIT (Smart Exit) ---
            # Suggest monitoring RSI for early exit
            if signal == "BUY" and rsi > 65:
                 reasons.append("[RSI_High:PulseWatch]")
            elif signal == "SELL" and rsi < 35:
                 reasons.append("[RSI_Low:PulseWatch]")

            conf = active_score / 100.0
            reason_str = f"[PRO] Score:{active_score} | {' '.join(reasons)}"
            
            return StrategyDecision(
                signal=signal,
                entry_price=close,
                sl=sl,
                tp=tp,
                reason=reason_str,
                confidence=conf,
                risk_pct=risk_pct
            )
            
        return StrategyDecision(signal="WAIT", reason=f"Score B{buy_score}/S{sell_score}", confidence=0)

    def check_exit(self, df: pd.DataFrame, position: Any, **kwargs) -> Optional[Dict[str, Any]]:
        """
        [DEPRECATED] Exit Logic is now handled centrally by app.risk.trailing.TrailingManager
        (Phase C: Ratchet Trailing Stop implementation)
        
        This method is kept for reference/backtesting simulation only.
        """
        if df is None or len(df) < 50:
            return None

        # Resolve position details
        ticket = position.ticket
        direction = "BUY" if position.type == mt5.ORDER_TYPE_BUY else "SELL"
        entry_price = position.price_open
        current_sl = position.sl
        current_tp = position.tp
        
        # Get latest market data
        last = df.iloc[-1]
        current_price = last['close']
        atr = last.get('atr', 3.0)
        
        # Don't modify if ATR is missing or unsafe
        if atr <= 0: return None
        
        # Calculate PnL distance in ATR units
        dist_from_entry = (current_price - entry_price) if direction == "BUY" else (entry_price - current_price)
        dist_atr = dist_from_entry / atr
        
        new_sl = current_sl
        action = None
        reason = ""

        # --- 1. CHANDELIER EXIT (Trailing Stop) ---
        # Lookback 20 periods for Highest High / Lowest Low
        lookback = 20
        recent_high = df['high'].tail(lookback).max()
        recent_low = df['low'].tail(lookback).min()
        
        chandelier_dist = atr * self.CHANDELIER_MULT
        
        if direction == "BUY":
            # Chandelier Stop for Buy = Highest High - (ATR * Mult)
            proposed_sl = recent_high - chandelier_dist
            
            # Only trail UP
            if proposed_sl > current_sl:
                # Ensure we don't accidentally cross price (though Chandelier logic prevents this usually)
                if proposed_sl < current_price:
                    new_sl = proposed_sl
                    action = "MODIFY_SL"
                    reason = "Chandelier Trail"
                    
        elif direction == "SELL":
            # Chandelier Stop for Sell = Lowest Low + (ATR * Mult)
            proposed_sl = recent_low + chandelier_dist
            
            # Only trail DOWN
            if (current_sl == 0) or (proposed_sl < current_sl):
                # Ensure we don't cross price
                if proposed_sl > current_price:
                     new_sl = proposed_sl
                     action = "MODIFY_SL"
                     reason = "Chandelier Trail"

        # --- 2. MULTI-TIER PROFIT LOCKING ---
        # Overrides Chandelier if Profit Lock is safer/tighter
        
        # Tier 1: Break-Even Lock (+1 ATR)
        if dist_atr >= self.PROFIT_LOCK_1:
            be_price = entry_price + (atr * 0.1) if direction == "BUY" else entry_price - (atr * 0.1)
            # Ensure we lock at least BE+Buffer
            
            if direction == "BUY":
                if new_sl < be_price:
                    new_sl = be_price
                    action = "MODIFY_SL"
                    reason = "Profit Lock: Break-Even"
            else: # SELL
                if (new_sl == 0) or (new_sl > be_price):
                    new_sl = be_price
                    action = "MODIFY_SL"
                    reason = "Profit Lock: Break-Even"

        # Tier 2: Secure 50% Profit (+2 ATR)
        if dist_atr >= self.PROFIT_LOCK_2:
            lock_price = entry_price + (dist_from_entry * 0.5) if direction == "BUY" else entry_price - (dist_from_entry * 0.5)
            
            if direction == "BUY":
                if new_sl < lock_price:
                    new_sl = lock_price
                    action = "MODIFY_SL"
                    reason = "Profit Lock: 50% Secured"
            else: # SELL
                if (new_sl == 0) or (new_sl > lock_price):
                    new_sl = lock_price
                    action = "MODIFY_SL"
                    reason = "Profit Lock: 50% Secured"
        
        # --- 3. HARD EXIT (Reversal Signal) ---
        # Check for reversal if price crosses SuperTrend
        st_dir_raw = last.get(
            f'SUPERT_d_{self.SUPERTREND_LEN}_{self.SUPERTREND_MUL}',
            last.get(f'SUPERTd_{self.SUPERTREND_LEN}_{self.SUPERTREND_MUL}', 0),
        )
        if isinstance(st_dir_raw, str):
            st_dir = 1.0 if st_dir_raw.lower() == 'up' else -1.0
        else:
            st_dir = float(st_dir_raw) if st_dir_raw is not None else 0.0
        if (direction == "BUY" and st_dir == -1) or (direction == "SELL" and st_dir == 1):
             return {"action": "CLOSE", "reason": "SuperTrend Reversal"}

        # Return Modification Request if changed
        if action == "MODIFY_SL" and abs(new_sl - current_sl) > 0.01:
             return {
                 "action": "MODIFY_SL",
                 "sl": new_sl,
                 "reason": reason
             }
             
        return None

    def _ensure_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate indicators using pandas_ta"""
        # Vitality Score (Custom)
        if 'Vitality' not in df.columns and 'ADX_14' in df.columns:
             # Simple vitality based on ADX + Volume
             df['Vitality'] = (df['ADX_14'] / 100 * 50) + 50 # Base 50 + Trend Bonus
             
        # SuperTrend
        st_dir_col = f'SUPERT_d_{self.SUPERTREND_LEN}_{self.SUPERTREND_MUL}'
        st_dir_alt_col = f'SUPERTd_{self.SUPERTREND_LEN}_{self.SUPERTREND_MUL}'
        if st_dir_col not in df.columns and st_dir_alt_col not in df.columns:
            st = ind.supertrend(df['high'], df['low'], df['close'], length=self.SUPERTREND_LEN, multiplier=self.SUPERTREND_MUL)
            if st is not None:
                df = pd.concat([df, st], axis=1)

        # Volume SMA for RVOL
        if 'vol_avg_20' not in df.columns:
            v_col = 'tick_volume' if 'tick_volume' in df.columns else 'volume'
            if v_col in df.columns:
                df['vol_avg_20'] = ind.sma(df[v_col], length=20)
                df['vol_avg_20'] = df['vol_avg_20'].fillna(df[v_col])
        
        # VWAP
        if 'vwap' not in df.columns:
            # Need datetime index
            vol = df['tick_volume'] if 'tick_volume' in df.columns else df.get('volume', None)
            if vol is not None:
                try:
                    # Manual VWAP (Robust to index type)
                    # VWAP = Cumulative(Typical Price * Volume) / Cumulative(Volume)
                    tp = (df['high'] + df['low'] + df['close']) / 3
                    cum_vol = vol.cumsum()
                    cum_pv = (tp * vol).cumsum()
                    df['vwap'] = cum_pv / cum_vol
                    df['vwap'] = df['vwap'].fillna(tp) # Fallback to TP if vol is 0
                except Exception as e:
                     logger.warning(f"VWAP Calc failed: {e}")
                     df['vwap'] = (df['high'] + df['low'] + df['close']) / 3
            else:
                 df['vwap'] = (df['high'] + df['low'] + df['close']) / 3
        
        # HAIKIN ASHI
        if 'HA_close' not in df.columns:
            ha = ind.heiken_ashi(df['open'], df['high'], df['low'], df['close'])
            if ha is not None:
                df['HA_open'] = ha['HA_Open']
                df['HA_high'] = ha['HA_High']
                df['HA_low'] = ha['HA_Low']
                df['HA_close'] = ha['HA_Close']

        # RSI
        if 'rsi' not in df.columns:
            df['rsi'] = ind.rsi(df['close'], length=self.RSI_PERIOD)
            
        # ADX
        if 'adx' not in df.columns:
            adx = ind.adx(df['high'], df['low'], df['close'], length=self.ADX_PERIOD)
            if adx is not None:
                endpoint = f"ADX_{self.ADX_PERIOD}"
                if endpoint in adx.columns:
                    df['adx'] = adx[endpoint]
        
        # ATR
        if 'atr' not in df.columns:
            df['atr'] = ind.atr(df['high'], df['low'], df['close'], length=self.ATR_PERIOD)

        # MACD (12, 26, 9)
        if 'MACD_12_26_9' not in df.columns:
            macd = ind.macd(df['close'], fast=12, slow=26, signal=9)
            if macd is not None:
                df = pd.concat([df, macd], axis=1)
            
        return df

# Global Instance
gold_scalp_pro_strategy = GoldScalpProStrategy()
