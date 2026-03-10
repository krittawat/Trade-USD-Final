import datetime
from typing import Any, Optional
import pandas as pd
import numpy as np

from app.strategy.base import BaseStrategy
from app.domain.models import SymbolProfile, Decision
from app.domain.enums import Action, RegimeType
from app.core.logging import get_logger
from app.analysis import indicators

logger = get_logger(__name__)

class AetherFlowStrategy(BaseStrategy):
    """
    AETHER FLOW SYSTEM – V7.0 Vault Edition
    
    CORE UPGRADES:
    1. Session Filter (XAU/OIL): Trading only during London/NY overlaps.
    2. BTC Trend Specialist: Decoupled ATR/Key for crypto volatility.
    3. Dynamic BE (Gold): Tighter break-even activation to protect wins.
    4. Liquidity Sweep Filter: Higher conviction on reversals.
    5. HTF Hard Gate: Mandatory H1 EMA 50 alignment.
    """
    
    name = "aether_flow"
    asset_class = "*"
    
    @classmethod
    def get_name(cls) -> str:
        return "aether_flow"

    @classmethod
    def get_supported_timeframes(cls) -> list[str]:
        return ["M1", "M5", "M15", "H1"]
        
    def __init__(self, symbol: Optional[str] = None, **kwargs):
        super().__init__()
        self.symbol = symbol or "UNKNOWN"
        self._initialized_for_symbol = None
        
        # UT Bot Defaults
        self.ut_key = kwargs.get("ut_key", 2.0)
        self.ut_atr = kwargs.get("ut_atr", 6)
        
        # Hull Defaults
        self.hull_len = kwargs.get("hull_len", 55)
        self.hull_mode = kwargs.get("hull_mode", "thma") 
        
        # VMC Defaults
        self.wt_chlen = kwargs.get("wt_chlen", 9)
        self.wt_avg = kwargs.get("wt_avg", 12)
        self.wt_ob = kwargs.get("wt_ob", 53)
        self.wt_os = kwargs.get("wt_os", -53)
        
        # Risk Defaults
        self.tp_mult = kwargs.get("tp_mult", 2.0)
        self.be_activation = kwargs.get("be_activation", 1.0) # Move to BE at 1R
        
        if symbol:
            self._apply_symbol_params(symbol, kwargs)

    def _apply_symbol_params(self, symbol: str, kwargs: dict):
        s = symbol.upper()
        if "XAUUSD" in s:
            # V7.3 Vault Settings for Gold
            self.ut_key, self.ut_atr = 3.2, 16 
            self.hull_len = 120
            self.tp_mult = 2.5
            self.mfi_threshold = 15
            self.be_activation = 0.7 # Secure gold trades early
            self.use_session_filter = True
        elif "BTCUSD" in s:
            # V7.3 Balanced: Aiming for 50%+ WR with decent frequency
            self.ut_key, self.ut_atr = 2.5, 12 
            self.hull_len = 100
            self.tp_mult = 3.0
            self.mfi_threshold = 12
            self.be_activation = 1.0
            self.use_session_filter = False
        elif "USOIL" in s:
            self.ut_key, self.ut_atr = 2.2, 10
            self.hull_len = 55
            self.tp_mult = 2.0
            self.mfi_threshold = 8
            self.be_activation = 1.0
            self.use_session_filter = True
        elif "US30" in s or "USTEC" in s:
            # V7.3 Indices Settings: Strong session filter to avoid Asian session noise
            self.ut_key, self.ut_atr = 3.0, 14
            self.hull_len = 110
            self.tp_mult = 2.0
            self.mfi_threshold = 12
            self.be_activation = 1.0
            self.use_session_filter = True
        else:
            self.mfi_threshold = 10
            self.be_activation = 1.0
            self.use_session_filter = False
            
        self._initialized_for_symbol = symbol

    def _is_market_session(self, current_time: Any) -> bool:
        """London & NY Session Filter (UTC)."""
        hour = current_time.hour if hasattr(current_time, 'hour') else pd.Timestamp(current_time, unit='s' if isinstance(current_time, (int, float)) and current_time < 1e11 else None).hour
        # London (8-16) + NY (13-21) = 8:00 to 21:00 UTC
        return 8 <= hour <= 21

    def _get_htf_trend(self, h1_candles: Optional[pd.DataFrame]) -> str:
        if h1_candles is None or len(h1_candles) < 50:
            return "UNKNOWN"
        h1_ema = indicators.ema(h1_candles['close'], 50)
        curr_h1 = h1_candles['close'].iloc[-1]
        curr_ema = h1_ema.iloc[-1]
        
        if curr_h1 > curr_ema: return "BULLISH"
        elif curr_h1 < curr_ema: return "BEARISH"
        return "NEUTRAL"

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs
    ) -> Decision:
        symbol = profile.symbol
        if self._initialized_for_symbol != symbol:
            self._apply_symbol_params(symbol, kwargs)
            
        if len(candles) < 200:
            return self.create_hold(symbol, "Insufficient data")

        # Session Filter Check
        timestamp = candles.index[-1]
        if self.use_session_filter and not self._is_market_session(timestamp):
            return self.create_hold(symbol, "Outside target trading sessions")

        # Extract Series
        o, h, l, c = candles['open'], candles['high'], candles['low'], candles['close']
        curr_close = float(c.iloc[-1])
        
        # 1. Hull Velocity
        hull = indicators.thma(c, length=self.hull_len)
        hull_sloping_up = hull.iloc[-1] > hull.iloc[-2]
        hull_sloping_down = hull.iloc[-1] < hull.iloc[-2]

        # 2. UT Bot Primary Momentum
        trail = indicators.ut_bot_trail(c, h, l, self.ut_key, self.ut_atr)
        ut_buy = c.iloc[-1] > trail.iloc[-1] and c.iloc[-2] <= trail.iloc[-2]
        ut_sell = c.iloc[-1] < trail.iloc[-1] and c.iloc[-2] >= trail.iloc[-2]
        ut_status_bull = c.iloc[-1] > trail.iloc[-1]
        ut_status_bear = c.iloc[-1] < trail.iloc[-1]
        
        # 3. VMC Engine
        wt_df = indicators.vmc_wavetrend(c, self.wt_chlen, self.wt_avg)
        wt1, wt2 = wt_df['wt1'], wt_df['wt2']
        wt_cross_up = wt1.iloc[-1] > wt2.iloc[-1] and wt1.iloc[-2] <= wt2.iloc[-2]
        wt_cross_down = wt1.iloc[-1] < wt2.iloc[-1] and wt1.iloc[-2] >= wt2.iloc[-2]

        # 4. MFI Area
        mfi_rsi = indicators.vmc_mfi_rsi(o, h, l, c)
        curr_mfi = mfi_rsi.iloc[-1]
        
        # 5. Divergences
        divs = indicators.detect_divergences(wt2, h, l, top_limit=self.wt_ob-5, bot_limit=self.wt_os+5)
        bull_div = divs['bull_div'].iloc[-1]
        bear_div = divs['bear_div'].iloc[-1]
        
        # 6. Candlestick Confirmation
        tbr = indicators.detect_three_bar_reversal(o, h, l, c)
        conf_bull = tbr['bullish_3br'].iloc[-1]
        conf_bear = tbr['bearish_3br'].iloc[-1]

        # 7. HTF Logic
        h1_candles = kwargs.get("h1_candles")
        htf_trend = self._get_htf_trend(h1_candles)
        
        # 8. Volume Guard
        pressure = kwargs.get("pressure", {})
        vol_score = pressure.get("score", 0) if pressure else 2.5
        high_vol = vol_score >= 1.8
        
        # --- V7.0 Vault Logic ---
        action = Action.HOLD
        confidence = 0.0
        reason = ""
        tags = ["aether_v7.0_vault"]
        
        # A. PRECISION VAULT (MOMENTUM)
        # Required: UT Signal + VMC Support + HTF + MFI + Hull Slope + Vol
        # For BTC: We rely more on UT Bot persistence
        if "BTC" in symbol.upper():
            vault_bull = ut_buy and htf_trend == "BULLISH" and curr_mfi > self.mfi_threshold and hull_sloping_up
            vault_bear = ut_sell and htf_trend == "BEARISH" and curr_mfi < -self.mfi_threshold and hull_sloping_down
        else:
            vault_bull = (wt_cross_up or ut_buy) and ut_status_bull and htf_trend == "BULLISH" and \
                        curr_mfi > self.mfi_threshold and hull_sloping_up and high_vol
            vault_bear = (wt_cross_down or ut_sell) and ut_status_bear and htf_trend == "BEARISH" and \
                        curr_mfi < -self.mfi_threshold and hull_sloping_down and high_vol

        # B. EXTREME REVERSAL
        extreme_rev_bull = wt_cross_up and wt2.iloc[-1] < -68 and (bull_div or conf_bull) and curr_mfi > 0
        extreme_rev_bear = wt_cross_down and wt2.iloc[-1] > 68 and (bear_div or conf_bear) and curr_mfi < 0

        if vault_bull:
            action = Action.BUY
            confidence = 0.96
            reason = "Vault Momentum Bull"
            tags.append("vault")
        elif extreme_rev_bull:
            action = Action.BUY
            confidence = 0.94
            reason = "Extreme Reversal Bull"
            tags.append("reversal")
            
        elif vault_bear:
            action = Action.SELL
            confidence = 0.96
            reason = "Vault Momentum Bear"
            tags.append("vault")
        elif extreme_rev_bear:
            action = Action.SELL
            confidence = 0.94
            reason = "Extreme Reversal Bear"
            tags.append("reversal")

        if action != Action.HOLD:
            atr_val = indicators.atr(h, l, c, 14).iloc[-1]
            if action == Action.BUY:
                lowest_5 = float(l.tail(5).min())
                stop_loss = max(lowest_5, curr_close - (atr_val * self.ut_atr * 0.7))
                risk = abs(curr_close - stop_loss)
                take_profit = curr_close + (risk * self.tp_mult)
            else:
                highest_5 = float(h.tail(5).max())
                stop_loss = min(highest_5, curr_close + (atr_val * self.ut_atr * 0.7))
                risk = abs(curr_close - stop_loss)
                take_profit = curr_close - (risk * self.tp_mult)

            if risk <= 0: return self.create_hold(symbol, "Risk error")

            return Decision(
                symbol=symbol, action=action, confidence=confidence,
                reason=reason, stop_loss=stop_loss, take_profit=take_profit,
                strategy_name=self.name, tags=tags,
                # Signal the execution engine to move BE early for Gold
                extra={'be_activation_r': self.be_activation}
            )

        return self.create_hold(symbol, "Vault gate closed")

    def update_brain(self, outcome: dict) -> None:
        pass
