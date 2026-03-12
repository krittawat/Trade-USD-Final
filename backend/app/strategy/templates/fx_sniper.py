"""
Fx Sniper Strategy — High Precision Forex & Opportunistic Gold.

Logic:
    1. Forex Core (EURUSD, GBPUSD, etc.):
        - M15 Trend: EMA 20 > 50 > 200 (Uptrend) or < (Downtrend).
        - M5 Entry: Price pullback to EMA 20/50 zone.
        - Momentum: RSI(14) confirmation (Bullish: bounce from 40-50 / Bearish: reject 50-60).
    
    2. Gold Opportunistic (XAUUSD):
        - STRICT Regime Check: H1 ADX > 30 (Strong Trend ONLY).
        - Block if M15 range < 20 pips (Avoid Chop).
        - Risk Reduced by 50% (handled in Sizing).

Timeframe:
    - Analysis: M15 (Trend), M5 (Entry)
    - Execution: M5
"""

import pandas as pd
import pandas_ta as ta
import app.analysis.indicators as ind
from typing import Optional

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.smart_base import SmartStrategy

logger = get_logger(__name__)

# --- Parameters ---
EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200

RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# Gold Specifics
GOLD_MIN_ADX = 30.0          # Only trade Gold if ADX > 30
GOLD_MIN_RANGE_PIPS = 20.0   # Avoid chop

class FxSniperStrategy(SmartStrategy):
    """
    Sniper Mode Strategy for Forex (Core) & Gold (Opportunistic).
    """
    name = "fx_sniper"
    timeframe = "M5"  # Execution timeframe
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
    ]

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        """
        Analyze logic for Forex Sniper & Gold Opportunistic.
        """
        symbol = profile.symbol
        is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()

        # --- Data Validation ---
        # Need enough data for M15 simulation (approx 3x M5 bars)
        # EMA 200 on M15 roughly requires 600 M5 bars
        if len(candles) < 600:
            return self.create_hold(symbol, "Insufficient data for M15 EMAs")

        # --- 1. Resample to M15 for Trend Analysis ---
        # Simple resampling for checking trend
        candles_m15 = self._resample_candles(candles, "15min")
        
        if len(candles_m15) < 205:
             return self.create_hold(symbol, "Insufficient M15 data")

        # M15 Indicators
        ema20_m15 = ta.ema(candles_m15["close"], EMA_FAST)
        ema50_m15 = ta.ema(candles_m15["close"], EMA_MID)
        ema200_m15 = ta.ema(candles_m15["close"], EMA_SLOW)
        
        # Current M15 Trend
        c_m15 = candles_m15.iloc[-1]
        e20_val = ema20_m15.iloc[-1]
        e50_val = ema50_m15.iloc[-1]
        e200_val = ema200_m15.iloc[-1]

        trend_up = e20_val > e50_val > e200_val
        trend_down = e20_val < e50_val < e200_val

        if not (trend_up or trend_down):
            return self.create_hold(symbol, "M15 Trend Unclear (EMAs not aligned)")

        # --- 2. Gold Specific Regime Filter ---
        if is_gold:
            # H1 ADX Check (Relaxed for "Every Condition" request)
            # Was 30, now 20 to allow more trades
            GOLD_ADX_THRESHOLD = 20.0 
            
            candles_h1 = self._resample_candles(candles, "1H")
            if len(candles_h1) > 20:
                adx_h1 = ta.adx(candles_h1["high"], candles_h1["low"], candles_h1["close"], 14)
                if adx_h1 is not None and not adx_h1.empty:
                    current_adx = adx_h1["ADX_14"].iloc[-1]
                    # Only block if EXTREMELY low (dead market)
                    if current_adx < 10.0:
                         return self.create_hold(symbol, f"Gold ADX Dead ({current_adx:.1f} < 10)")
        
        # --- 3. M5 Entry Logic ---
        # Indicators M5
        ema20_m5 = ta.ema(candles["close"], EMA_FAST)
        ema50_m5 = ta.ema(candles["close"], EMA_MID)
        rsi_m5 = ta.rsi(candles["close"], RSI_PERIOD)
        atr_m5 = ta.atr(candles["high"], candles["low"], candles["close"], RSI_PERIOD)
        
        # Bollinger Bands for Range Trading
        bbands = ta.bbands(candles["close"], length=20, std=2.0)
        
        current_close = candles["close"].iloc[-1]
        cur_rsi = rsi_m5.iloc[-1]
        cur_atr = atr_m5.iloc[-1]
        
        # M5 EMAs
        e20_m5_val = ema20_m5.iloc[-1]
        e50_m5_val = ema50_m5.iloc[-1]

        action = Action.HOLD
        confidence = 0.0
        reason = ""
        stop_loss = 0.0
        take_profit = 0.0
        
        # ATR Multipliers
        sl_mult = 1.5 if is_gold else 1.0
        tp_mult = 1.5 

        # ---------------------------------------------------------
        # MODE A: TREND FOLLOWING (Primary Sniper)
        # ---------------------------------------------------------
        if trend_up:
            # ENTRY: Price dips into/near EMA 20-50 zone OR RSI bounces from 40-50
            ema200_m5 = ta.ema(candles["close"], EMA_SLOW)
            e200_m5_val = ema200_m5.iloc[-1] if ema200_m5 is not None else 0

            if current_close > e200_m5_val:
                # RSI Hook
                prev_rsi = rsi_m5.iloc[-2]
                rsi_hook_up = prev_rsi < cur_rsi and cur_rsi > 50

                # Pullback to value
                dist_to_ema20 = abs(current_close - e20_m5_val)
                near_ema = dist_to_ema20 < (cur_atr * 0.5)

                if rsi_hook_up and (near_ema or current_close > e20_m5_val):
                    action = Action.BUY
                    confidence = 0.8
                    reason = f"M15 Uptrend + M5 Pullback (RSI {cur_rsi:.1f})"
                    
                    sl_dist = cur_atr * sl_mult
                    stop_loss = current_close - sl_dist
                    take_profit = current_close + (sl_dist * tp_mult)

        elif trend_down:
            # ENTRY: Price ralllies to EMA 20-50 zone OR RSI rejects 50-60
            ema200_m5 = ta.ema(candles["close"], EMA_SLOW)
            e200_m5_val = ema200_m5.iloc[-1] if ema200_m5 is not None else 999999

            if current_close < e200_m5_val:
                # RSI Hook Down
                prev_rsi = rsi_m5.iloc[-2]
                rsi_hook_down = prev_rsi > cur_rsi and cur_rsi < 50

                # Pullback to value
                dist_to_ema20 = abs(current_close - e20_m5_val)
                near_ema = dist_to_ema20 < (cur_atr * 0.5)

                if rsi_hook_down and (near_ema or current_close < e20_m5_val):
                    action = Action.SELL
                    confidence = 0.8
                    reason = f"M15 Downtrend + M5 Pullback (RSI {cur_rsi:.1f})"

                    sl_dist = cur_atr * sl_mult
                    stop_loss = current_close + sl_dist
                    take_profit = current_close - (sl_dist * tp_mult) # Sell TP is below

        # ---------------------------------------------------------
        # MODE B: RANGE / MEAN REVERSION (New: "Every Condition")
        # ---------------------------------------------------------
        else:
            # M15 Trend is Unclear/Sideways -> Use Bollinger Bands
            # Logic: Price touches Band + RSI Extreme -> Revert to Mean
            
            # Robust access via iloc (BBL, BBM, BBU, BBB, BBP)
            # 0=Lower, 1=Mid, 2=Upper
            lower_band = bbands.iloc[-1, 0]  # Current Lower
            upper_band = bbands.iloc[-1, 2]  # Current Upper
            mid_band = bbands.iloc[-1, 1]    # Current Mid (SMA)
            
            # Check range width to avoid super tight chop
            bb_width = (upper_band - lower_band) / profile.point if profile.point > 0 else 0
            min_width = 150 # 15 pips (approx)
            
            if bb_width > min_width:
                # BUY: Price touched Lower + RSI < 35
                if current_close <= lower_band * 1.0005 and cur_rsi < 35:
                    action = Action.BUY
                    confidence = 0.6  # Lower confidence for range
                    reason = "M15 Range + BB Lower Bounce"
                    stop_loss = current_close - (cur_atr * 1.5)
                    take_profit = mid_band # Target Middle Band
                    
                # SELL: Price touched Upper + RSI > 65
                elif current_close >= upper_band * 0.9995 and cur_rsi > 65:
                    action = Action.SELL
                    confidence = 0.6
                    reason = "M15 Range + BB Upper Rejection"
                    stop_loss = current_close + (cur_atr * 1.5)
                    take_profit = mid_band # Target Middle Band

        # --- 4. Final Filter: Spread & Session ---
        # (Handled by Gate, but strategy can reduce confidence if unsure)
        
        if action != Action.HOLD:
            return Decision(
                symbol=symbol,
                action=action,
                confidence=confidence,
                reason=reason,
                stop_loss=round(stop_loss, profile.digits),
                take_profit=round(take_profit, profile.digits),
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["sniper", "trend_pullback" if confidence > 0.7 else "range_reversal", "gold_opp" if is_gold else "fx_core"]
            )

        return self.create_hold(symbol, "No Setup (Trend or Range)")

    def _resample_candles(self, m5_candles: pd.DataFrame, timeframe_str: str) -> pd.DataFrame:
        """Helper to resample M5 candles to higher TFs."""
        # Ensure index is datetime
        df = m5_candles.copy()
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], unit='s') if df["time"].dtype == 'int64' else pd.to_datetime(df["time"])
            df.set_index("time", inplace=True)
        
        # Resample logic
        # OHLCV aggregation
        # Resample logic
        # OHLCV aggregation
        agg_dict = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
        }
        
        if 'volume' in df.columns:
            agg_dict['volume'] = 'sum'
            
        if 'tick_volume' in df.columns:
            agg_dict['tick_volume'] = 'sum'
            
        resampled = df.resample(timeframe_str).agg(agg_dict).dropna()
        return resampled
