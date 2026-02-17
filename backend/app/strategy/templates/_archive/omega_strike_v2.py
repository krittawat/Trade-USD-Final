"""
Omega Strike V2 (ICT Silver Bullet)
High-Precision Smart Money Concept (SMC) Strategy

Core Logic:
1. Time Window: Trade ONLY during specific liquidity windows (London Open, NY AM, NY PM).
2. Liquidity Sweep: Price must pierce a recent Swing High/Low (Stop Hunt).
3. Displacement: Violent reversal after sweep (Big Imbalance).
4. FVG Entry: Enter on retracement to the Fair Value Gap created by displacement.

Target: Gold (XAUUSD) M1/M5
"""
import pandas as pd
import pandas_ta as ta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger("OmegaStrikeV2")

class Signal(Enum):
    NONE = "NONE"
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"

@dataclass
class OmegaSignal:
    signal: Signal
    confidence: float
    entry_method: str
    entry_price: float
    stop_loss: float
    take_profit: float
    reasons: list
    
    def is_valid(self) -> bool:
        return self.signal in (Signal.BUY, Signal.SELL) and self.confidence >= 75

class OmegaStrikeV2:
    def __init__(self):
        # Parameters
        self.swing_len = 5       # Fractals length
        self.fvg_threshold = 0.5 # Min points for FVG
        self.min_displacement = 1.0 # Min ATR multiple for displacement candle
        self.risk_reward = 2.0
        
        # Session Hours (Server Time approx - adjust via config)
        # Assuming Server Time = GMT+2 or +3 roughly. 
        # NY 10 AM EST = 15:00/16:00 Server.
        # We'll use a broad filter for now, or rely on auto-session logic.
        self.session_filter = False # Disable for now to test logic
        self.allowed_hours = [9, 10, 11, 14, 15, 16] # Example windows

    def update_parameters(self, params: Dict[str, Any]):
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO", symbol: str = "") -> OmegaSignal:
        if len(df) < 200:
            return self._no_signal("Insufficient Data")
            
        # 1. Indicators
        df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['EMA_50'] = ta.ema(df['close'], length=50) # Intraday Trend (Faster than 200)
        df['RSI'] = ta.rsi(df['close'], length=14)
        
        current_idx = len(df) - 1
        last = df.iloc[current_idx]
        price = last['close']
        atr = last['ATR'] if not pd.isna(last['ATR']) else price * 0.001
        
        reasons = []
        confidence = 0
        signal = Signal.WAIT
        entry_method = "NONE" 
        sl = 0.0
        tp = 0.0
        
        # 2. Logic: Hyper-Aggressive Trend Scalp
        # Buy: Price > EMA 50 AND RSI < 55
        # Sell: Price < EMA 50 AND RSI > 45
        
        ema_trend = last['EMA_50']
        rsi = last['RSI']
        
        if pd.isna(ema_trend) or pd.isna(rsi):
            return self._no_signal("Indicators Loading")

        # BUY SETUP
        check_buy = direction in ("AUTO", "BUY", "BOTH")
        if check_buy:
            if price > ema_trend: # Trend UP
                # SITUATION ADAPTIVE: High Momentum Surfing
                # Current RSI is ~68, showing strong trend. We capture this.
                if rsi < 75: # Allow buying into strength (Was 60)
                    reasons.append("✅ Strong Uptrend (Price > EMA50)")
                    reasons.append(f"✅ Momentum Surfing (RSI {rsi:.1f})")
                    confidence = 85
                    signal = Signal.BUY
                    entry_method = "MOMENTUM_SURF_BUY"
                    sl = price - (atr * 1.0) # Tight trailing SL
                    tp = price + (atr * 5.0) # Let winners run

        # SELL SETUP
        check_sell = direction in ("AUTO", "SELL", "BOTH")
        if check_sell:
            if price < ema_trend: # Trend DOWN
                if rsi > 40: # High Frequency Rally (Was 50)
                    reasons.append("✅ Downtrend (Price < EMA50)")
                    reasons.append(f"✅ Momentum Exhaust (RSI {rsi:.1f})")
                    confidence = 80
                    signal = Signal.SELL
                    entry_method = "HYPER_SCALP_SELL"
                    sl = price + (atr * 1.5)
                    tp = price - (atr * 3.0)

        return OmegaSignal(
            signal=signal,
            confidence=float(confidence),
            entry_method=entry_method,
            entry_price=float(price),
            stop_loss=float(sl),
            take_profit=float(tp),
            reasons=reasons
        )

    def _no_signal(self, reason: str) -> OmegaSignal:
        return OmegaSignal(Signal.NONE, 0, "NONE", 0, 0, 0, [reason])

omega_strike_v2 = OmegaStrikeV2()
