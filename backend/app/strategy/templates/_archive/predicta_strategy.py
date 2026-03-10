
import pandas as pd
import app.analysis.indicators as ind
import numpy as np
from typing import Dict, Any, List
from .base_strategy import BaseStrategy, StrategyDecision

class PredictaStrategy(BaseStrategy):
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
        self.name = "PREDICTA_V4"
        
        # --- Main Settings ---
        self.atr_length = 14
        self.st_factor = 3.0
        self.st_period = 10
        
        # --- Confluence Settings ---
        self.min_confluence = 6
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

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "atr_length": self.atr_length,
            "st_factor": self.st_factor,
            "min_confluence": self.min_confluence
        }

    def analyze(self, df: pd.DataFrame, direction_mode: str = "AUTO") -> StrategyDecision:
        """
        Analyzes the dataframe and returns a signal based on Predicta V4 logic.
        """
        if df is None or len(df) < self.vol_lookback + 10:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient Data")

        # Working on a copy
        df = df.copy()
        
        # Standardize column names
        if 'volume' not in df.columns and 'tick_volume' in df.columns:
            df['volume'] = df['tick_volume']
        
        # Avoid zero volume
        df['volume'] = df['volume'].replace(0, 1)

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
        return self._evaluate_signal(last, prev, direction_mode)

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
        if macd is not None and not macd.empty:
            df['macd_line'] = macd.iloc[:, 0]
            df['macd_hist'] = macd.iloc[:, 1]
            df['macd_signal'] = macd.iloc[:, 2]
        else:
            df['macd_line'] = 0; df['macd_hist'] = 0; df['macd_signal'] = 0

        # Stochastic
        stoch = ta.stoch(df['high'], df['low'], df['close'], k=self.stoch_k, d=self.stoch_d)
        if stoch is not None and not stoch.empty:
            df['stoch_k'] = stoch.iloc[:, 0]
            df['stoch_d'] = stoch.iloc[:, 1]
        else:
            df['stoch_k'] = 50; df['stoch_d'] = 50

        # Volume stats
        df['vol_sma'] = ta.sma(df['volume'], length=self.vol_sma)
        df['vol_ratio'] = df['volume'] / df['vol_sma'].replace(0, 1)
        
        # ADX
        adx = ta.adx(df['high'], df['low'], df['close'], length=self.adx_len)
        if adx is not None and not adx.empty:
            df['adx'] = adx.iloc[:, 0]
        else:
            df['adx'] = 0

    def _calc_custom_supertrend(self, df: pd.DataFrame):
        # Using vectorization where possible, but iterative for recursion
        if len(df) == 0: return

        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        
        # Calculate Trend ATR (separate from signal ATR)
        trend_atr_series = ta.atr(df['high'], df['low'], df['close'], length=self.st_period)
        trend_atr = trend_atr_series.fillna(0).values
        
        hl2 = (high + low) / 2.0
        
        # Pre-calculate bands
        st_prod = self.st_factor * trend_atr
        upper_band_raw = hl2 + st_prod
        lower_band_raw = hl2 - st_prod
        
        n = len(df)
        upper_band = np.zeros(n)
        lower_band = np.zeros(n)
        trend_dir = np.ones(n) # 1 = Down, -1 = Up (Matching Pine: -1 is Up)
        
        # Initial values
        upper_band[0] = upper_band_raw[0]
        lower_band[0] = lower_band_raw[0]
        
        for i in range(1, n):
            # Lower Band
            prev_lower = lower_band[i-1]
            if close[i-1] > prev_lower:
                lower_band[i] = max(lower_band_raw[i], prev_lower)
            else:
                lower_band[i] = lower_band_raw[i]
                
            # Upper Band
            prev_upper = upper_band[i-1]
            if close[i-1] < prev_upper:
                upper_band[i] = min(upper_band_raw[i], prev_upper)
            else:
                upper_band[i] = upper_band_raw[i]
            
            # Trend Direction
            prev_dir = trend_dir[i-1]
            
            # Pine Logic:
            # if prevDirection == -1:
            #     trendDirection := close < lowerBand ? 1 : -1
            # else:
            #     trendDirection := close > upperBand ? -1 : 1
            
            if prev_dir == -1: # Was Up
                if close[i] < lower_band[i]:
                    trend_dir[i] = 1 # Change to Down
                else:
                    trend_dir[i] = -1
            else: # Was Down
                if close[i] > upper_band[i]:
                    trend_dir[i] = -1 # Change to Up
                else:
                    trend_dir[i] = 1
                    
        df['is_uptrend'] = trend_dir == -1
        df['is_downtrend'] = trend_dir == 1

    def _calc_volume_delta(self, df: pd.DataFrame):
        high = df['high']
        low = df['low']
        close = df['close']
        vol = df['volume']
        
        candle_range = high - low
        mask_valid = candle_range > 0
        
        # Estimate Buy/Sell Volume based on candle wick ratios
        # buyVolume = candleRange > 0 ? volume * (close - low) / candleRange : volume * 0.5
        ratio_buy = (close - low) / candle_range
        ratio_sell = (high - close) / candle_range
        
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
        atr = df['atr']
        
        # Percentile Rank of current ATR over last 100 bars
        df['atr_rank'] = atr.rolling(window=self.vol_lookback).rank() * 100 
        
        # Multiplier
        conditions = [
            (df['atr_rank'] > 75), # High Vol
            (df['atr_rank'] < 25)  # Low Vol
        ]
        choices = [0.85, 1.15]
        df['vol_multiplier'] = np.select(conditions, choices, default=1.0)

    def _evaluate_signal(self, last, prev, direction_mode: str) -> StrategyDecision:
        # Defaults
        signal = "NO_TRADE"
        confidence = 0.0
        reasons = []
        
        # --- Value Extraction ---
        is_uptrend = bool(last['is_uptrend'])
        is_downtrend = bool(last['is_downtrend'])
        
        # Core
        macd_line = last['macd_line']; macd_sig = last['macd_signal']; macd_hist = last['macd_hist']
        rsi = last['rsi']
        stoch_k = last['stoch_k']; stoch_d = last['stoch_d']
        vol_ratio = last['vol_ratio']
        adx = last['adx']
        
        # Delta
        vol_delta = last['vol_delta']
        delta_mom = bool(last['delta_momentum'])
        delta_ema = last['delta_ema'] if not pd.isna(last['delta_ema']) else 0
        delta_bullish = bool(last['delta_bullish'])
        delta_bearish = bool(last['delta_bearish'])
        
        # Trend
        ema8 = last['ema8']; ema21 = last['ema21']; ema50 = last['ema50']

        # --- Scoring (Simplified for Py) ---
        # We need `long_pct` and `short_pct` for the Perfect Time check.
        # Implemented faithful to Pine Script logic.
        
        # MACD
        macd_score_long = 100 if (macd_line > macd_sig and macd_hist > 0) else 70 if (macd_line > macd_sig) else 50 if (macd_hist > 0) else 20
        macd_score_short = 100 if (macd_line < macd_sig and macd_hist < 0) else 70 if (macd_line < macd_sig) else 50 if (macd_hist < 0) else 20
        
        # RSI
        rsi_score_long = 100 if (rsi < 30) else 85 if (rsi < 40) else 70 if (rsi < 50) else 50 if (rsi < 60) else 25
        rsi_score_short = 100 if (rsi > 70) else 85 if (rsi > 60) else 70 if (rsi > 50) else 50 if (rsi > 40) else 25
        
        # Stoch
        stoch_score_long = 100 if (stoch_k > stoch_d and stoch_k < 20) else 85 if (stoch_k > stoch_d and stoch_k < 50) else 65 if (stoch_k > stoch_d) else 25
        stoch_score_short = 100 if (stoch_k < stoch_d and stoch_k > 80) else 85 if (stoch_k < stoch_d and stoch_k > 50) else 65 if (stoch_k < stoch_d) else 25
        
        # Volume
        vol_score = 100 if (vol_ratio > 2.0) else 80 if (vol_ratio > 1.5) else 60 if (vol_ratio > 1.0) else 45 if (vol_ratio > 0.8) else 25
        
        # Delta
        delta_score_long = 100 if (vol_delta > 0 and delta_mom) else 75 if (vol_delta > 0) else 40 if (vol_delta > -abs(delta_ema)) else 20
        delta_score_short = 100 if (vol_delta < 0 and not delta_mom) else 75 if (vol_delta < 0) else 40 if (vol_delta < abs(delta_ema)) else 20
        
        # ADX
        adx_score = 100 if (adx > 35) else 85 if (adx > 30) else 70 if (adx > 25) else 50 if (adx > 20) else 30
        
        # Trend
        trend_score_long = 100 if (is_uptrend and ema8 > ema21 and ema21 > ema50) else 80 if (is_uptrend and ema8 > ema21) else 60 if (is_uptrend) else 0
        trend_score_short = 100 if (is_downtrend and ema8 < ema21 and ema21 < ema50) else 80 if (is_downtrend and ema8 < ema21) else 60 if (is_downtrend) else 0

        # Weighted Sum
        long_score_raw = (trend_score_long * 0.23) + (macd_score_long * 0.18) + (delta_score_long * 0.15) + (rsi_score_long * 0.12) + (stoch_score_long * 0.12) + (adx_score * 0.10) + (vol_score * 0.10)
        short_score_raw = (trend_score_short * 0.23) + (macd_score_short * 0.18) + (delta_score_short * 0.15) + (rsi_score_short * 0.12) + (stoch_score_short * 0.12) + (adx_score * 0.10) + (vol_score * 0.10)
        
        vol_multiplier = last['vol_multiplier']
        long_val = round(min(100, max(0, long_score_raw * vol_multiplier)))
        short_val = round(min(100, max(0, short_score_raw * vol_multiplier)))
        
        total_raw = long_val + short_val
        final_long_pct = round(long_val / total_raw * 100) if total_raw > 0 else 50
        final_short_pct = 100 - final_long_pct
        
        # --- Checks ---
        dynamic_threshold = 60 if adx > 30 else 65 if adx > 25 else 70 if adx > 20 else 75
        volume_ok = vol_ratio >= self.min_volume_ratio
        
        # Confluence Count
        conf_long = sum([
            is_uptrend, ema8 > ema21, macd_line > macd_sig, stoch_k > stoch_d,
            volume_ok, adx > self.adx_threshold, rsi > 50, delta_bullish
        ])
        
        conf_short = sum([
            is_downtrend, ema8 < ema21, macd_line < macd_sig, stoch_k < stoch_d,
            volume_ok, adx > self.adx_threshold, rsi < 50, delta_bearish
        ])
        
        # --- Perfect Time Logic ---
        long_perfect = (
            is_uptrend and 
            final_long_pct >= dynamic_threshold and 
            conf_long >= self.min_confluence and 
            volume_ok and 
            rsi > 50 and 
            delta_bullish
        )
        
        short_perfect = (
            is_downtrend and 
            final_short_pct >= dynamic_threshold and 
            conf_short >= self.min_confluence and 
            volume_ok and 
            rsi < 50 and 
            delta_bearish
        )
        
        # --- Decision ---
        # Filter by requested direction
        can_buy = direction_mode in ["AUTO", "BUY"]
        can_sell = direction_mode in ["AUTO", "SELL"]
        
        if long_perfect and can_buy:
            signal = "BUY"
            confidence = float(final_long_pct)
            reasons.append(f"⚡ PERFECT LONG (Score: {final_long_pct}%)")
            reasons.append(f"Confluence: {conf_long}/8")
            if delta_bullish: reasons.append("Delta Confirmed (+)")
            
        elif short_perfect and can_sell:
            signal = "SELL"
            confidence = float(final_short_pct)
            reasons.append(f"⚡ PERFECT SHORT (Score: {final_short_pct}%)")
            reasons.append(f"Confluence: {conf_short}/8")
            if delta_bearish: reasons.append("Delta Confirmed (-)")
        
        # Stop Loss & Take Profit Calculation (Risk Managed)
        close_price = last['close']
        atr_val = last['atr']
        
        # 2.0 ATR Stop Loss (Standard Safe)
        # 3.0 ATR Take Profit (1.5R)
        sl_pips = atr_val * 2.0 * vol_multiplier
        tp_pips = atr_val * 3.0 * vol_multiplier
        
        sl_price = close_price - sl_pips if signal == "BUY" else close_price + sl_pips
        tp_price = close_price + tp_pips if signal == "BUY" else close_price - tp_pips
        
        if signal == "NO_TRADE":
            sl_price = None
            tp_price = None

        return StrategyDecision(
            signal=signal,
            entry_price=close_price,
            sl=sl_price,
            tp=tp_price,
            reason=" | ".join(reasons),
            confidence=confidence
        )

# Instantiate the strategy for BotManager to use
predicta_strategy = PredictaStrategy()
