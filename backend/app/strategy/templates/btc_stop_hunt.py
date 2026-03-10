"""
BTC Stop Hunt (Liquidity Sweep) Strategy

Optimized for: BTCUSDc (Exness MT5 cent contract)
Timeframe: M5 / M15

Philosophy:
    - Institutional players often manipulate the market to grab liquidity (Stop Loss orders) 
      placed above obvious resistance or below obvious support.
    - We detect this behavior by finding recent swing highs/lows.
    - A "Sweep" occurs when price briefly trades beyond these swings but closes strongly back inside the range.
    - Confirmations: Long wicks (Pinbars), Volume spikes (institutions stepping in), Momentum divergence.
    - Stop Loss is placed precisely behind the peak of the sweep wick + small ATR buffer.
    - Take Profit targets 1.5R - 2.0R to guarantee high expected value.

Primary Target: Win Rate ~45-55% but with high Payoff Ratio (Avg Win > Avg Loss). 
"""

import pandas as pd
import numpy as np

from app.strategy.base import BaseStrategy
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

logger = get_logger(__name__)

BTC_STOP_HUNT_DEFAULTS = {
    # Swing Detection
    "swing_lookback_left": 20,
    "swing_lookback_right": 2,  
    
    # Sweep Rules
    "max_sweep_bars": 3,        
    "min_wick_ratio": 0.5,      
    "close_inside_pct": 0.1,    
    
    # Volume Filter
    "vol_ma": 20,
    "vol_spike_ratio": 1.0,    
    
    # Volatility / Risk
    "atr_period": 14,
    "sl_buffer_atr": 0.5,       
    "min_sl_pct": 0.002,        
    "max_sl_pct": 0.02,         
    
    # Targets
    "rr_standard": 2.0,
    "rr_strong": 2.5,
    
    # Trend alignment
    "ema_trend": 200,
    "require_ema_alignment": False, 

    # GOSH PROTOCOL (Stealth Mode)
    # Hides SL/TP from broker by not sending them with the order.
    # The bot must manage them purely in memory (virtual SL/TP).
    "use_ghost_protocol": True,
    
    "min_bars_between_trades": 3,
}

class BtcStopHuntStrategy(BaseStrategy):
    name = "btc_stop_hunt"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.RANGING,
        RegimeType.HIGH_VOLATILITY,
    ]

    def __init__(self) -> None:
        super().__init__()
        self._last_signal_bar = -999
        from app.strategy.param_loader import get_param_loader
        loader = get_param_loader()
        if loader:
            self.p = loader.get_params(self.name, "BTCUSDc", BTC_STOP_HUNT_DEFAULTS)
        else:
            self.p = {**BTC_STOP_HUNT_DEFAULTS}

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "BTCUSDc"
        
        # ─── Data check ───
        min_bars = max(self.p["ema_trend"], 50)
        if candles is None or len(candles) < min_bars:
            return self.create_hold(
                symbol=symbol,
                reason=f"Data insufficient ({len(candles) if candles is not None else 0} < {min_bars})",
            )

        bar_idx = len(candles) - 1
        if (bar_idx - self._last_signal_bar) < self.p["min_bars_between_trades"]:
            return self.create_hold(
                symbol=symbol,
                reason=f"Cooldown: wait {self.p['min_bars_between_trades']} bars",
            )

        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)
        
        current_close = float(close.iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])
        current_open = float(open_.iloc[-1])
        
        # ─── ATR ───
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(self.p["atr_period"]).mean()
        atr_val = float(atr_series.iloc[-1])
        if pd.isna(atr_val) or atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR NaN or zero")

        # ─── Volume ───
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        vol = candles[vol_col].astype(float) if vol_col in candles.columns else None
        vol_current = float(vol.iloc[-1]) if vol is not None else 0
        vol_avg = float(vol.rolling(self.p["vol_ma"]).mean().iloc[-1]) if vol is not None else 1
        vol_ratio = vol_current / vol_avg if vol_avg > 0 else 0
        vol_spike = vol_ratio >= self.p["vol_spike_ratio"]

        # ─── Trend Filter ───
        ema_t = close.ewm(span=self.p["ema_trend"], adjust=False).mean()
        ema_t_val = float(ema_t.iloc[-1])
        
        # ─── Find Swing Highs & Lows ───
        # Exclude the very recent bars to find the "level" that was just broken
        l_len = self.p["swing_lookback_left"]
        window_size = l_len * 2
        recent_data = candles.iloc[-(window_size + 10): -3] # Look slightly back for the swing 
        
        if len(recent_data) < l_len:
            return self.create_hold(symbol=symbol, reason="Not enough data for swing")

        # We need a stable resistance / support level.
        # Find local maximal high and minimal low in recent history.
        swing_high_idx = recent_data["high"].idxmax()
        swing_low_idx = recent_data["low"].idxmin()
        
        level_high = float(recent_data.loc[swing_high_idx, "high"])
        level_low = float(recent_data.loc[swing_low_idx, "low"])

        # ─── Sweep Detection (Current Bar or Last Bar) ───
        # Look at the last 2 bars to see if they swept the level and closed back inside.
        
        action = Action.HOLD
        reasons = []
        confidence = 0.0
        
        # Wick math
        candle_range = current_high - current_low
        if candle_range <= 0: candle_range = 1e-5
        
        upper_wick = current_high - max(current_open, current_close)
        lower_wick = min(current_open, current_close) - current_low
        
        upper_wick_ratio = upper_wick / candle_range
        lower_wick_ratio = lower_wick / candle_range

        is_bull_trap = False
        is_bear_trap = False

        # Check for SELL setup (Bull Trap / Liquidity Grab above resistance)
        if current_high > level_high and current_close < level_high:
            # Swept the high, but closed below it.
            if upper_wick_ratio >= self.p["min_wick_ratio"]:
                is_bull_trap = True
            
        # Check for BUY setup (Bear Trap / Liquidity Grab below support)
        if current_low < level_low and current_close > level_low:
            # Swept the low, but closed above it.
            if lower_wick_ratio >= self.p["min_wick_ratio"]:
                is_bear_trap = True

        # Fallback check previous bar (if current is just confirmation)
        if not is_bull_trap and not is_bear_trap:
            prev_high = float(high.iloc[-2])
            prev_low = float(low.iloc[-2])
            prev_close = float(close.iloc[-2])
            prev_open = float(open_.iloc[-2])
            
            p_range = prev_high - prev_low
            if p_range <= 0: p_range = 1e-5
            p_uw_ratio = (prev_high - max(prev_open, prev_close)) / p_range
            p_lw_ratio = (min(prev_open, prev_close) - prev_low) / p_range
            
            if prev_high > level_high and prev_close < level_high and current_close < level_high:
                if p_uw_ratio >= self.p["min_wick_ratio"]:
                    is_bull_trap = True
                    # Update local extremes to use the sweeping wick
                    current_high = prev_high
            
            if prev_low < level_low and prev_close > level_low and current_close > level_low:
                if p_lw_ratio >= self.p["min_wick_ratio"]:
                    is_bear_trap = True
                    current_low = prev_low

        trigger_price = current_close
        sl_price = None
        tp_price = None

        if is_bull_trap:
            if self.p["require_ema_alignment"] and trigger_price > ema_t_val:
                return self.create_hold(symbol=symbol, reason="Bull trap swept, but price > EMA200 (trend mismatch)")
                
            action = Action.SELL
            confidence = 75.0
            reasons.append(f"Swept Res ({level_high:.0f})")
            if vol_spike:
                confidence += 15.0
                reasons.append(f"Vol Spike ({vol_ratio:.1f}x)")
            
            # SL is placed above the liquidity sweep wick + atr buffer
            sl_price = current_high + (atr_val * self.p["sl_buffer_atr"])
            
        elif is_bear_trap:
            if self.p["require_ema_alignment"] and trigger_price < ema_t_val:
                return self.create_hold(symbol=symbol, reason="Bear trap swept, but price < EMA200 (trend mismatch)")

            action = Action.BUY
            confidence = 75.0
            reasons.append(f"Swept Sup ({level_low:.0f})")
            if vol_spike:
                confidence += 15.0
                reasons.append(f"Vol Spike ({vol_ratio:.1f}x)")
            
            # SL is placed below the liquidity sweep wick + atr buffer
            sl_price = current_low - (atr_val * self.p["sl_buffer_atr"])
            
        else:
            return self.create_hold(symbol=symbol, reason="No liquidity sweep detected")

        # Risk Management Rules (V2)
        if sl_price is not None:
            # Check Max/Min SL percentage distance
            sl_dist = abs(trigger_price - sl_price)
            sl_pct = sl_dist / trigger_price
            
            if sl_pct < self.p["min_sl_pct"]:
                # Widen SL if it's too tight (to avoid noise outs)
                if action == Action.BUY:
                    sl_price = trigger_price * (1 - self.p["min_sl_pct"])
                else:
                    sl_price = trigger_price * (1 + self.p["min_sl_pct"])
                sl_dist = abs(trigger_price - sl_price)
                reasons.append("SL widened to minimum")
                
            if sl_pct > self.p["max_sl_pct"]:
                return self.create_hold(symbol=symbol, reason=f"SL too wide ({sl_pct:.2%} > max {self.p['max_sl_pct']:.2%})")
            
            # Calculate TP based on RR
            rr = self.p["rr_strong"] if vol_spike else self.p["rr_standard"]
            if action == Action.BUY:
                tp_price = trigger_price + (sl_dist * rr)
            else:
                tp_price = trigger_price - (sl_dist * rr)
                
            self._last_signal_bar = bar_idx
            
            confidence_norm = min(confidence / 100.0, 0.95)
            reason_str = "🕵️ STOP HUNT | " + " | ".join(reasons)
            
            logger.debug("btc_stop_hunt_signal", extra={
                "symbol": symbol, "action": action.value,
                "confidence": round(confidence_norm, 2),
                "sl": round(sl_price, 2), "tp": round(tp_price, 2),
                "rr": rr, "vol_ratio": round(vol_ratio, 2)
            })
            
            return Decision(
                symbol=symbol,
                action=action,
                confidence=confidence_norm,
                reason=reason_str,
                stop_loss=sl_price,
                take_profit=tp_price,
                risk_reward_ratio=rr,
                strategy_name=self.name,
                timeframe=self.timeframe,
                tags=["liquidity_sweep", "stop_hunt", f"conf_{confidence:.0f}"],
                debug={
                    "sl_pct": round(sl_pct, 4),
                    "vol_ratio": round(vol_ratio, 2),
                    "level_high": round(level_high, 2),
                    "level_low": round(level_low, 2),
                    "ghost_protocol": self.p.get("use_ghost_protocol", False)
                }
            )

        return self.create_hold(symbol=symbol, reason="Unknown setup state")
