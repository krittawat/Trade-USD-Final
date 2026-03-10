
import pandas as pd
import pandas as pd
try:
    import app.analysis.indicators as ind
except ImportError:
    from app.utils.safe_ta import SafeTA as ta
from typing import Dict, Any, List
from enum import Enum

# Import sub-strategies (Assuming they exist or we reimplement logic)
from app.strategy.omega_strike_v2 import omega_strike_v2

class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    NONE = "NONE"

class UnifiedSignal:
    def __init__(self, signal, confidence, entry_method, entry_price, sl, tp, reasons):
        self.signal = signal
        self.confidence = confidence
        self.entry_method = entry_method
        self.entry_price = entry_price
        self.stop_loss = sl
        self.take_profit = tp
        self.reasons = reasons

class UnifiedAlphaStrategy:
    def __init__(self):
        self.name = "UNIFIED_ALPHA"
        self.description = "Combines Guerrilla (M1), Omega (M5), and Quantum Hedge"
        
        # Sub-strategy instances
        self.omega = omega_strike_v2
        
    def analyze(self, df_m5: pd.DataFrame, direction: str = "AUTO", symbol: str = "XAUUSD") -> UnifiedSignal:
        
        # 1. LAYER 1: OMEGA STRIKE V2 (M5 Trend)
        # We reuse the existing powerful logic
        omega_signal = self.omega.analyze(df_m5, direction, symbol)
        
        if omega_signal.signal in (Signal.BUY, Signal.SELL):
            # Omega found a big move! Prioritize this.
            return UnifiedSignal(
                signal=omega_signal.signal,
                confidence=omega_signal.confidence,
                entry_method=f"ALPHA_OMEGA_{omega_signal.entry_method}",
                entry_price=omega_signal.entry_price,
                sl=omega_signal.stop_loss,
                tp=omega_signal.take_profit,
                reasons=[f"🦁 Alpha Trigger: {r}" for r in omega_signal.reasons]
            )
            
        # 2. LAYER 2: GUERRILLA FEEDER (M1 Scalp Simulation)
        # Since we only receive M5 DF here usually, we simulate with what we have 
        # OR we check if the M5 bar is extreme enough to trigger a "Fast Scalp"
        
        # Guerrilla Logic Re-implementation for M5 context (approximated)
        # If High RSI > 80 or Low RSI < 20 on M5, it implies M1 is screaming.
        
        last = df_m5.iloc[-1]
        rsi = last['RSI'] if 'RSI' in last else 50
        price = last['close']
        atr = last['ATR'] if 'ATR' in last else 1.0
        
        # CENT ACCOUNT SCALING: 
        # Standard Scalp TP = $1.50 -> 150 USC
        # 2.0 Lot * 100 points ($0.1) * 0.1 value ?? > Let's stick to Price Delta
        
        if rsi < 25: # Oversold enough for a quick bite
             return UnifiedSignal(
                signal=Signal.BUY,
                confidence=60, # Lower confidence, volume will be handled by Manager
                entry_method="ALPHA_GUERRILLA_BUY",
                entry_price=price,
                sl=price - (atr * 0.5), # Tight SL
                tp=price + (atr * 1.0), # Quick TP
                reasons=["🐜 Guerrilla Feed: M5 Oversold Dip"]
            )
            
        if rsi > 75: # Overbought enough for a quick bite
             return UnifiedSignal(
                signal=Signal.SELL,
                confidence=60,
                entry_method="ALPHA_GUERRILLA_SELL",
                entry_price=price,
                sl=price + (atr * 0.5), # Tight SL
                tp=price - (atr * 1.0), # Quick TP
                reasons=["🐜 Guerrilla Feed: M5 Overbought Peak"]
            )
            
        # 3. LAYER 3: WAITING
        return UnifiedSignal(Signal.WAIT, 0, "NONE", 0, 0, 0, ["Alpha Scan: No Clear Setup"])

unified_alpha = UnifiedAlphaStrategy()
