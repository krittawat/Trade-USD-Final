import pandas as pd
import pandas_ta as ta
import numpy as np
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass
from enum import Enum
import logging

# Configure logging
logger = logging.getLogger(__name__)

class Signal(Enum):
    NONE = "NONE"
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"

@dataclass
class PredictaV4Signal:
    signal: Signal
    confidence: float
    long_pct: float
    short_pct: float
    confluence_score: int
    is_perfect: bool
    reasons: List[str]
    stop_loss: float = 0.0
    take_profit: float = 0.0

class PredictaFuturesV4Strategy:
    """
    Predicta Futures - Next Candle Predictor V4
    Research Enhanced Edition | Target: 75%+ Win Rate
    
    Features:
    - Custom Supertrend
    - Volume Delta (Leading Indicator)
    - Volatility Regime Detection
    - 8-Point Confluence System
    - Dynamic Scoring
    - Perfect Time Logic (with Delta Confirmation)
    """
    
    def __init__(self):
        # --- Main Settings ---
        self.atr_length = 14
        self.st_factor = 3.0
        self.st_period = 10
        
        # --- Confluence Settings ---
        self.min_confluence = 5
        self.min_volume_ratio = 0.8
        self.adx_threshold = 25
        
        # --- Indicator Settings ---
        self.ema_fast = 8
        self.ema_med = 21
        self.ema_slow = 50
        self.rsi_period = 14
        self.macd_fast = 12
        self.macd_slow = 26
        self.macd_sig = 9
        self.stoch_k = 14
        self.stoch_d = 3
        self.vol_sma = 20
        self.adx_len = 14
        
        # --- Volatility Settings ---
        self.vol_lookback = 100

    def analyze(self, df: pd.DataFrame) -> PredictaV4Signal:
        """
        Analyzes the dataframe and returns a signal based on Predicta V4 logic.
        Expects df to have columns: open, high, low, close, volume
        """
        if df is None or len(df) < self.vol_lookback + 10:
             return self._no_signal("Insufficient Data")

        # Working on a copy to avoid SettingWithCopy warnings on the original df
        df = df.copy()
        
        # Standardize column names (MT5 uses 'tick_volume')
        if 'volume' not in df.columns and 'tick_volume' in df.columns:
            df['volume'] = df['tick_volume']
        
        # 1. Calculate Core Indicators
        self._calc_indicators(df)
        
        # 2. Calculate Custom Supertrend
        self._calc_custom_supertrend(df)
        
        # 3. Calculate Volume Delta
        self._calc_volume_delta(df)
        
        # 4. Volatility Regime
        self._calc_volatility_regime(df)
        
        # 5. Get Last Candle for Analysis
        last = df.iloc[-1]
        prev = df.iloc[-2]
             
        # 6. Scoring & Confluence
        return self._evaluate_signal(last, prev, df)

    def _calc_indicators(self, df: pd.DataFrame):
        # EMAs
        df['ema8'] = ta.ema(df['close'], length=self.ema_fast)
        df['ema21'] = ta.ema(df['close'], length=self.ema_med)
        df['ema50'] = ta.ema(df['close'], length=self.ema_slow)
        
        # ATR
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=self.atr_length)
        
        # RSI
        df['rsi'] = ta.rsi(df['close'], length=self.rsi_period)
        
        # MACD
        macd = ta.macd(df['close'], fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_sig)
        if macd is not None:
             df['macd_line'] = macd.iloc[:, 0]
             df['macd_hist'] = macd.iloc[:, 1]
             df['macd_signal'] = macd.iloc[:, 2]
        else:
             # Fallback if calculation fails
             df['macd_line'] = 0; df['macd_hist'] = 0; df['macd_signal'] = 0

        # Stochastic
        stoch = ta.stoch(df['high'], df['low'], df['close'], k=self.stoch_k, d=self.stoch_d)
        if stoch is not None:
            df['stoch_k'] = stoch.iloc[:, 0]
            df['stoch_d'] = stoch.iloc[:, 1]
        else:
            df['stoch_k'] = 50; df['stoch_d'] = 50

        # Volume stats
        df['vol_sma'] = ta.sma(df['volume'], length=self.vol_sma)
        # Avoid division by zero
        df['vol_ratio'] = df['volume'] / df['vol_sma'].replace(0, 1)
        
        # ADX
        adx = ta.adx(df['high'], df['low'], df['close'], length=self.adx_len)
        if adx is not None:
            df['adx'] = adx.iloc[:, 0] # ADX is usually index 0, DMP 1, DMN 2
        else:
            df['adx'] = 0

    def _calc_custom_supertrend(self, df: pd.DataFrame):
        # Custom Supertrend Implementation based on Pine Script
        # hl2 = (high + low) / 2
        # upperBandRaw = hl2Value + (stFactor * trendAtr)
        # lowerBandRaw = hl2Value - (stFactor * trendAtr)
        # ... logic ...
        
        trend_atr = ta.atr(df['high'], df['low'], df['close'], length=self.st_period)
        hl2 = (df['high'] + df['low']) / 2
        
        upper_band_raw = hl2 + (self.st_factor * trend_atr)
        lower_band_raw = hl2 - (self.st_factor * trend_atr)
        
        # Initialize columns
        df['st_upper'] = 0.0
        df['st_lower'] = 0.0
        df['st_direction'] = 1 # 1 = Down, -1 = Up (Pine logic: -1 is Up in code 'isUptrend = trendDirection == -1')
        
        # Vectorized or iterative? Iterative is safer for recursive logic
        # For performance in backtesting, vectorization is better, but live trading needs accuracy.
        # Given this is "Predicta" and complex, we'll iterate efficiently or use numpy.
        
        # Using numpy for speed
        close = df['close'].values
        ub_raw = upper_band_raw.values
        lb_raw = lower_band_raw.values
        n = len(df)
        
        upper_band = np.zeros(n)
        lower_band = np.zeros(n)
        direction = np.zeros(n) # 1 = Down, -1 = Up
        
        # Initialize first values
        upper_band[0] = ub_raw[0]
        lower_band[0] = lb_raw[0]
        direction[0] = 1
        
        for i in range(1, n):
            # Lower Band Logic
            prev_lower = lower_band[i-1]
            if close[i-1] > prev_lower:
                lower_band[i] = max(lb_raw[i], prev_lower)
            else:
                lower_band[i] = lb_raw[i]
                
            # Upper Band Logic
            prev_upper = upper_band[i-1]
            if close[i-1] < prev_upper:
                upper_band[i] = min(ub_raw[i], prev_upper)
            else:
                upper_band[i] = ub_raw[i]
            
            # Direction Logic
            prev_dir = direction[i-1]
            if prev_dir == 1: # Was Downtrend
                if close[i] > upper_band[i]:
                    direction[i] = -1 # Switch to Up
                else:
                    direction[i] = 1
            else: # Was Uptrend
                if close[i] < lower_band[i]:
                    direction[i] = 1 # Switch to Down
                else:
                    direction[i] = -1
                    
        df['st_direction'] = direction
        df['is_uptrend'] = df['st_direction'] == -1
        df['is_downtrend'] = df['st_direction'] == 1

    def _calc_volume_delta(self, df: pd.DataFrame):
        # Candle Range Volume Apportioning
        # buyVolume = candleRange > 0 ? volume * (close - low) / candleRange : volume * 0.5
        # sellVolume = candleRange > 0 ? volume * (high - close) / candleRange : volume * 0.5
        
        high = df['high']
        low = df['low']
        close = df['close']
        vol = df['volume']
        
        candle_range = high - low
        
        # Handle zero division safely
        ratio_buy = (close - low) / candle_range
        ratio_sell = (high - close) / candle_range
        
        # Where range is 0 (rare), use 0.5 split
        # We can mask this
        mask_valid = candle_range > 0
        
        buy_vol = np.where(mask_valid, vol * ratio_buy, vol * 0.5)
        sell_vol = np.where(mask_valid, vol * ratio_sell, vol * 0.5)
        
        vol_delta = buy_vol - sell_vol
        df['vol_delta'] = vol_delta
        
        # Delta EMA & Momentum
        df['delta_ema'] = ta.ema(pd.Series(vol_delta), length=10)
        df['delta_momentum'] = df['vol_delta'] > df['delta_ema']
        
        df['delta_bullish'] = df['vol_delta'] > 0
        df['delta_bearish'] = df['vol_delta'] < 0

    def _calc_volatility_regime(self, df: pd.DataFrame):
        # atrPercentile = ta.percentrank(atr, 100)
        # volRegime = ...
        
        # percentrank in pandas_ta? or rolling rank
        # We can implement simple rolling percentile
        atr = df['atr']
        
        # efficient rolling rank
        # This gives rank 0..1
        df['atr_rank'] = atr.rolling(100).rank() * 100 
        
        # Multiplier
        # volMultiplier = atrPercentile > 75 ? 0.85 : atrPercentile < 25 ? 1.15 : 1.0
        conditions = [
            (df['atr_rank'] > 75),
            (df['atr_rank'] < 25)
        ]
        choices = [0.85, 1.15]
        df['vol_multiplier'] = np.select(conditions, choices, default=1.0)
        
        # For info
        # conditions_regime = [
        #    (df['atr_rank'] > 75),
        #    (df['atr_rank'] < 25)
        # ]
        # choices_regime = ["HIGH", "LOW"]
        # df['vol_regime'] = np.select(conditions_regime, choices_regime, default="MEDIUM")

    def _evaluate_signal(self, last, prev, df) -> PredictaV4Signal:
        reasons = []
        
        # --- Extract Values ---
        is_uptrend = last['is_uptrend']
        is_downtrend = last['is_downtrend']
        
        macd_line = last['macd_line']; macd_sig = last['macd_signal']; macd_hist = last['macd_hist']
        rsi = last['rsi']
        stoch_k = last['stoch_k']; stoch_d = last['stoch_d']
        vol_ratio = last['vol_ratio']
        vol_delta = last['vol_delta']; delta_ema = last['delta_ema']; delta_mom = last['delta_momentum']
        adx = last['adx']
        
        ema8 = last['ema8']; ema21 = last['ema21']; ema50 = last['ema50']
        
        # --- Scoring System ---
        
        # MACD Score
        macd_score_long = 100 if (macd_line > macd_sig and macd_hist > 0) else \
                          70 if (macd_line > macd_sig) else \
                          50 if (macd_hist > 0) else 20
        
        macd_score_short = 100 if (macd_line < macd_sig and macd_hist < 0) else \
                           70 if (macd_line < macd_sig) else \
                           50 if (macd_hist < 0) else 20
        
        # RSI Score
        rsi_score_long = 100 if (rsi < 30) else 85 if (rsi < 40) else 70 if (rsi < 50) else 50 if (rsi < 60) else 25
        rsi_score_short = 100 if (rsi > 70) else 85 if (rsi > 60) else 70 if (rsi > 50) else 50 if (rsi > 40) else 25
        
        # Stoch Score
        stoch_score_long = 100 if (stoch_k > stoch_d and stoch_k < 20) else \
                           85 if (stoch_k > stoch_d and stoch_k < 50) else \
                           65 if (stoch_k > stoch_d) else 25
                           
        stoch_score_short = 100 if (stoch_k < stoch_d and stoch_k > 80) else \
                            85 if (stoch_k < stoch_d and stoch_k > 50) else \
                            65 if (stoch_k < stoch_d) else 25

        # Vol Score
        vol_score = 100 if (vol_ratio > 2.0) else 80 if (vol_ratio > 1.5) else \
                    60 if (vol_ratio > 1.0) else 45 if (vol_ratio > 0.8) else 25
        
        # Delta Score
        delta_score_long = 100 if (vol_delta > 0 and delta_mom) else \
                           75 if (vol_delta > 0) else \
                           40 if (vol_delta > -abs(delta_ema)) else 20
                           
        delta_score_short = 100 if (vol_delta < 0 and not delta_mom) else \
                            75 if (vol_delta < 0) else \
                            40 if (vol_delta < abs(delta_ema)) else 20
        
        # ADX Score
        adx_score = 100 if (adx > 35) else 85 if (adx > 30) else \
                    70 if (adx > 25) else 50 if (adx > 20) else 30
                    
        # Trend Score
        trend_score_long = 100 if (is_uptrend and ema8 > ema21 and ema21 > ema50) else \
                           80 if (is_uptrend and ema8 > ema21) else \
                           60 if (is_uptrend) else 0
                           
        trend_score_short = 100 if (is_downtrend and ema8 < ema21 and ema21 < ema50) else \
                            80 if (is_downtrend and ema8 < ema21) else \
                            60 if (is_downtrend) else 0
                            
        # Weighted Sums
        long_score_raw = (trend_score_long * 0.23) + (macd_score_long * 0.18) + (delta_score_long * 0.15) + \
                         (rsi_score_long * 0.12) + (stoch_score_long * 0.12) + (adx_score * 0.10) + (vol_score * 0.10)
                         
        short_score_raw = (trend_score_short * 0.23) + (macd_score_short * 0.18) + (delta_score_short * 0.15) + \
                          (rsi_score_short * 0.12) + (stoch_score_short * 0.12) + (adx_score * 0.10) + (vol_score * 0.10)
                          
        vol_mult = last['vol_multiplier']
        long_pct = round(min(100, max(0, long_score_raw * vol_mult)))
        short_pct = round(min(100, max(0, short_score_raw * vol_mult)))
        
        # Normalize to 100% split? Pine script: 
        # totalRaw = longScore + shortScore
        # longPct = totalRaw > 0 ? math.round(longScore / totalRaw * 100) : 50
        # But wait, later it uses `longPct` for threshold check (dynamicThreshold). 
        # Actually in Pine:
        # longScore = ... * volMultiplier
        # totalRaw = longScore + shortScore
        # finalLongPct = longScore / totalRaw * 100
        # BUT the Perfect Time check uses `longPct` (which variable? Pine uses the normalized one?)
        # Let's re-read Pine carefully:
        # longScore = math.round(math.min(100, math.max(0, longScoreRaw * volMultiplier)))
        # totalRaw = longScore + shortScore
        # longPct = totalRaw > 0 ? math.round(longScore / totalRaw * 100) : 50
        # longPerfect = isUptrend and longPct >= dynamicThreshold ...
        
        # So yes, it validates the NORMALIZED percentage against the threshold.
        # This implies it compares Long vs Short strength relative to each other.
        
        total_raw = long_pct + short_pct # using strictly the clipped scores
        final_long_pct = round(long_pct / total_raw * 100) if total_raw > 0 else 50
        final_short_pct = 100 - final_long_pct
        
        # --- Dynamic Threshold ---
        # dynamicThreshold = adxValue > 30 ? 55 : adxValue > 25 ? 60 : adxValue > 20 ? 65 : 70
        dynamic_threshold = 55 if adx > 30 else 60 if adx > 25 else 65 if adx > 20 else 70
        
        # --- Confluence Counts ---
        conf_long = 0
        conf_long += 1 if is_uptrend else 0
        conf_long += 1 if ema8 > ema21 else 0
        conf_long += 1 if macd_line > macd_sig else 0
        conf_long += 1 if stoch_k > stoch_d else 0
        conf_long += 1 if vol_ratio >= self.min_volume_ratio else 0
        conf_long += 1 if adx > self.adx_threshold else 0
        conf_long += 1 if rsi > 50 else 0
        conf_long += 1 if last['delta_bullish'] else 0
        
        conf_short = 0
        conf_short += 1 if is_downtrend else 0
        conf_short += 1 if ema8 < ema21 else 0
        conf_short += 1 if macd_line < macd_sig else 0
        conf_short += 1 if stoch_k < stoch_d else 0
        conf_short += 1 if vol_ratio >= self.min_volume_ratio else 0
        conf_short += 1 if adx > self.adx_threshold else 0
        conf_short += 1 if rsi < 50 else 0
        conf_short += 1 if last['delta_bearish'] else 0
        
        volume_ok = vol_ratio >= self.min_volume_ratio
        
        # --- Perfect Time Logic ---
        # longPerfect = isUptrend and longPct >= dynamicThreshold and confluenceLong >= minConfluence and volumeOk and rsiAbove50 and deltaBullish
        long_perfect = (
            is_uptrend and 
            final_long_pct >= dynamic_threshold and 
            conf_long >= self.min_confluence and 
            volume_ok and 
            rsi > 50 and 
            last['delta_bullish'] # Critical V4 Fix
        )
        
        short_perfect = (
            is_downtrend and 
            final_short_pct >= dynamic_threshold and 
            conf_short >= self.min_confluence and 
            volume_ok and 
            rsi < 50 and 
            last['delta_bearish'] # Critical V4 Fix
        )
        
        # --- Signal Generation ---
        signal = Signal.WAIT
        signal_reasons = []
        is_perfect = False
        
        # Logic: We should signal if "Perfect Time" JUST started or continues?
        # Typically bots signal on the *close* where condition becomes true.
        # Check previous candle for transition if needed, or just signal state.
        # User says "Perfect Time" is the entry.
        
        # Also there are BUY/SELL Signals in Pine:
        # bullSignal = ta.crossover(ema8, ema21) and isUptrend and deltaBullish
        # These are "Ema Cross" signals. "Perfect Time" is clearer.
        # Let's support both but prioritize Perfect Time.
        
        # Check EMA Crossover just for info
        # To do crossover properly we need prev values.
        prev_ema8 = prev['ema8']; prev_ema21 = prev['ema21']
        ema_cross_bull = (prev_ema8 < prev_ema21) and (ema8 > ema21)
        ema_cross_bear = (prev_ema8 > prev_ema21) and (ema8 < ema21)
        
        bull_signal = ema_cross_bull and is_uptrend and last['delta_bullish']
        bear_signal = ema_cross_bear and is_downtrend and last['delta_bearish']
        
        if long_perfect:
            signal = Signal.BUY
            is_perfect = True
            signal_reasons.append(f"⚡ PERFECT LONG (Pct: {final_long_pct}%)")
            signal_reasons.append(f"Confluence: {conf_long}/8")
            if last['delta_bullish']: signal_reasons.append("Delta Confirmed (+)")
            
        elif short_perfect:
            signal = Signal.SELL
            is_perfect = True
            signal_reasons.append(f"⚡ PERFECT SHORT (Pct: {final_short_pct}%)")
            signal_reasons.append(f"Confluence: {conf_short}/8")
            if last['delta_bearish']: signal_reasons.append("Delta Confirmed (-)")
            
        elif bull_signal:
             signal = Signal.BUY
             signal_reasons.append("Standard BUY (EMA Cross + Delta)")
             
        elif bear_signal:
             signal = Signal.SELL
             signal_reasons.append("Standard SELL (EMA Cross + Delta)")
             
        # Calculate suggested SL/TP based on ATR
        # Typical logic: SL below Support / above Resistance or simple ATR multiplier
        # Pine code projection: maxHeight = atr * 2 * volMult
        # We can use that for TP? Or just strict Risk management.
        # User global rules say: "LotSize = ... / StopLossDistance".
        # We provide the SL distance here.
        
        close_price = last['close']
        cur_atr = last['atr']
        
        # 2.0 ATR SL is common for Trend following
        sl_dist = cur_atr * 2.0 * last['vol_multiplier']
        tp_dist = cur_atr * 3.0 * last['vol_multiplier'] # 1.5R
        
        stop_loss = close_price - sl_dist if signal == Signal.BUY else close_price + sl_dist
        take_profit = close_price + tp_dist if signal == Signal.BUY else close_price - tp_dist
        
        return PredictaV4Signal(
            signal=signal,
            confidence=final_long_pct if signal == Signal.BUY else final_short_pct,
            long_pct=final_long_pct,
            short_pct=final_short_pct,
            confluence_score=conf_long if signal == Signal.BUY else conf_short if signal == Signal.SELL else 0,
            is_perfect=is_perfect,
            reasons=signal_reasons,
            stop_loss=stop_loss,
            take_profit=take_profit
        )

    def _no_signal(self, reason: str) -> PredictaV4Signal:
        return PredictaV4Signal(Signal.NONE, 0, 0, 0, 0, False, [reason])

# Instance
predicta_v4_strategy = PredictaFuturesV4Strategy()
