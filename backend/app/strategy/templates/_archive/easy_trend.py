
"""
Easy Entry/Exit Trend Strategy
Based on TradingView Indicator "Easy Entry/Exit Trend Colors"

Logic:
1. Trend Filter: SMA 50 vs SMA 200
2. Momentum Trigger: MACD Crossover (12, 26, 9)
3. Volatility Confirmation: Bollinger Bands (10, 1)
4. Strength Filter: ADX > 20 (Avoid Chop)

"""
import pandas as pd
import pandas_ta as ta
from typing import Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum

class Signal(Enum):
    NONE = "NONE"
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"

@dataclass
class EasyTrendSignal:
    signal: Signal
    confidence: float
    entry_method: str
    entry_price: float
    stop_loss: float
    take_profit: float
    reasons: list

    def is_valid(self) -> bool:
        return self.signal in (Signal.BUY, Signal.SELL) and self.confidence >= 70 # Increased confidence requirement

class EasyTrendStrategy:
    def __init__(self):
        self.sma_fast_len = 50
        self.sma_slow_len = 200
        self.macd_fast = 12
        self.macd_slow = 26
        self.macd_sig = 9
        self.bb_len = 10
        self.bb_std = 1.0
        self.rsi_len = 14
        self.rsi_buy = 55
        self.rsi_sell = 45
        self.adx_len = 14
        self.adx_threshold = 20
        # Risk Multipliers
        self.sl_atr_mult = 2.0
        self.tp_atr_mult = 4.0 # 1:2 Risk Reward minimum

    def update_parameters(self, params: Dict[str, Any]):
        """Dynamic update for optimization"""
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> EasyTrendSignal:
        symbol = df.attrs.get('symbol', 'DEFAULT').upper()
        
        # --- DYNAMIC ASSET CONFIGURATION ---
        # Adjust sensitivity based on asset class volatility profile
        if "BTC" in symbol:
            # Bitcoin: Needs wider breathing room and stronger trend confirmation
            self.sl_atr_mult = 2.5
            self.adx_threshold = 25
        elif "XAG" in symbol:
            # Silver: Very volatile, similar to Gold but often jerkier
            self.sl_atr_mult = 2.2
            self.adx_threshold = 22
        elif "JPY" in symbol:
             # JPY Pairs: Often trend strongly but retrace deep
             self.sl_atr_mult = 1.8
        else:
            # Default (Gold/Majors): 
            self.sl_atr_mult = 2.0
            self.adx_threshold = 20
            
        self.tp_atr_mult = self.sl_atr_mult * 2.0 # Maintain 1:2 Ratio
        
        if len(df) < 200:
             return self._no_signal("Insufficient data (Need 200+ candles)")

        # 1. Calculate Indicators
        # SMA Trend
        df['SMA_50'] = ta.sma(df['close'], length=self.sma_fast_len)
        df['SMA_200'] = ta.sma(df['close'], length=self.sma_slow_len)
        
        # MACD
        macd = ta.macd(df['close'], fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_sig)
        if macd is None or macd.empty: return self._no_signal("MACD Error")
        
        df['MACD'] = macd.iloc[:, 0]        # MACD Line
        df['MACD_HIST'] = macd.iloc[:, 1]   # Histogram
        df['MACD_SIGNAL'] = macd.iloc[:, 2] # Signal Line

        # Bollinger Bands on MACD
        bb_macd = ta.bbands(df['MACD'], length=self.bb_len, std=self.bb_std)
        if bb_macd is not None and not bb_macd.empty:
            df['BB_LOWER'] = bb_macd.iloc[:, 0]
            df['BB_MID'] = bb_macd.iloc[:, 1]
            df['BB_UPPER'] = bb_macd.iloc[:, 2]
        else:
            return self._no_signal("BB Error")
        
        # ATR & RSI & ADX
        df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['RSI'] = ta.rsi(df['close'], length=self.rsi_len)
        
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=self.adx_len)
        if adx_df is not None:
             df['ADX'] = adx_df[f"ADX_{self.adx_len}"]
        else:
             df['ADX'] = 0

        # Get Current Candle
        last = df.iloc[-1]
        
        price = last['close']
        atr = last['ATR'] if not pd.isna(last['ATR']) else price * 0.005
        adx = last.get('ADX', 0)
        
        sma_50 = last['SMA_50']
        sma_200 = last['SMA_200']
        macd_val = last['MACD']
        bb_upper = last['BB_UPPER']
        bb_lower = last['BB_LOWER']

        # Logic
        trend = "BULL" if sma_50 > sma_200 else "BEAR"
        
        reasons = []
        confidence = 0
        
        # ADX Filter
        if adx < self.adx_threshold:
            reasons.append(f"⛔ Choppy Market (ADX {adx:.1f} < {self.adx_threshold})")
            # We degrade confidence significantly but don't hard reject to allow monitoring logs
            # But for trade signal, we will likely fail is_valid()
        else:
            reasons.append(f"✅ Strong Trend (ADX {adx:.1f})")
            confidence += 10
        
        bull_impulse = macd_val > bb_upper
        bear_impulse = macd_val < bb_lower
        
        signal = Signal.WAIT
        entry_method = "NONE"
        sl = 0.0
        tp = 0.0

        check_buy = direction in ("AUTO", "BUY", "BOTH")
        check_sell = direction in ("AUTO", "SELL", "BOTH")

        if check_buy:
            if trend == "BULL":
                rsi_val = last.get('RSI', 50)
                if bull_impulse and rsi_val > self.rsi_buy:
                    reasons.append(f"✅ Bullish Impulse + RSI {rsi_val:.1f}")
                    confidence += 50
                    
                    if last['MACD_HIST'] > 0:
                         reasons.append("✅ Momentum Positive")
                         confidence += 10
                         
                    if confidence >= 70:
                        signal = Signal.BUY
                        entry_method = "EASY_TREND_BUY"
                        
                        dist = atr * self.sl_atr_mult
                        sl = price - dist
                        tp = price + (dist * 2.0) # 1:2 Ratio
                else:
                    if not bull_impulse: reasons.append("⛔ No Momentum")
            else:
                 reasons.append("⛔ Trend is BEARISH")

        if check_sell:
             if trend == "BEAR":
                rsi_val = last.get('RSI', 50)
                if bear_impulse and rsi_val < self.rsi_sell:
                    reasons.append(f"✅ Bearish Impulse + RSI {rsi_val:.1f}")
                    confidence += 50
                    
                    if last['MACD_HIST'] < 0:
                         reasons.append("✅ Momentum Negative")
                         confidence += 10
                         
                    if confidence >= 70:
                        signal = Signal.SELL
                        entry_method = "EASY_TREND_SELL"

                        dist = atr * self.sl_atr_mult
                        sl = price + dist
                        tp = price - (dist * 2.0) # 1:2 Ratio
                else:
                    if not bear_impulse: reasons.append("⛔ No Momentum")
             else:
                reasons.append("⛔ Trend is BULLISH")

        # --- SESSION OPTIMIZATION (XAU/USD) ---
        if 'XAU' in symbol and signal != Signal.WAIT:
            # Bad Hours (Server Time 0-23): Avoid Asian open chop and rollover
            # Typically 21:00 - 01:00 is risky due to spread or rollover if scalping, but for trend it might be ok.
            # Keeping safe hours from previous logic but refined.
            # safe_hours = {0, 1, 3, 4, 8, 10, 11, 12, 13, 14, 15, 17, 18, 19, 20, 21, 23}
            # Simplified: Avoid rollover hour
            pass

        return EasyTrendSignal(
            signal=signal,
            confidence=float(confidence),
            entry_method=entry_method,
            entry_price=float(price),
            stop_loss=float(sl),
            take_profit=float(tp),
            reasons=reasons
        )

    def _no_signal(self, reason: str) -> EasyTrendSignal:
        return EasyTrendSignal(Signal.NONE, 0, "NONE", 0, 0, 0, [reason])

easy_trend_strategy = EasyTrendStrategy()
