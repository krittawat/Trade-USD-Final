import numpy as np
import pandas as pd
from typing import Optional

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

try:
    import talib
except ImportError:
    talib = None

logger = get_logger(__name__)

class GGAdaptiveWeaponry(BaseStrategy):
    """
    Market-Adaptive Trading System (GG Antigravity Edition)
    
    Phase 1: Market Regime Detection (Self-Diagnosis)
      1. Strong Trend: ADX > 25 & Prices above/below EMA 200.
      2. Volatile Range: High ATR & ADX < 20.
      3. Compression/Squeeze: Low ATR & Bollinger Bands Squeezing.
      4. Structural Shift: Detect CHoCH / MSS.
      
    Phase 2: Weaponry Assignment
      - Weapon A (Trend Follower): CDC ActionZone (EMA 12/26) + Trailing Stop (ATR 2.0).
      - Weapon B (Mean Reversion): RSI (80/20) + Bollinger Bands Rebound.
      - Weapon C (Sniper): Order Block (OB) + Fair Value Gap (FVG) Entry.
    """

    name = "gg_adaptive_weaponry"
    asset_class = "*"
    timeframes = ["M5", "M15", "H1"]
    suitable_regimes = ["ALL"]

    def __init__(self):
        super().__init__()
        # Trend Parameters (CDC Action Zone)
        self.ema_fast = 12
        self.ema_slow = 26
        self.ema_trend = 200
        self.adx_period = 14
        
        # Range Parameters
        self.rsi_period = 14
        self.bb_period = 20
        self.bb_std = 2.0
        
        # Risk Multipliers
        self.atr_period = 14
        self.sl_atr_mult_trend = 1.5
        self.sl_atr_mult_range = 1.0
        self.sl_atr_mult_sniper = 0.8
        
        self.tp_mult_trend = 2.5
        self.tp_mult_range = 1.5
        self.tp_mult_sniper = 3.0

    def get_version(self) -> str:
        return "1.0.0"

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        """Core AI analysis logic executing defined weapons."""
        if len(candles) < max(self.ema_trend, 60):
            return Decision(
                symbol=profile.symbol, action=Action.HOLD,
                confidence=0.0, reason="Insufficient candle data",
                strategy_name=self.name
            )

        # ─── Data Prep ───
        close = candles['close'].values
        high = candles['high'].values
        low = candles['low'].values
        open_ = candles['open'].values

        if talib is None:
            return Decision(
                symbol=profile.symbol, action=Action.HOLD, confidence=0.0,
                reason="TA-Lib required for accurate weaponry", strategy_name=self.name
            )

        # Indicator Calculation
        adx = talib.ADX(high, low, close, timeperiod=self.adx_period)
        ema_fast = talib.EMA(close, timeperiod=self.ema_fast)
        ema_slow = talib.EMA(close, timeperiod=self.ema_slow)
        ema200 = talib.EMA(close, timeperiod=self.ema_trend)
        atr = talib.ATR(high, low, close, timeperiod=self.atr_period)
        rsi = talib.RSI(close, timeperiod=self.rsi_period)
        bb_upper, bb_mid, bb_lower = talib.BBANDS(close, timeperiod=self.bb_period, nbdevup=self.bb_std, nbdevdn=self.bb_std)

        # Safety Check for NaNs
        if np.isnan(ema200[-1]) or np.isnan(adx[-1]) or np.isnan(atr[-1]):
            return Decision(
                symbol=profile.symbol, action=Action.HOLD, confidence=0.0,
                reason="Indicators warming up", strategy_name=self.name
            )

        current_close = close[-1]
        current_adx = adx[-1]
        current_atr = atr[-1]
        current_rsi = rsi[-1]
        
        # ─── PHASE 1: Market Intelligence (State Diagnosis) ───
        
        # Calculate ATR percentile (Lookback 100)
        atr_lookback = atr[~np.isnan(atr)][-100:]
        if len(atr_lookback) > 10:
            atr_pct = np.percentile(atr_lookback, [20, 80])
            atr_low_thresh, atr_high_thresh = atr_pct[0], atr_pct[1]
        else:
            atr_low_thresh, atr_high_thresh = current_atr, current_atr

        # Calculate BB Squeeze
        bb_width = bb_upper - bb_lower
        bbw_lookback = bb_width[~np.isnan(bb_width)][-100:]
        bbw_low_thresh = np.percentile(bbw_lookback, 20) if len(bbw_lookback) > 10 else bb_width[-1]

        # SMC Structure Detection (Local Pivots for CHoCH/MSS)
        struct_shift, struct_dir = self._detect_structure_shift(high, low, close)

        # State Classification
        state = "UNKNOWN"
        if struct_shift:
            state = "STRUCTURAL_SHIFT"
        elif current_adx > 25 and (current_close > ema200[-1] or current_close < ema200[-1]):
            state = "STRONG_TREND"
        elif current_adx < 20 and current_atr > atr_high_thresh:
            state = "VOLATILE_RANGE"
        elif current_atr < atr_low_thresh and bb_width[-1] < bbw_low_thresh:
            state = "COMPRESSION"
            
        logger.debug(f"[{profile.symbol}] Market State: {state}")

        # ─── PHASE 2: Weaponry Assignment ───
        
        decision = Decision(symbol=profile.symbol, action=Action.HOLD, confidence=0.0, reason=f"State: {state} | No trigger", strategy_name=self.name)

        if state == "STRONG_TREND":
            decision = self._trend_weapon(profile, close, high, low, ema_fast, ema_slow, ema200, current_atr, state)
            
        elif state == "VOLATILE_RANGE":
            decision = self._range_weapon(profile, close, high, low, current_rsi, bb_upper, bb_lower, current_atr, state)
            
        elif state == "STRUCTURAL_SHIFT" or state == "COMPRESSION":
            # For Shift and Compression, Sniper weapon hunts for precise entries (FVG/OB)
            decision = self._sniper_weapon(profile, close, high, low, open_, current_atr, struct_dir, state)

        # Default fallback to HOLD if weapon misfired or returned None
        if decision is None:
            return Decision(symbol=profile.symbol, action=Action.HOLD, confidence=0.0, reason="Weapon failed to fire", strategy_name=self.name)
            
        return decision

    def _trend_weapon(self, profile, close, high, low, ema_fast, ema_slow, ema200, atr, state) -> Decision:
        """Weapon A: CDC Action Zone (EMA 12/26 Cross) + ATR 2.0 Trailing Stop"""
        c = close[-1]
        c_prev = close[-2]
        e200_0 = ema200[-1]
        
        action = Action.HOLD
        conf = 0.0
        reason = ""
        
        # CDC Action Zone Logic
        bull = ema_fast[-1] > ema_slow[-1]
        bear = ema_fast[-1] < ema_slow[-1]
        
        green = bull and c > ema_fast[-1]
        green_prev = (ema_fast[-2] > ema_slow[-2]) and c_prev > ema_fast[-2]
        
        red = bear and c < ema_fast[-1]
        red_prev = (ema_fast[-2] < ema_slow[-2]) and c_prev < ema_fast[-2]
        
        buycond = green and not green_prev
        sellcond = red and not red_prev
        
        if buycond and c > e200_0:
            action = Action.BUY
            conf = 0.85
            reason = f"Weapon A (BUY): CDC First Green + Price > EMA200 [{state}]"
        elif sellcond and c < e200_0:
            action = Action.SELL
            conf = 0.85
            reason = f"Weapon A (SELL): CDC First Red + Price < EMA200 [{state}]"
            
        if action != Action.HOLD:
            sl_dist = atr * 2.0  # ATR 2.0 Trailing proxy
            tp_dist = atr * self.tp_mult_trend
            sl = c - sl_dist if action == Action.BUY else c + sl_dist
            tp = c + tp_dist if action == Action.BUY else c - tp_dist
            return Decision(
                symbol=profile.symbol, action=action, confidence=conf,
                reason=reason, stop_loss=round(sl, 5), take_profit=round(tp, 5),
                strategy_name=self.name, tags=["trend", "cdc_actionzone", "adx_high"]
            )
            
        return Decision(symbol=profile.symbol, action=Action.HOLD, confidence=0.0, reason=f"Weapon A (CDC) hunting First Green/Red [{state}]", strategy_name=self.name)

    def _range_weapon(self, profile, close, high, low, rsi, bb_upper, bb_lower, atr, state) -> Decision:
        """Weapon: RSI 80/20 + Bollinger Bands Rebound"""
        c = close[-1]
        bbu = bb_upper[-1]
        bbl = bb_lower[-1]
        
        action = Action.HOLD
        conf = 0.0
        reason = ""
        
        if rsi < 20 and c <= bbl:
            action = Action.BUY
            conf = 0.75
            reason = f"Weapon B (BUY): RSI 20 ({rsi:.1f}) at Lower BB [{state}]"
        elif rsi > 80 and c >= bbu:
            action = Action.SELL
            conf = 0.75
            reason = f"Weapon B (SELL): RSI 80 ({rsi:.1f}) at Upper BB [{state}]"

        if action != Action.HOLD:
            sl_dist = atr * self.sl_atr_mult_range
            tp_dist = atr * self.tp_mult_range
            sl = c - sl_dist if action == Action.BUY else c + sl_dist
            tp = c + tp_dist if action == Action.BUY else c - tp_dist
            return Decision(
                symbol=profile.symbol, action=action, confidence=conf,
                reason=reason, stop_loss=round(sl, 5), take_profit=round(tp, 5),
                strategy_name=self.name, tags=["range", "mean_reversion"]
            )
            
        return Decision(symbol=profile.symbol, action=Action.HOLD, confidence=0.0, reason=f"Range Weapon active but RSI safe ({rsi:.1f}) [{state}]", strategy_name=self.name)

    def _sniper_weapon(self, profile, close, high, low, open_, atr, struct_dir, state) -> Decision:
        """Weapon: Smart Money Concepts (FVG + Fib 0.618 logic derived)"""
        action = Action.HOLD
        conf = 0.0
        reason = ""
        c = close[-1]
        
        # Simple FVG check for the previous 3 candles
        # Bullish FVG: Low of candle 1 > High of candle 3
        # Bearish FVG: High of candle 1 < Low of candle 3
        if len(close) > 5:
            l1, l3 = low[-1], low[-3]
            h1, h3 = high[-1], high[-3]
            
            bullish_fvg = l1 > h3
            bearish_fvg = h1 < l3
            
            if struct_dir == 1 and bullish_fvg:
                action = Action.BUY
                conf = 0.85
                reason = f"Sniper Weapon (BUY): Structural Shift UP + FVG Found [{state}]"
            elif struct_dir == -1 and bearish_fvg:
                action = Action.SELL
                conf = 0.85
                reason = f"Sniper Weapon (SELL): Structural Shift DOWN + FVG Found [{state}]"

        if action != Action.HOLD:
            sl_dist = atr * self.sl_atr_mult_sniper
            # Tight SL, High TP
            tp_dist = atr * self.tp_mult_sniper
            sl = c - sl_dist if action == Action.BUY else c + sl_dist
            tp = c + tp_dist if action == Action.BUY else c - tp_dist
            return Decision(
                symbol=profile.symbol, action=action, confidence=conf,
                reason=reason, stop_loss=round(sl, 5), take_profit=round(tp, 5),
                strategy_name=self.name, tags=["sniper", "smc", "fvg"]
            )
            
        return Decision(symbol=profile.symbol, action=Action.HOLD, confidence=0.0, reason=f"Sniper Weapon hunting... [{state}]", strategy_name=self.name)

    def _detect_structure_shift(self, high, low, close) -> tuple[bool, int]:
        """
        Detect Market Structure Shift (MSS/CHoCH) using basic 5-period local pivots.
        Returns: (Shift_Detected: bool, Direction: 1 for UP, -1 for DOWN)
        """
        if len(high) < 20:
            return False, 0
            
        recent_highs = high[-10:]
        recent_lows = low[-10:]
        recent_closes = close[-5:]
        
        # very rudimentary CHoCH: price forcefully closes above a recent swing high or below swing low
        swing_high = np.max(recent_highs[:-1])
        swing_low = np.min(recent_lows[:-1])
        
        current_close = close[-1]
        
        if current_close > swing_high:
            # Shift UP (Bullish CHoCH)
            return True, 1
        elif current_close < swing_low:
            # Shift DOWN (Bearish CHoCH)
            return True, -1
            
        return False, 0
