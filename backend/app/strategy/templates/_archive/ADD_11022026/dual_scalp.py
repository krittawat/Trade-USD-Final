"""
Dual Scalp Strategy - High Frequency Reversal
Fast Entry / Fast Exit for Two-Sided Markets.

Key Features:
1. M1/M5 Timeframe Scalping
2. RSI Extreme Reversal (Overbought/Oversold)
3. Bollinger Band Bounce
4. Tight SL/TP for rapid turnover
"""
import pandas as pd
from typing import Dict, Optional
from dataclasses import dataclass
from enum import Enum

class Signal(Enum):
    NONE = "NONE"
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"


@dataclass
class ScalpSignal:
    signal: Signal
    confidence: float
    entry_method: str
    entry_price: float
    stop_loss: float
    take_profit: float
    reasons: list

    def is_valid(self) -> bool:
        return self.signal in (Signal.BUY, Signal.SELL) and self.confidence >= 50


class DualScalpStrategy:
    """
    Dual Scalp: Fast mean reversion strategy.
    Designed for M1/M5 volatility.
    """
    
    # OPTIMIZED Parameters from Grid Search (Profit $26,064)
    CONFIG = {
        "rsi_period": 7,       # Fast RSI
        "rsi_buy": 30,         # Optimized: Less aggressive for higher WR
        "rsi_sell": 80,        # Optimized: Wait for extreme overbought
        "bb_period": 20,
        "bb_dev": 2.2,         # Wider bands to avoid false breakouts
        "sl_atr_mult": 2.0,    # Optimized: Wider SL for noise
        "tp_atr_mult": 3.0,    # Optimized: Better Risk:Reward
        "min_volatility": 0    # No min volatility for now, trade everything
    }

    def __init__(self):
        pass

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> ScalpSignal:
        """
        Analyze M1/M5 data for Scalp opportunities.
        """
        if len(df) < 50:
            return self._no_signal("Insufficient data")
            
        # Calculate Indicators
        # RSI 7 (Fast)
        if 'RSI_7' not in df.columns:
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0).rolling(window=7).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=7).mean()
            rs = gain / loss
            df['RSI_7'] = 100 - (100 / (1 + rs))

        # Bollinger Bands (20, 2.2)
        if 'BB_UPPER' not in df.columns:
            sma = df['close'].rolling(20).mean()
            std = df['close'].rolling(20).std()
            df['BB_UPPER'] = sma + (std * self.CONFIG['bb_dev'])
            df['BB_LOWER'] = sma - (std * self.CONFIG['bb_dev'])
            df['BB_MID'] = sma

        # ATR (for SL/TP)
        if 'ATR' not in df.columns:
            tr = pd.concat([
                df['high'] - df['low'], 
                (df['high'] - df['close'].shift()).abs(), 
                (df['low'] - df['close'].shift()).abs()
            ], axis=1).max(axis=1)
            df['ATR'] = tr.rolling(14).mean()

        last = df.iloc[-1]
        price = last['close']
        rsi = last.get('RSI_7', 50)
        upper = last.get('BB_UPPER', price)
        lower = last.get('BB_LOWER', price)
        atr = last.get('ATR', price * 0.001) # Fallback 0.1% of price if ATR missing
        
        if pd.isna(atr) or atr == 0:
            atr = price * 0.001

        reasons = []
        confidence = 50

        # --- SCALP BUY ---
        # Logic: RSI < 25 AND Price touching/below Lower Band
        is_buy = rsi < self.CONFIG['rsi_buy'] and price <= lower
        
        # --- SCALP SELL ---
        # Logic: RSI > 75 AND Price touching/above Upper Band
        is_sell = rsi > self.CONFIG['rsi_sell'] and price >= upper

        # Direction Filter
        check_buy = direction in ("AUTO", "BUY", "BOTH")
        check_sell = direction in ("AUTO", "SELL", "BOTH")

        if check_buy and is_buy:
            confidence = 80
            reasons.append(f"⚡ RSI Oversold ({rsi:.1f}) + BB Low Touch")
            sl = price - (atr * self.CONFIG['sl_atr_mult'])
            tp = price + (atr * self.CONFIG['tp_atr_mult'])
            
            # Trend Counter-Check (Optional): If M15 Trend is down, lower confidence?
            # For Scalp, we ignore trend often, but let's be safe.
            # Assuming pure mean reversion for now.
            
            return ScalpSignal(
                signal=Signal.BUY,
                confidence=float(confidence),
                entry_method="SCALP_RSI_BB",
                entry_price=float(price),
                stop_loss=float(sl),
                take_profit=float(tp),
                reasons=reasons
            )

        if check_sell and is_sell:
            confidence = 80
            reasons.append(f"⚡ RSI Overbought ({rsi:.1f}) + BB High Touch")
            sl = price + (atr * self.CONFIG['sl_atr_mult'])
            tp = price - (atr * self.CONFIG['tp_atr_mult'])
            
            return ScalpSignal(
                signal=Signal.SELL,
                confidence=float(confidence),
                entry_method="SCALP_RSI_BB",
                entry_price=float(price),
                stop_loss=float(sl),
                take_profit=float(tp),
                reasons=reasons
            )
            
        # Waiting Logic
        if rsi < 40:
            reasons.append(f"Bearish Pressure (RSI {rsi:.1f})")
        elif rsi > 60:
             reasons.append(f"Bullish Pressure (RSI {rsi:.1f})")
        else:
             reasons.append("Chop / Sideways")

        return self._no_signal(reasons[0] if reasons else "Scanning M1/M5...")

    def _no_signal(self, reason: str) -> ScalpSignal:
        return ScalpSignal(
            signal=Signal.NONE,
            confidence=0,
            entry_method="NONE",
            entry_price=0,
            stop_loss=0,
            take_profit=0,
            reasons=[reason]
        )

# Global Instance
dual_scalp_strategy = DualScalpStrategy()
