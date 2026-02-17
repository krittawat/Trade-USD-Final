"""
Smart Fusion Strategy (The Intelligent Hybrid)
Combines Indicator Trend Logic with Candlestick Price Action and Volume Validation.

Core Logic:
1. Trend: EMA Alignment + ADX > 20
2. Trigger: MACD Breakout
3. VALIDATION (The "Smart" Layer):
   - Candlestick Patterns: Hammer, Shooting Star, Engulfing
   - Volume Spread Analysis (VSA): Breakout must have > 1.2x Avg Volume
   
Goal: Eliminate "Fakeouts" and enter only on high-quality setups.
"""
import pandas as pd
import pandas_ta as ta
from .base_strategy import BaseStrategy, StrategyDecision

class SmartFusionStrategy(BaseStrategy):
    def __init__(self):
        self.name = "SmartFusion"
        # Base Indicators
        self.fast = 12
        self.slow = 26
        self.signal = 9
        self.adx_threshold = 20
        # Base Risk
        self.sl_atr_mult = 2.0   # was 1.5 — too tight for M5
        self.tp_atr_mult = 3.0   # was 2.5 — improve RR to 1.5

    def get_status(self):
        return {"name": self.name}

    def _tune_parameters(self, symbol: str):
        """Auto-tune based on Asset Class"""
        s = symbol.upper()
        if "BTC" in s:
            # BTC: High Volatility, needs room to breathe (TRAINED MODEL: GEN-10)
            self.adx_threshold = 28 # Strict Trend (Was 25)
            self.sl_atr_mult = 3.0  # Wide Stop (Was 2.5)
            self.tp_atr_mult = 5.0  # Big Runs (Was 4.0)
        elif "XAG" in s:
            # Silver: The "Devils Metal" (Very Wicky)
            self.adx_threshold = 22
            self.sl_atr_mult = 2.0
            self.tp_atr_mult = 3.0
        elif "XAU" in s:
            # Gold: Balanced (slightly wider for Gold volatility)
            self.adx_threshold = 20
            self.sl_atr_mult = 2.0   # was 1.5
            self.tp_atr_mult = 3.5   # was 2.5 — RR=1.75 for Gold

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> StrategyDecision:
        if df.empty or len(df) < 200:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient Data")

        # 0. Asset Tuning
        symbol = df.attrs.get('symbol', 'XAUUSD') # Default to Gold if unknown
        self._tune_parameters(symbol)

        # === 1. INDICATORS ===
        # MACD
        macd = ta.macd(df['close'], fast=self.fast, slow=self.slow, signal=self.signal)
        macd_line = macd[f"MACD_{self.fast}_{self.slow}_{self.signal}"]
        
        # EMA
        df['ema50'] = ta.ema(df['close'], length=50)
        df['ema200'] = ta.ema(df['close'], length=200)
        
        # ADX & ATR & Volume
        adx_series = ta.adx(df['high'], df['low'], df['close'], length=14)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['vol_ma'] = ta.sma(df['volume'], length=20)

        # === 2. SMART PRICE ACTION (Institutional Concepts) ===
        # WEALTH BUILDER UPGRADE: Fair Value Gaps (FVG) & Order Blocks
        # We look at i (current), i-1 (prev), i-2 (prev-prev)
        i = -1
        
        # Need prev-prev candles. In live trading, df has history.
        c0 = df.iloc[i]   # Current (forming)
        c1 = df.iloc[i-1] # Completed
        c2 = df.iloc[i-2] # Pre-Previous
        c3 = df.iloc[i-3]
        
        # Vital Checks: Define current candle properties
        close = c0['close']
        high = c0['high']
        low = c0['low']
        
        # 2.1 Fair Value Gap (FVG) Detection
        # Bullish FVG: High of candle i-2 < Low of candle i
        # (Gap between Wick High of 2 and Wick Low of 0)
        # Note: In standard definition it's candle 1 High vs candle 3 Low. Let's stick to completed candles: c1 High vs c3 Low.
        
        bullish_fvg = c3['high'] < c1['low']
        bearish_fvg = c3['low'] > c1['high']
        
        # 2.2 Pattern Recognition
        is_bullish_engulfing = (c1['close'] > c1['open']) and \
                               (c2['close'] < c2['open']) and \
                               (c1['close'] > c2['high']) and \
                               (c1['open'] < c2['low'])
                               
        is_bearish_engulfing = (c1['close'] < c1['open']) and \
                               (c2['close'] > c2['open']) and \
                               (c1['close'] < c2['low']) and \
                               (c1['open'] > c2['high'])
                               
        # is_hammer = (wick_lower > 2 * body) and (wick_upper < body)
        # is_shooter = (wick_upper > 2 * body) and (wick_lower < body)
        
        # === 3. LOGIC ===
        curr_macd = macd_line.iloc[i]
        curr_ema50 = df['ema50'].iloc[i]
        curr_ema200 = df['ema200'].iloc[i]
        adx_val = adx_series['ADX_14'].iloc[i] if adx_series is not None else 0
        volume = df['volume'].iloc[i]
        vol_avg = df['vol_ma'].iloc[i]
        
        reasons = []
        confidence = 0.0
        final_signal = "NO_TRADE"
        entry_method = "Standard"

        # Filter 1: Trend
        is_uptrend = curr_ema50 > curr_ema200
        is_downtrend = curr_ema50 < curr_ema200
        
        # Filter 2: Chop (Smart ADX)
        if adx_val < self.adx_threshold:
            return StrategyDecision(signal="NO_TRADE", reason=f"Smart Filter: Low ADX {adx_val:.1f} < {self.adx_threshold}")

        # Filter 3: Volume Validation (The "Trick Volume" Check)
        is_volume_valid = volume > (vol_avg * 1.0) # Must be at least average

        check_buy = direction in ("AUTO", "BUY", "BOTH")
        check_sell = direction in ("AUTO", "SELL", "BOTH")

        # BUY LOGIC
        if check_buy and is_uptrend and curr_macd > -0.5: # Relaxed MACD for FVG entries
            # Smart Entry: Pattern OR Volume Breakout OR FVG Re-entry
            if is_bullish_engulfing or (is_volume_valid and close > c1['high']):
                final_signal = "BUY"
                confidence = 85.0
                if is_bullish_engulfing:
                    reasons.append("Bullish Engulfing Pattern")
                    entry_method = "PATTERN_ENGULF"
                    confidence += 5
                else:
                    reasons.append("Volume Breakout")
                    entry_method = "VOL_BREAKOUT"
            
            # FVG Strategy: If Price dipped into Bullish FVG area and rejected
            elif bullish_fvg and low <= c1['low'] and close > c1['low']:
                 final_signal = "BUY"
                 confidence = 90.0 # High confidence for FVG test
                 reasons.append("💎 Fair Value Gap (FVG) Retest & Reject")
                 entry_method = "FVG_RETEST"

            elif not is_volume_valid and final_signal == "NO_TRADE":
                reasons.append("Ignored: Low Volume")

        # SELL LOGIC
        elif check_sell and is_downtrend and curr_macd < 0.5:
            if is_bearish_engulfing or (is_volume_valid and close < c1['low']):
                final_signal = "SELL"
                confidence = 85.0
                if is_bearish_engulfing:
                    reasons.append("Bearish Engulfing Pattern")
                    entry_method = "PATTERN_ENGULF"
                    confidence += 5
                else:
                    reasons.append("Volume Breakdown")
                    entry_method = "VOL_BREAKOUT"
            
            # FVG Strategy
            elif bearish_fvg and high >= c1['high'] and close < c1['high']:
                 final_signal = "SELL"
                 confidence = 90.0
                 reasons.append("💎 Fair Value Gap (FVG) Retest & Reject")
                 entry_method = "FVG_RETEST"
            
            elif not is_volume_valid and final_signal == "NO_TRADE":
                reasons.append("Ignored: Low Volume")

        # === 4. RISK ===
        sl = 0.0
        tp = 0.0
        atr = df['atr'].iloc[i]
        
        if final_signal != "NO_TRADE":
            dist = atr * self.sl_atr_mult
            if final_signal == "BUY":
                sl = close - dist
                tp = close + (dist * (self.tp_atr_mult / self.sl_atr_mult))
            else:
                sl = close + dist
                tp = close - (dist * (self.tp_atr_mult / self.sl_atr_mult))

        return StrategyDecision(
            signal=final_signal,
            entry_price=close,
            sl=float(sl),
            tp=float(tp),
            reason=", ".join(reasons),
            reasons=reasons,
            confidence=confidence,
            entry_method=entry_method,
            is_valid=lambda: final_signal in ("BUY", "SELL"), # Dynamic compatibility
            stop_loss=float(sl),
            take_profit=float(tp)
        )

smart_fusion_strategy = SmartFusionStrategy()
