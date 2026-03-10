import pandas as pd
import pandas as pd
try:
    import app.analysis.indicators as ind
except ImportError:
    from app.utils.safe_ta import SafeTA as ta
import numpy as np
from datetime import datetime, time, timedelta
from typing import Dict, Any, Tuple, Optional, List
from dataclasses import dataclass
from enum import Enum

from app.strategy.antigravity import Signal, EntrySignal

class SessionType(Enum):
    ASIAN = "ASIAN"
    LONDON = "LONDON"
    NEW_YORK_AM = "NY_AM"
    NEW_YORK_PM = "NY_PM"
    OFF_HOURS = "OFF"

@dataclass
class MarketStructure:
    trend: str # BULLISH, BEARISH, RANGING
    sweep_type: Optional[str] = None # HIGH (Bearish Sweep), LOW (Bullish Sweep), NONE
    fvg_zone: Optional[Tuple[float, float]] = None # (Top, Bottom) of FVG
    swing_level: Optional[float] = None # The price level that was swept
    displacement_strength: float = 0.0 # ATR multiplier of the move

class InstitutionalScalpStrategy:
    """
    Antigravity Institutional Scalp (Hybrid SMC Sniper)
    
    Strategy: "Golden Hybrid"
    1. Trend Context (M5): Resample M1 data to M5 to determine main trend (EMA 50 > EMA 200).
    2. Execution (M1): Look for Liquidity Sweeps of recent Fractal Highs/Lows.
    3. Confirmation: M1 Candle must close back inside range (Sweep) + High Momentum Displacement.
    
    Target: XAUUSD (Gold), BTCUSD
    Account: Cent / Standard
    """
    
    def __init__(self):
        self.params = {
            "timeframe": "M1", 
            "lookback_candles": 50, # For Fractal Search
            "m5_resample": True,
            "risk_reward_ratio": 2.0, # Minimum R:R
            "min_displacement_atr": 1.0, # Move must be > 1.0x ATR to be considered displacement
            "max_spread_pips": 3.0, # Max allowed spread (visual check mostly)
            "session_filter": True
        }
        
    def analyze(self, df: pd.DataFrame, symbol: str = "XAUUSD") -> EntrySignal:
        if len(df) < 100:
            return self._no_signal("Insufficient Data (Need 100+ candles)")
            
        current = df.iloc[-1]
        
        # 1. Session Filter (Time check)
        # We prefer London & NY for Gold Volatility
        if self.params["session_filter"]:
            session = self._get_session(current.name)
            if session == SessionType.OFF_HOURS and "BTC" not in symbol:
                # Optional: Allow crypto to trade 24/7, but Gold only in killzones
                return self._no_signal("Outside Kill Zone")
        
        # 2. Volatility Check (ATR)
        # If ATR is too low, market is dead. If too high, risky.
        atr_14 = ta.atr(df['high'], df['low'], df['close'], length=14)
        current_atr = atr_14.iloc[-1]
        
        if current_atr <= 0:
             return self._no_signal("ATR Error")

        # 3. Hybrid Trend Context (M5 Simulation)
        # We resample M1 to M5 to check the bigger picture (Trend Filter)
        m5_trend = "NEUTRAL"
        if self.params["m5_resample"]:
            m5_df = self._resample_to_m5(df)
            if len(m5_df) > 50:
                 # EMA 20 on M5 (Short-term trend) vs EMA 50 on M5 (Medium-term)
                 m5_ema_20 = ta.ema(m5_df['close'], length=20).iloc[-1]
                 m5_ema_50 = ta.ema(m5_df['close'], length=50).iloc[-1]
                 
                 if m5_ema_20 > m5_ema_50:
                     m5_trend = "BULLISH"
                 elif m5_ema_20 < m5_ema_50:
                     m5_trend = "BEARISH"
        
        # 4. Market Structure Analysis (M1 Sweeps)
        structure = self._analyze_m1_structure(df, current_atr)
        
        # 5. Signal Logic (Confluence)
        # CASE A: BUY SIGNAL
        if structure.sweep_type == "LOW" and (m5_trend == "BULLISH" or m5_trend == "NEUTRAL"):
            # Logic: Uptrending M5, M1 just swept a low and reclaimed it.
            
            # Entry Price: Current Close (Market)
            # Stop Loss: Just below the Sweep Low (The wick)
            sl_price = structure.swing_level - (current_atr * 0.5) # Buffer
            risk_dist = current['close'] - sl_price
            
            if risk_dist <= 0: return self._no_signal("Invalid SL calculation")
            
            # Take Profit: 2x Risk
            tp_price = current['close'] + (risk_dist * self.params["risk_reward_ratio"])
            
            return EntrySignal(
                Signal.BUY,
                confidence=85,
                reasons=[
                    f"M5 Trend: {m5_trend}",
                    f"M1 Sweep: Bullish Liquidity Grab @ {structure.swing_level:.2f}",
                    f"Displacement: Strong Rejection"
                ],
                stop_loss=sl_price,
                take_profit=tp_price,
                entry_price=current['close'],
                entry_method="HYBRID_SMC_M1"
            )

        # CASE B: SELL SIGNAL
        elif structure.sweep_type == "HIGH" and (m5_trend == "BEARISH" or m5_trend == "NEUTRAL"):
            # Logic: Downtrending M5, M1 just swept a high and rejected.
            
            sl_price = structure.swing_level + (current_atr * 0.5) # Buffer
            risk_dist = sl_price - current['close']
            
            if risk_dist <= 0: return self._no_signal("Invalid SL calculation")
            
            tp_price = current['close'] - (risk_dist * self.params["risk_reward_ratio"])
            
            return EntrySignal(
                Signal.SELL,
                confidence=85,
                reasons=[
                    f"M5 Trend: {m5_trend}",
                    f"M1 Sweep: Bearish Liquidity Grab @ {structure.swing_level:.2f}",
                    f"Displacement: Strong Rejection"
                ],
                stop_loss=sl_price,
                take_profit=tp_price,
                entry_price=current['close'],
                entry_method="HYBRID_SMC_M1"
            )

        return self._no_signal("Scanning M1 Structure...")

    def _analyze_m1_structure(self, df: pd.DataFrame, current_atr: float) -> MarketStructure:
        """
        Detects M1 Liquidity Sweeps.
        Pattern:
        1. Swing Point formed in last 15 candles.
        2. Candle -2 or -1 wicks through it but closes back inside.
        """
        
        # Lookback window for swing points (e.g., last 20 candles, excluding last 3)
        window_size = 20
        recent_window = df.iloc[-(window_size+3):-3] 
        
        recent_low = recent_window['low'].min()
        recent_high = recent_window['high'].max()
        
        # Candles of interest: Sweep Candle (could be -2 or -1)
        c_prev = df.iloc[-2]
        c_curr = df.iloc[-1]
        
        sweep_type = None
        swing_level = None
        
        # Check BULLISH SWEEP (Low Grab)
        # Condition: A wick went below recent_low, but Latest price is ABOVE recent_low
        lowest_wick = min(c_prev['low'], c_curr['low'])
        curr_close = c_curr['close']
        
        if lowest_wick < recent_low and curr_close > recent_low:
             # Displacement check: Current candle should be Bullish
             if c_curr['close'] > c_curr['open']:
                 sweep_type = "LOW"
                 swing_level = lowest_wick # The absolute low of the sweep
        
        # Check BEARISH SWEEP (High Grab)
        highest_wick = max(c_prev['high'], c_curr['high'])
        if highest_wick > recent_high and curr_close < recent_high:
             # Displacement check: Current candle should be Bearish
             if c_curr['close'] < c_curr['open']:
                 sweep_type = "HIGH"
                 swing_level = highest_wick
                 
        return MarketStructure("NEUTRAL", sweep_type, None, swing_level)

    def _resample_to_m5(self, df: pd.DataFrame) -> pd.DataFrame:
        """Resample 1-minute data to 5-minute candles for context."""
        try:
            # Ensure index is datetime
            if not isinstance(df.index, pd.DatetimeIndex):
                 return pd.DataFrame()
                 
            m5_df = df.resample('5min').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()
            return m5_df
        except Exception as e:
            # Fallback if resampling fails
            return pd.DataFrame()

    def _no_signal(self, reason: str) -> EntrySignal:
        return EntrySignal(Signal.NONE, 0, "NONE", 0, 0, 0, [reason])

    def _get_session(self, dt: Any) -> SessionType:
        if isinstance(dt, (int, float, np.int64, np.float64)):
            try:
                dt = pd.to_datetime(dt, unit='s')
            except:
                return SessionType.OFF_HOURS
                
        h = dt.hour
        # UTC Times (Approximate)
        # London: 07:00 - 16:00 UTC
        # NY: 13:00 - 21:00 UTC
        if 7 <= h < 12: return SessionType.LONDON
        if 13 <= h < 21: return SessionType.NEW_YORK_AM # Covers Overlap + PM
        return SessionType.OFF_HOURS

institutional_scalp = InstitutionalScalpStrategy()
