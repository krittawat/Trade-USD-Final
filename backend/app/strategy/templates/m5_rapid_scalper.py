"""
M5 Rapid Scalper Strategy
=========================
Aggressive Scalping for XAUUSD M5.
High Frequency Entries: MA12 + SAR + MACD.
"""

import pandas as pd
import pandas_ta as ta
import numpy as np
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import app.analysis.indicators as ind
from .base_strategy import BaseStrategy
from app.domain.models import Decision
from app.domain.enums import Action
from app.risk.anti_hunt_sl import apply_anti_hunt_sl

# Optional config service (graceful fallback to hardcoded defaults)
_config_service = None
try:
    from app.core.config_service import config_service as _config_service
except ImportError:
    pass

logger = logging.getLogger("M5RapidScalper")

class M5RapidScalperStrategy(BaseStrategy):
    """
    Rapid Scalping Strategy (M5 Edition)
    Optimized for high frequency and fast exits.
    Indicators: MA12, PSAR, MACD, RSI, ATR.
    """
    
    # Core Indicators
    MA_PERIOD = 10         # Fast EMA (Optimized)
    SAR_AF = 0.025         # Sensitive AF
    SAR_MAX = 0.30         # Sensitive Max
    MACD_FAST = 5
    MACD_SLOW = 13
    MACD_SIGNAL = 5
    RSI_PERIOD = 10        # Faster momentum shifts
    ATR_PERIOD = 14
    
    # RSI Bounds
    RSI_UPPER = 90
    RSI_LOWER = 25
    
    # Risk Management (AGGRESSIVE SCALPING)
    SL_ATR_MULT = 1.2      # Ultra Tight SL
    TP_ATR_MULT = 4.0      # Extended TP
    MIN_SL_DISTANCE = 2.0  # Minimum $2.00 SL (Gold)
    
    # Adaptive Risk Settings
    RISK_RAPID = 0.10      # 10% per rapid scalp trade for small Cent Accounts (necessary to allow 0.01 lot sizes to surpass SL bounds)
    
    def __init__(self):
        self.name = "M5_RAPID_SCALPER"
        self.params = {}
        self._load_config()
        
    def _load_config(self):
        def _cfg(key, section, default):
            if _config_service is not None:
                return _config_service.get(key, section, default)
            return default

        s = "STRATEGY_M5_RAPID_SCALPER"
        self.MA_PERIOD = _cfg("MA_PERIOD", s, type(self).MA_PERIOD)
        self.SAR_AF = _cfg("SAR_AF", s, type(self).SAR_AF)
        self.SAR_MAX = _cfg("SAR_MAX", s, type(self).SAR_MAX)
        
        self.RSI_UPPER = _cfg("RSI_UPPER", s, type(self).RSI_UPPER)
        self.RSI_LOWER = _cfg("RSI_LOWER", s, type(self).RSI_LOWER)
        
        self.SL_ATR_MULT = _cfg("SL_ATR_MULT", s, type(self).SL_ATR_MULT)
        self.TP_ATR_MULT = _cfg("TP_ATR_MULT", s, type(self).TP_ATR_MULT)
        self.MIN_SL_DISTANCE = _cfg("MIN_SL_DISTANCE", s, 2.0)
        self.RISK_RAPID = _cfg("RISK_RAPID", s, 0.10)
    
    def update_parameters(self, params: dict):
        self.params.update(params)
        # Explicitly map the optimizer parameters to the class variables
        if 'MA_PERIOD' in params: self.MA_PERIOD = params['MA_PERIOD']
        if 'SAR_AF' in params: self.SAR_AF = params['SAR_AF']
        if 'SAR_MAX' in params: self.SAR_MAX = params['SAR_MAX']
        if 'MACD_FAST' in params: self.MACD_FAST = params['MACD_FAST']
        if 'MACD_SLOW' in params: self.MACD_SLOW = params['MACD_SLOW']
        if 'MACD_SIGNAL' in params: self.MACD_SIGNAL = params['MACD_SIGNAL']
        if 'RSI_PERIOD' in params: self.RSI_PERIOD = params['RSI_PERIOD']
        if 'RSI_UPPER' in params: self.RSI_UPPER = params['RSI_UPPER']
        if 'RSI_LOWER' in params: self.RSI_LOWER = params['RSI_LOWER']
        if 'ATR_PERIOD' in params: self.ATR_PERIOD = params['ATR_PERIOD']
        if 'SL_ATR_MULT' in params: self.SL_ATR_MULT = params['SL_ATR_MULT']
        if 'TP_ATR_MULT' in params: self.TP_ATR_MULT = params['TP_ATR_MULT']
        if 'MIN_SL_DISTANCE' in params: self.MIN_SL_DISTANCE = params['MIN_SL_DISTANCE']
        if 'RISK_RAPID' in params: self.RISK_RAPID = params['RISK_RAPID']
    
    def get_status(self):
        return {
            "name": self.name,
            "style": "Rapid Scalping",
            "indicators": ["MA12", "PSAR", "MACD", "RSI", "ATR"],
            "features": ["High Frequency", "Tight Risk"]
        }
    
    def _ensure_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate indicators using pandas_ta and custom logic"""
        # Ensure we don't modify the original dataframe passed
        # (Though we get it by reference, better to operate safely)
        
        # 1. MA12 (Exponential)
        if 'ma12' not in df.columns:
            df['ma12'] = ind.ema(df['close'], length=self.MA_PERIOD)
            
        # 2. PSAR (using pandas_ta for robust calculation)
        if 'psar' not in df.columns:
            # Dropna just for calculation safely if needed, but usually df is clean
            sar_df = ta.psar(df['high'], df['low'], df['close'], af0=self.SAR_AF, af=self.SAR_AF, max_af=self.SAR_MAX)
            if sar_df is not None and not sar_df.empty:
                # pandas_ta returns PSARl_... and PSARs_...
                psar_l_col = [c for c in sar_df.columns if c.startswith('PSARl_')]
                psar_s_col = [c for c in sar_df.columns if c.startswith('PSARs_')]
                
                if psar_l_col and psar_s_col:
                    df['psar'] = sar_df[psar_l_col[0]].fillna(sar_df[psar_s_col[0]])
                
                # Capture direction: 1 for bullish, 0 for bearish
                dir_col = [c for c in sar_df.columns if c.startswith('PSARr_')]
                if dir_col:
                    df['psar_dir'] = sar_df[dir_col[0]]
                    
        # 3. RSI
        if 'rsi' not in df.columns:
            df['rsi'] = ind.rsi(df['close'], length=self.RSI_PERIOD)
            
        # 4. MACD
        if 'MACD_12_26_9' not in df.columns:
            macd = ind.macd(df['close'], fast=self.MACD_FAST, slow=self.MACD_SLOW, signal=self.MACD_SIGNAL)
            if macd is not None:
                df = pd.concat([df, macd], axis=1)
                
        # 5. ATR
        if 'atr' not in df.columns:
            df['atr'] = ind.atr(df['high'], df['low'], df['close'], length=self.ATR_PERIOD)
            
        # 6. Volume Average (for dead market filter)
        vol_col = 'tick_volume' if 'tick_volume' in df.columns else 'volume'
        if vol_col in df.columns and 'vol_avg_20' not in df.columns:
            df['vol_avg_20'] = ind.sma(df[vol_col], length=20)
            df['vol_avg_20'] = df['vol_avg_20'].fillna(df[vol_col])
        
        return df

    def analyze(self, df: pd.DataFrame, symbol: str = "XAUUSD", **kwargs) -> Decision:
        """
        Analyze logic for M5 Rapid Scalper.
        Focus: Speed and Confluence of MA, SAR, and MACD.
        """
        if df is None or len(df) < 50:
            return Decision(symbol=symbol, action=Action.HOLD, confidence=0.0, reason="Insufficient Base Data", timestamp=datetime.utcnow())

        # --- Dynamic Symbol Overrides (Silver Profile) ---
        if "XAG" in symbol.upper():
            self.MA_PERIOD = kwargs.get('MA_PERIOD', 14)
            self.SAR_AF = kwargs.get('SAR_AF', 0.025)
            self.SAR_MAX = kwargs.get('SAR_MAX', 0.3)
            self.RSI_PERIOD = kwargs.get('RSI_PERIOD', 14)
            self.MACD_FAST = kwargs.get('MACD_FAST', 12)
            self.MACD_SLOW = kwargs.get('MACD_SLOW', 26)
            self.MACD_SIGNAL = kwargs.get('MACD_SIGNAL', 9)
            self.SL_ATR_MULT = kwargs.get('SL_ATR_MULT', 2.0)
            self.TP_ATR_MULT = kwargs.get('TP_ATR_MULT', 3.0)
            self.RSI_UPPER = kwargs.get('RSI_UPPER', 90)
            self.RSI_LOWER = kwargs.get('RSI_LOWER', 25)

        df = self._ensure_indicators(df)
        
        # We need the last completed candle (or current tick if live)
        # Using -1 assumes the framework passes the current state
        r = df.iloc[-1]
        
        close = float(r['close'])
        ma12 = float(r.get('ma12', close))
        psar = float(r.get('psar', close))
        rsi = float(r.get('rsi', 50))
        atr = float(r.get('atr', 3.0))
        
        macd_line = float(r.get(f'MACD_{self.MACD_FAST}_{self.MACD_SLOW}_{self.MACD_SIGNAL}', 0))
        macd_sig = float(r.get(f'MACDs_{self.MACD_FAST}_{self.MACD_SLOW}_{self.MACD_SIGNAL}', 0))
        
        # Volume Filter check (Prevent trading in totally dead zones)
        vol_col = 'tick_volume' if 'tick_volume' in df.columns else 'volume'
        curr_vol = float(r.get(vol_col, 0))
        vol_avg = float(r.get('vol_avg_20', 1))
        
        is_dead_market = (curr_vol < (vol_avg * 0.5)) if vol_avg > 0 else False

        # --- Base Logic ---
        # BUY Logic:
        # 1. Price > MA12
        # 2. PSAR is below price (Bullish)
        # 3. MACD > Signal
        is_buy_trend = (close > ma12)
        psar_bullish = (psar < close)
        macd_bullish = (macd_line > macd_sig)
        
        # SELL Logic:
        # 1. Price < MA12
        # 2. PSAR is above price (Bearish)
        # 3. MACD < Signal
        is_sell_trend = (close < ma12)
        psar_bearish = (psar > close)
        macd_bearish = (macd_line < macd_sig)

        signal = Action.HOLD
        reasons = []
        confidence = 0.0
        
        # --- Extreme Filter (Don't buy the absolute top, don't sell the absolute bottom) ---
        extreme_buy_block = (rsi > self.RSI_UPPER)
        extreme_sell_block = (rsi < self.RSI_LOWER)

        # Execution evaluation
        if is_dead_market:
            return Decision(symbol=symbol, action=Action.HOLD, confidence=0.0, reason=f"[Block] Dead Market Vol: {curr_vol}/{vol_avg:.1f}", timestamp=datetime.utcnow())

        # Check BUY
        if is_buy_trend and psar_bullish and macd_bullish:
            if not extreme_buy_block:
                signal = Action.BUY
                reasons.append("MA12_UP")
                reasons.append("SAR_BULL")
                reasons.append("MACD_UP")
                confidence = 0.75
                if rsi > 50: 
                    confidence += 0.10
                    reasons.append("RSI>50")
            else:
                reasons.append("Overbought Block")
                
        # Check SELL
        elif is_sell_trend and psar_bearish and macd_bearish:
            if not extreme_sell_block:
                signal = Action.SELL
                reasons.append("MA12_DN")
                reasons.append("SAR_BEAR")
                reasons.append("MACD_DN")
                confidence = 0.75
                if rsi < 50:
                    confidence += 0.10
                    reasons.append("RSI<50")
            else:
                reasons.append("Oversold Block")

        if signal == Action.HOLD:
             return Decision(symbol=symbol, action=Action.HOLD, confidence=0.0, reason="No clear rapid signal", timestamp=datetime.utcnow())

        # --- Dynamic Risk Calculation ---
        sl_mult = self.SL_ATR_MULT
        tp_mult = self.TP_ATR_MULT
        
        # Adjusted volatility check (wider ATR -> tighten SL slightly to protect capital)
        atr_avg = df['atr'].rolling(20).mean().iloc[-1] if 'atr' in df.columns else atr
        if atr > (atr_avg * 1.5):
            sl_mult = 2.0  # Give more breathing room in volatile spikes
            tp_mult = 3.0  # Aim higher
            reasons.append("[HighVol_Adjust]")

        _direction_str = "BUY" if signal == Action.BUY else "SELL"
        sl = apply_anti_hunt_sl(
            df=df, close=close, atr=atr,
            direction=_direction_str,
            atr_mult=sl_mult,
            min_sl_distance=self.MIN_SL_DISTANCE,
        )
        
        if signal == Action.BUY:
            tp = close + (atr * tp_mult)
        else:
            tp = close - (atr * tp_mult)

        reason_str = f"[RAPID] {' | '.join(reasons)}"

        return Decision(
            symbol=symbol,
            action=signal,
            confidence=round(confidence, 2),
            reason=reason_str,
            stop_loss=sl,
            take_profit=tp,
            risk_pct=self.RISK_RAPID,
            strategy_name=self.name,
            timestamp=datetime.utcnow()
        )

# Global Instance
m5_rapid_scalper_strategy = M5RapidScalperStrategy()
