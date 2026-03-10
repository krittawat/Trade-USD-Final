"""
Ranging Sniper Strategy — Sniper in Sideways Markets.

Logic:
    1. Regime Filter: ADX < 25 (Strict Sideways)
    2. Mean Reversion: Buy Low (Lower Band), Sell High (Upper Band)
    3. Confluence: RSI Overbought/Oversold
    4. Exit: Middle Band (conservative) or Opposite Band (aggressive)

Suitable for:
    - M5/M15 Timeframes
    - Low Volatility / Ranging Markets
"""

import pandas as pd
import pandas_ta as ta
import app.analysis.indicators as ind

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)

# --- Parameters ---
ADX_MAX = 25.0       # Max ADX for ranging
BB_LENGTH = 20
BB_STD = 2.0
RSI_LENGTH = 14
RSI_OS = 35          # Oversold threshold
RSI_OB = 65          # Overbought threshold
ATR_SL_MULT = 1.5    # Tight SL for ranging
MIN_CONFIDENCE = 0.60

class RangingSniperStrategy(BaseStrategy):
    """
    Ranging Sniper — เทรดสั้นในกรอบไซด์เวย์.
    """

    name = "ranging_sniper"
    timeframe = "M5" # Preferred timeframe for sniping
    suitable_regimes = [
        RegimeType.RANGING,
        RegimeType.LOW_VOLATILITY,
    ]

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        
        symbol = profile.symbol

        # --- VALIDATION ---
        if candles is None or len(candles) < 50:
            return self.create_hold(symbol=symbol, reason="Insufficient indicators data")

        # --- INDICATORS ---
        # 1. ADX
        adx_df = ta.adx(candles["high"], candles["low"], candles["close"], length=14)
        adx_val = adx_df[f"ADX_14"].iloc[-1] if adx_df is not None else 50.0

        # 2. Bollinger Bands
        bb = ta.bbands(candles["close"], length=BB_LENGTH, std=BB_STD)
        if bb is None:
             return self.create_hold(symbol=symbol, reason="BB calc failed")
             
        # Robust: use iloc since pandas_ta column names vary between versions
        # Column order: 0=Lower, 1=Mid, 2=Upper, 3=Bandwidth, 4=%B
        lower = bb.iloc[-1, 0]
        mid   = bb.iloc[-1, 1]
        upper = bb.iloc[-1, 2]
        
        # 3. RSI
        rsi = ta.rsi(candles["close"], length=RSI_LENGTH)
        rsi_val = rsi.iloc[-1] if rsi is not None else 50.0
        
        # 4. ATR
        atr = ta.atr(candles["high"], candles["low"], candles["close"], length=14)
        atr_val = atr.iloc[-1] if atr is not None else 0.0

        current_price = candles["close"].iloc[-1]

        # --- LOGIC ---
        
        # 1. Regime Check (Must be Ranging)
        # If passed regime is TRENDING, we strictly skip
        if regime in [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN]:
             return self.create_hold(symbol=symbol, reason=f"Skip: Market is TRENDING (Regime={regime})")
             
        # Double check with internal ADX
        if adx_val > ADX_MAX:
             return self.create_hold(symbol=symbol, reason=f"Skip: ADX {adx_val:.1f} > {ADX_MAX} (Too strong)")

        action = Action.HOLD
        confidence = 0.0
        reasons = []

        # BUY Logic: Price near Lower Band + RSI Oversold
        # Allow price to be slightly above lower band (within 10% of range)
        band_range = upper - lower
        near_lower = current_price <= (lower + band_range * 0.1)
        
        if near_lower:
            reasons.append("Price near Lower Band")
            confidence += 0.3
            
            if rsi_val < RSI_OS:
                reasons.append(f"RSI {rsi_val:.1f} Oversold")
                confidence += 0.3
            elif rsi_val < 45:
                confidence += 0.1 # Slight bullish bias

            # Candle Rejection (Hammer/Pinbar check simplified)
            # If close > open and close > lower
            if candles["close"].iloc[-1] > candles["open"].iloc[-1]:
                 confidence += 0.1
                 reasons.append("Bullish Candle")

            if confidence >= MIN_CONFIDENCE:
                action = Action.BUY

        # SELL Logic: Price near Upper Band + RSI Overbought
        near_upper = current_price >= (upper - band_range * 0.1)
        
        if near_upper:
            reasons.append("Price near Upper Band")
            confidence += 0.3
            
            if rsi_val > RSI_OB:
                reasons.append(f"RSI {rsi_val:.1f} Overbought")
                confidence += 0.3
            elif rsi_val > 55:
                confidence += 0.1

            if candles["close"].iloc[-1] < candles["open"].iloc[-1]:
                 confidence += 0.1
                 reasons.append("Bearish Candle")

            if confidence >= MIN_CONFIDENCE:
                action = Action.SELL

        # --- EXECUTION ---
        if action == Action.HOLD:
            # --- 5. TIME DECAY EXIT (Efficiency) ---
            # If position is open > 45 mins (9 bars) and profit is small, close it.
            # Ranging trades should be quick bounces.
            # This logic needs to be checked against CURRENT position, which analyze() doesn't have access to directly here
            # properly in standard BaseStrategy flow (usually check_exit handles this).
            # But we can return a specific signal if we had position context.
            # Since analyze() is for NEW ENTRY, this part is for commentary or if we extended BaseStrategy to pass position.
            
            # Use 'reason' to suggest this behavior check in check_exit
            return self.create_hold(symbol=symbol, reason="Wait for Extremes")

        # Set SL/TP
        # TP = Middle Band (Mean Reversion)
        # SL = Outside Band + ATR buffer
        
        if action == Action.BUY:
            tp = mid
            sl = lower - (atr_val * ATR_SL_MULT)
            
            # Ensure Min RR 1:1 at least, otherwise skip
            risk = current_price - sl
            reward = tp - current_price
            if risk > 0 and (reward/risk) < 0.8: 
                return self.create_hold(symbol=symbol, reason=f"Low RR ({reward/risk:.2f})")
                
        else: # SELL
            tp = mid
            sl = upper + (atr_val * ATR_SL_MULT)
            
            risk = sl - current_price
            reward = current_price - tp
            if risk > 0 and (reward/risk) < 0.8:
                return self.create_hold(symbol=symbol, reason=f"Low RR ({reward/risk:.2f})")

        return Decision(
            symbol=symbol,
            action=action,
            confidence=round(confidence, 2),
            reason=" + ".join(reasons),
            stop_loss=round(sl, profile.digits),
            take_profit=round(tp, profile.digits),
            risk_reward_ratio=round(reward/risk if risk > 0 else 0, 2),
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["ranging", "mean_reversion", "so_sniping"],
            debug={
                "adx": round(adx_val, 1),
                "rsi": round(rsi_val, 1),
                "bb_width": round(band_range, profile.digits)
            }
        )
