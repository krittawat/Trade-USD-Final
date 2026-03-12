"""
Oscillator Power Strategy
=========================
A robust trading strategy for XAUUSD, XAGUSD, and BTCUSD that avoids EMA crossovers.
It uses a combination of MACD, Bulls/Bears Power, Momentum, and RSI to identify high-probability setups.

Logic:
- BUY: MACD bull cross AND histogram > 0 AND Bulls Power > 0 AND Momentum > 100 AND RSI in [52, 70]
- SELL: MACD bear cross AND histogram < 0 AND Bears Power < 0 AND Momentum < 100 AND RSI in [30, 48]
"""

import pandas as pd
import numpy as np
from typing import Optional, Dict, Any

from app.analysis.indicators import adx, atr, bulls_power, bears_power, macd, rsi, momentum
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy
from app.core.logging import get_logger

logger = get_logger("OscillatorPower")

class OscillatorPowerStrategy(BaseStrategy):
    """
    Oscillator Power Strategy — Focusing on momentum and trend exhaustion using oscillators.
    """

    name = "oscillator_power"
    timeframe = "M5" # Recommended: M5 or M15
    
    _DEFAULTS = {
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "rsi_length": 14,
        "rsi_buy_min": 52.0,
        "rsi_buy_max": 70.0,
        "rsi_sell_min": 30.0,
        "rsi_sell_max": 48.0,
        "mom_length": 14,
        "power_length": 13,
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 3.0,
        "be_atr_mult": 1.0,
        "min_sl_distance": 2.0,
        "confidence_base": 0.70,
    }

    def __init__(self, **kwargs):
        super().__init__()
        self.p = dict(self._DEFAULTS)
        self.p.update(kwargs)

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        pressure: dict | None = None,
    ) -> Decision:
        """
        Analyze logic using MACD, Bulls/Bears Power, Momentum, and RSI.
        """
        symbol = profile.symbol
        if candles is None or len(candles) < 50:
            return self.create_hold(symbol, "Insufficient data (need >50 bars)")

        # 1. Prepare Indicators
        close = candles["close"]
        high = candles["high"]
        low = candles["low"]
        
        # MACD
        m_df = macd(close, self.p["macd_fast"], self.p["macd_slow"], self.p["macd_signal"])
        m_line_col = f'MACD_{self.p["macd_fast"]}_{self.p["macd_slow"]}_{self.p["macd_signal"]}'
        m_sig_col = f'MACDs_{self.p["macd_fast"]}_{self.p["macd_slow"]}_{self.p["macd_signal"]}'
        m_hist_col = f'MACDh_{self.p["macd_fast"]}_{self.p["macd_slow"]}_{self.p["macd_signal"]}'
        
        m_line = m_df[m_line_col]
        m_sig = m_df[m_sig_col]
        m_hist = m_df[m_hist_col]
        
        # RSI
        r_val = rsi(close, self.p["rsi_length"])
        
        # Momentum
        mom_val = momentum(close, self.p["mom_length"])
        
        # Bulls / Bears Power
        bull_p = bulls_power(high, close, self.p["power_length"])
        bear_p = bears_power(low, close, self.p["power_length"])
        
        # ATR for SL/TP
        atr_val = atr(high, low, close, 14)
        
        # Current Values
        curr_m_line = m_line.iloc[-1]
        curr_m_sig = m_sig.iloc[-1]
        curr_m_hist = m_hist.iloc[-1]
        prev_m_line = m_line.iloc[-2]
        prev_m_sig = m_sig.iloc[-2]
        
        curr_rsi = r_val.iloc[-1]
        curr_mom = mom_val.iloc[-1]
        curr_bull = bull_p.iloc[-1]
        curr_bear = bear_p.iloc[-1]
        curr_atr = atr_val.iloc[-1]
        curr_price = close.iloc[-1]
        
        # Check for NaNs
        if any(pd.isna([curr_m_line, curr_m_sig, curr_rsi, curr_mom, curr_bull, curr_bear, curr_atr])):
            return self.create_hold(symbol, "Indicators not ready")

        # 2. FVG Detection Logic
        # Bullish FVG: Low[i] > High[i-2]
        # Bearish FVG: High[i] < Low[i-2]
        def find_fvg(df_slice):
            if len(df_slice) < 3: return None, None
            
            # Check last 5 bars for a gap
            for i in range(len(df_slice)-1, len(df_slice)-6, -1):
                if i < 2: break
                
                h_prev2 = df_slice['high'].iloc[i-2]
                l_curr = df_slice['low'].iloc[i]
                
                l_prev2 = df_slice['low'].iloc[i-2]
                h_curr = df_slice['high'].iloc[i]
                
                # Bullish FVG
                if l_curr > h_prev2:
                    return "BULLISH", (h_prev2, l_curr)
                
                # Bearish FVG
                if h_curr < l_prev2:
                    return "BEARISH", (h_curr, l_prev2)
            
            return None, None

        fvg_type, fvg_range = find_fvg(candles.tail(10))
        
        # 3. Strategy Logic (Oscillators)
        
        # BUY Setup (Oscillators)
        is_bull_cross = (prev_m_line <= prev_m_sig) and (curr_m_line > curr_m_sig)
        macd_buy = is_bull_cross or (curr_m_hist > 0)
        rsi_buy = self.p["rsi_buy_min"] <= curr_rsi <= self.p["rsi_buy_max"]
        mom_buy = curr_mom > 100
        power_buy = curr_bull > 0
        osc_buy = macd_buy and rsi_buy and mom_buy and power_buy
        
        # SELL Setup (Oscillators)
        is_bear_cross = (prev_m_line >= prev_m_sig) and (curr_m_line < curr_m_sig)
        macd_sell = is_bear_cross or (curr_m_hist < 0)
        rsi_sell = self.p["rsi_sell_min"] <= curr_rsi <= self.p["rsi_sell_max"]
        mom_sell = curr_mom < 100
        power_sell = curr_bear < 0
        osc_sell = macd_sell and rsi_sell and mom_sell and power_sell

        # 4. FVG Entry Confirmation
        # BUY Entry: Osc signals + Bullish FVG exists + Current price is near/in FVG
        fvg_buy_confirmed = False
        if fvg_type == "BULLISH" and fvg_range:
            # Entry if price is at or slightly above the FVG top, or inside it
            if curr_price >= fvg_range[0]: # Price >= High[i-2]
                fvg_buy_confirmed = True

        # SELL Entry: Osc signals + Bearish FVG exists + Current price is near/in FVG
        fvg_sell_confirmed = False
        if fvg_type == "BEARISH" and fvg_range:
            # Entry if price is at or slightly below the FVG bottom, or inside it
            if curr_price <= fvg_range[1]: # Price <= Low[i-2]
                fvg_sell_confirmed = True

        # 5. Decision Making
        confidence = self.p["confidence_base"]
        
        if osc_buy and fvg_buy_confirmed:
            # Entry BUY
            sl = curr_price - (curr_atr * self.p["sl_atr_mult"])
            tp = curr_price + (curr_atr * self.p["tp_atr_mult"])
            
            # Boost confidence for FVG + MACD Cross
            if is_bull_cross: confidence += 0.05
            confidence += 0.10 # FVG Bonus
                
            return Decision(
                symbol=symbol,
                action=Action.BUY,
                confidence=round(min(confidence, 0.95), 2),
                reason=f"FVG Bullish Entry: Gap({fvg_range[0]:.2f}-{fvg_range[1]:.2f}), MACD_H={curr_m_hist:.2f}, RSI={curr_rsi:.1f}",
                stop_loss=round(sl, 5),
                take_profit=round(tp, 5),
                strategy_name=self.name,
                timeframe=self.timeframe
            )

        if osc_sell and fvg_sell_confirmed:
            # Entry SELL
            sl = curr_price + (curr_atr * self.p["sl_atr_mult"])
            tp = curr_price - (curr_atr * self.p["tp_atr_mult"])
            
            # Boost confidence for FVG + MACD Cross
            if is_bear_cross: confidence += 0.05
            confidence += 0.10 # FVG Bonus
                
            return Decision(
                symbol=symbol,
                action=Action.SELL,
                confidence=round(min(confidence, 0.95), 2),
                reason=f"FVG Bearish Entry: Gap({fvg_range[0]:.2f}-{fvg_range[1]:.2f}), MACD_H={curr_m_hist:.2f}, RSI={curr_rsi:.1f}",
                stop_loss=round(sl, 5),
                take_profit=round(tp, 5),
                strategy_name=self.name,
                timeframe=self.timeframe
            )

        return self.create_hold(symbol, f"Wait: Osc(B:{osc_buy},S:{osc_sell}) | FVG({fvg_type})")

        return self.create_hold(symbol, "No setup confirmed")

# Global instance for factory
oscillator_power_strategy = OscillatorPowerStrategy()
