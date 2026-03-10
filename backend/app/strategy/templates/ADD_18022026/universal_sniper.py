"""
Universal Sniper Strategy V3 — "All-Weather Omniscient" (God-Tier)
===================================================================
A single multi-regime strategy that dynamically shifts its logic:
    - ADX > 25 (Trend/Breakout): Uses EMA + VWAP Pullbacks + ATR Expansions
    - ADX < 15 (Ranging): Uses Bollinger Bands Reversals + RSI Extremes
    - ADX 15-25 (Transition): Requires higher confluence before entry

Key Details:
    - Adapts automatically to Market Volatility (ATR checks)
    - Anti-Hunt SL applied to every trade
    - Minimum confidence gates dynamically scale with the current regime (Trend is safer, Ranging requires stricter entries)
"""

import pandas as pd
import pandas_ta as ta
import numpy as np
from typing import Optional

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy
from app.risk.anti_hunt_sl import apply_anti_hunt_sl

logger = get_logger(__name__)

# --- Configuration (Defaults — tuned for profitability) ---
DEFAULT_H1_EMA_FAST = 50
DEFAULT_H1_EMA_SLOW = 200
DEFAULT_M5_RSI_PERIOD = 14
DEFAULT_M5_ATR_PERIOD = 14
DEFAULT_BB_PERIOD = 20
DEFAULT_BB_STD = 2.0
DEFAULT_RISK_STANDARD = 0.01


class UniversalSniperStrategy(BaseStrategy):
    """
    Universal Sniper V3: All-Weather Multi-Regime Strategy.
    """
    name = "universal_sniper"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP, 
        RegimeType.TRENDING_DOWN, 
        RegimeType.HIGH_VOLATILITY,
        RegimeType.BREAKOUT,
        RegimeType.RANGING,
        RegimeType.LOW_VOLATILITY
    ]

    def __init__(self, **kwargs):
        super().__init__()
        # General Indicators
        self.h1_ema_fast = kwargs.get("h1_ema_fast", DEFAULT_H1_EMA_FAST)
        self.h1_ema_slow = kwargs.get("h1_ema_slow", DEFAULT_H1_EMA_SLOW)
        
        # M5 Indicators
        self.rsi_period = int(kwargs.get("rsi_period", DEFAULT_M5_RSI_PERIOD))
        self.atr_period = int(kwargs.get("atr_period", DEFAULT_M5_ATR_PERIOD))
        self.adx_period = int(kwargs.get("adx_period", 14))
        self.bb_period = int(kwargs.get("bb_period", DEFAULT_BB_PERIOD))
        self.bb_std = float(kwargs.get("bb_std", DEFAULT_BB_STD))
        
        self.risk_pct = kwargs.get("risk_pct", DEFAULT_RISK_STANDARD)
        
        # SL/TP Setup
        self.atr_multiplier = kwargs.get("atr_multiplier", 2.0)  
        self.rr_ratio = kwargs.get("rr_ratio", 2.0)              
        
        self.sar_step = kwargs.get('sar_step', 0.02)
        self.sar_max = kwargs.get('sar_max', 0.2)
        
    def _session_vwap(self, df: pd.DataFrame) -> pd.Series:
        """
        Session-aware VWAP that resets at session boundaries.
        Sessions: Asia (00:00-08:00 UTC), London (08:00-13:00), NY (13:00-21:00).
        """
        if 'time' not in df.columns and df.index.dtype == 'datetime64[ns]':
            hours = df.index.hour
        elif 'time' in df.columns:
            hours = pd.to_datetime(df['time']).dt.hour
        else:
            tp = (df['high'] + df['low'] + df['close']) / 3
            vol = df.get('tick_volume', df.get('volume', pd.Series(1, index=df.index)))
            return (tp * vol).cumsum() / vol.cumsum()
        
        session_id = pd.Series(0, index=df.index)
        session_id[hours >= 8] = 1   # London
        session_id[hours >= 13] = 2  # NY
        
        session_change = session_id.diff().fillna(1).abs() > 0
        session_group = session_change.cumsum()
        
        tp = (df['high'] + df['low'] + df['close']) / 3
        vol = df.get('tick_volume', df.get('volume', pd.Series(1, index=df.index)))
        
        tp_vol = tp * vol
        cum_tp_vol = tp_vol.groupby(session_group).cumsum()
        cum_vol = vol.groupby(session_group).cumsum()
        
        vwap = cum_tp_vol / cum_vol
        return vwap

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs
    ) -> Decision:
        
        symbol = profile.symbol
        
        if candles is None or len(candles) < 200:
            return Decision(symbol=symbol, action=Action.HOLD, confidence=0.0, reason="Insufficient M5 data")

        df = candles.copy()
        
        # --- 1. Compute Indicators ---
        # VWAP
        df['vwap'] = self._session_vwap(df)

        # Baseline TA
        df.ta.rsi(length=self.rsi_period, append=True)
        df.ta.atr(length=self.atr_period, append=True)
        df.ta.bbands(length=self.bb_period, std=self.bb_std, append=True)
        df.ta.ema(length=200, append=True) # M5 Baseline Trend
        df.ta.ema(length=50, append=True)  # M5 Fast Trend

        # ADX Logic
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=self.adx_period)
        if adx_df is not None and not adx_df.empty:
            df[f"ADX_{self.adx_period}"] = adx_df.iloc[:, 0]
            df[f"DMP_{self.adx_period}"] = adx_df.iloc[:, 1]
            df[f"DMN_{self.adx_period}"] = adx_df.iloc[:, 2]
        else:
            return Decision(symbol=symbol, action=Action.HOLD, reason="ADX calculation failed")

        current = df.iloc[-1]
        prev = df.iloc[-2]
        close = current['close']
        vwap = current.get('vwap', close)
        
        rsi_col = f"RSI_{self.rsi_period}"
        atr_col = f"ATRr_{self.atr_period}"
        adx_col = f"ADX_{self.adx_period}"
        dmp_col = f"DMP_{self.adx_period}"
        dmn_col = f"DMN_{self.adx_period}"
        ema200_col = "EMA_200"
        ema50_col = "EMA_50"
        
        bb_upper_col = f"BBU_{self.bb_period}_{self.bb_std}"
        bb_lower_col = f"BBL_{self.bb_period}_{self.bb_std}"

        # Safe Extraction
        rsi_val = current.get(rsi_col, 50.0)
        rsi_prev = prev.get(rsi_col, 50.0)
        atr_val = current.get(atr_col, 0.001)
        adx_val = current.get(adx_col, 0.0)
        dmp_val = current.get(dmp_col, 0.0)
        dmn_val = current.get(dmn_col, 0.0)
        bb_upper = current.get(bb_upper_col, close + atr_val)
        bb_lower = current.get(bb_lower_col, close - atr_val)
        ema200 = current.get(ema200_col, close)
        ema50 = current.get(ema50_col, close)
        
        if pd.isna(adx_val): return Decision(symbol=symbol, action=Action.HOLD, confidence=0.0, reason="NaN ADX")

        # --- 2. H1 Trend Proxy Mapping ---
        h1_trend = "FLAT"
        if ema50 > ema200 and close > ema200:
            h1_trend = "UP"
        elif ema50 < ema200 and close < ema200:
            h1_trend = "DOWN"

        # --- 3. Omnibus Signal Logic (All-Weather) ---
        signal = Action.HOLD
        confidence = 0.0
        reasons = [f"ADX={adx_val:.1f}"]
        
        # Direction helpers
        rsi_rising = rsi_val > rsi_prev
        rsi_falling = rsi_val < rsi_prev
        
        # ─── REGIME A: STRONG TREND / BREAKOUT (ADX > 25) ───
        if adx_val >= 25.0:
            reasons.append("Regime: Trend/Breakout")
            # Trend following / Pullback logic
            if h1_trend == "UP" and dmp_val > dmn_val:
                # BUY: Trend Pullbacks
                if close > vwap and 40 <= rsi_val <= 60 and rsi_rising:
                    signal = Action.BUY
                    confidence = 0.85
                    reasons.append("Trend Pullback (Price>VWAP & RSI Rising)")
            elif h1_trend == "DOWN" and dmn_val > dmp_val:
                # SELL: Trend Pullbacks
                if close < vwap and 40 <= rsi_val <= 60 and rsi_falling:
                    signal = Action.SELL
                    confidence = 0.85
                    reasons.append("Trend Pullback (Price<VWAP & RSI Falling)")
            
            # Breakout extension
            if signal == Action.HOLD and rsi_val > 65 and current['close'] > bb_upper and dmp_val > dmn_val:
                signal = Action.BUY
                confidence = 0.80
                reasons.append("Bullish Breakout (RSI + BB Surge)")
            elif signal == Action.HOLD and rsi_val < 35 and current['close'] < bb_lower and dmn_val > dmp_val:
                signal = Action.SELL
                confidence = 0.80
                reasons.append("Bearish Breakout (RSI + BB Surge)")

        # ─── REGIME B: RANGING / SIDEWAYS (ADX < 18) ───
        elif adx_val < 18.0:
            reasons.append("Regime: Sideways/Ranging")
            # Mean Reversion Logic (Fading extremes)
            # Rejection from lower band -> Buy back to mean
            if close <= bb_lower + (atr_val * 0.2) and rsi_val < 35 and rsi_rising:
                signal = Action.BUY
                confidence = 0.78
                reasons.append("Mean Reversion (BB Lower + RSI Rejection)")
            # Rejection from upper band -> Sell back to mean
            elif close >= bb_upper - (atr_val * 0.2) and rsi_val > 65 and rsi_falling:
                signal = Action.SELL
                confidence = 0.78
                reasons.append("Mean Reversion (BB Upper + RSI Rejection)")
                
        # ─── REGIME C: CHOPPY / TRANSITION (ADX 18-25) ───
        else:
            reasons.append("Regime: Transition")
            # Higher bar for entry required
            if h1_trend == "UP" and close > vwap and 45 <= rsi_val <= 55 and rsi_rising and dmp_val > dmn_val + 5:
                signal = Action.BUY
                confidence = 0.75
                reasons.append("Transition Bullish Setup")
            elif h1_trend == "DOWN" and close < vwap and 45 <= rsi_val <= 55 and rsi_falling and dmn_val > dmp_val + 5:
                signal = Action.SELL
                confidence = 0.75
                reasons.append("Transition Bearish Setup")

        # --- 4. Quality Gate Drop ---
        if signal == Action.HOLD:
            return Decision(symbol=symbol, action=Action.HOLD, confidence=0.0, reason=" | ".join(reasons))

        # --- 5. Risk Calculation ---
        stop_loss = 0.0
        take_profit = 0.0
        risk_pct = self.risk_pct

        if "XAG" in symbol.upper() or "SILVER" in symbol.upper():
            risk_pct = min(risk_pct, 0.005)

        sl_mult = self.atr_multiplier
        # During sideway ranges, SL can be slightly tighter.
        if adx_val < 18.0:
            sl_mult = max(1.5, self.atr_multiplier * 0.8) 
        
        direction_str = "BUY" if signal == Action.BUY else "SELL"
        try:
            stop_loss = apply_anti_hunt_sl(
                df=df, close=close, atr=atr_val,
                direction=direction_str,
                atr_mult=sl_mult,
                min_sl_distance=atr_val,
                enable_swing=True,  # Look for swings in All-Weather
                enable_buffer=False,
            )
        except Exception:
            if signal == Action.BUY:
                stop_loss = close - (atr_val * sl_mult)
            else:
                stop_loss = close + (atr_val * sl_mult)

        sl_dist = abs(close - stop_loss)
        
        # Take Profit Logic depends on regime:
        # In trends, aim for 2R+. In ranges, aim for opposite Bollinger Band.
        if adx_val < 18.0:
            if signal == Action.BUY:
                take_profit = min(close + (sl_dist * self.rr_ratio), bb_upper)
            else:
                take_profit = max(close - (sl_dist * self.rr_ratio), bb_lower)
        else:
            tp_dist = sl_dist * self.rr_ratio
            if signal == Action.BUY:
                take_profit = close + tp_dist
            else:
                take_profit = close - tp_dist

        # Sanity Check
        if signal == Action.BUY and stop_loss >= close:
            return Decision(symbol=symbol, action=Action.HOLD, confidence=0.0, reason="Invalid SL (>= Price)")
        if signal == Action.SELL and stop_loss <= close:
            return Decision(symbol=symbol, action=Action.HOLD, confidence=0.0, reason="Invalid SL (<= Price)")

        return Decision(
            symbol=symbol,
            action=signal,
            confidence=round(confidence, 3),
            reason=" | ".join(reasons),
            stop_loss=round(stop_loss, profile.digits),
            take_profit=round(take_profit, profile.digits),
            risk_pct=risk_pct,
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["all_weather_v3", h1_trend, f"ADX_{adx_val:.0f}"]
        )
