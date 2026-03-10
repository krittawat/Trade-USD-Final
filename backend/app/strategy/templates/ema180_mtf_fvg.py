"""
EMA 180 MTF Bounce Strategy (Gold & Silver).

Philosophy:
    - "บางที TF 5นาที กำลังขึ้นก็จริง แต่ H1 H4 แท่งจะลง ก็ไม่ไป ใช้ EMA 180 วัดค่า การเข้าไม้"
    - Trade M5 bounces on the EMA 180.
    - Strictly require H1 and H4 confirmation (EMA 50 vs 180) to avoid false setups.

Target Symbols: Gold (XAUUSD) & Silver (XAGUSD).
"""

import pandas as pd
import numpy as np
from app.strategy.base import BaseStrategy
from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.analysis.indicators import heiken_ashi
from app.analysis.fvg import detect_fvg

logger = get_logger(__name__)

DEFAULTS = {
    "ema_main": 180,
    "mtf_ema_fast": 20,  
    "mtf_ema_slow": 180,  
    "atr_period": 14,
    "sl_atr_mult": 3.0,          # Wider base ATR mult (was 2.5)
    "bounce_tolerance_atr": 2.0, # Stricter entry (was 2.5)
    "rr_ratio": 1.0,             # Better RR for profitability
    "sweep_lookback": 25,        
    "sweep_atr_mult": 0.3,       
    "rsi_period": 14,
    "rsi_overbought": 75,        # Trend pullbacks can have high RSI
    "rsi_oversold": 25,
}

class Ema180MtfFvgStrategy(BaseStrategy):
    """
    EMA 180 MTF Bounce + FVG Strategy.
    Trades M5 pullbacks to EMA 180, filtered by H1/H4 trends and Fair Value Gaps.
    """
    name = "ema180_mtf_fvg"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.RANGING,
        RegimeType.LOW_VOLATILITY,
        RegimeType.LIQUIDITY_SWEEP,
    ]

    def __init__(self) -> None:
        super().__init__()
        from app.strategy.param_loader import get_param_loader
        loader = get_param_loader()
        self.p = DEFAULTS.copy()
        if loader:
            # Attempt to load for Gold specifically as a fallback baseline
            db_params = loader.get_params(self.name, "XAUUSDc", DEFAULTS)
            self.p.update(db_params)

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol if profile else "UNKNOWN"

        min_bars = self.p["ema_main"] + 20
        if candles is None or len(candles) < min_bars:
            return self.create_hold(symbol=symbol, reason="Insufficient Data")

        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        open_ = candles["open"].astype(float)
        
        current_close = float(close.iloc[-1])
        current_high = float(high.iloc[-1])
        current_low = float(low.iloc[-1])
        
        current_open = float(open_.iloc[-1])
        ema_210 = close.ewm(span=self.p["ema_main"], adjust=False).mean()
        ema_210_val = float(ema_210.iloc[-1])
        
        # 2. ATR (Safe calculation)
        df_len = len(close)
        if df_len > self.p["atr_period"]:
            # Standard ATR formulation
            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr_series = tr.rolling(self.p["atr_period"]).mean()
            atr_val = float(atr_series.iloc[-1])
        else:
            atr_val = float((high.iloc[-1] - low.iloc[-1]) * 0.5) # fallback
        
        if pd.isna(ema_210_val) or pd.isna(atr_val):
            return self.create_hold(symbol=symbol, reason="NaN Indicators")

        # 2.5 Heiken Ashi Filter
        # ha_df = heiken_ashi(open_, high, low, close)
        # ha_open_val = float(ha_df['HA_Open'].iloc[-1])
        # ha_close_val = float(ha_df['HA_Close'].iloc[-1])
        ha_bullish = True # ha_close_val > ha_open_val
        ha_bearish = True # ha_close_val < ha_open_val

        # 3. RSI Calculation for momentum filtering
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(self.p["rsi_period"]).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(self.p["rsi_period"]).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

        # 4. Detect M5 Setup (Is price interacting with EMA 210?)
        dist_to_ema = abs(current_close - ema_210_val)
        is_near_ema = dist_to_ema <= (atr_val * self.p["bounce_tolerance_atr"])
        
        # Bullish setup: Price dipping near EMA 210, RSI not extremely overbought
        m5_bullish_setup = (current_low <= ema_210_val + (atr_val * 2.5)) and (current_close > ema_210_val - (atr_val * 1.5)) and (rsi_val < self.p["rsi_overbought"])
        
        # Bearish setup: Price rallying near EMA 210, RSI not extremely oversold
        m5_bearish_setup = (current_high >= ema_210_val - (atr_val * 2.5)) and (current_close < ema_210_val + (atr_val * 1.5)) and (rsi_val > self.p["rsi_oversold"])

        if not is_near_ema and not m5_bullish_setup and not m5_bearish_setup:
            return self.create_hold(symbol=symbol, reason=f"Price {current_close:.2f} far from EMA210 {ema_210_val:.2f} (Dist: {dist_to_ema:.2f})")

        # If we got here, we have a setup. Let's trace it.
        # print(f"DEBUG {symbol} | M5 Bull: {m5_bullish_setup} Bear: {m5_bearish_setup} | dist={dist_to_ema:.2f} RSI={rsi_val:.1f}")

        # 4. Check MTF (H1, H4) Confirmation
        h1_candles = kwargs.get("h1_candles")
        h4_candles = kwargs.get("h4_candles")
        
        h1_trend_up = False
        h1_trend_down = False
        if h1_candles is not None and len(h1_candles) > self.p["mtf_ema_slow"]:
            h1_c = h1_candles["close"].astype(float)
            h1_ema_50 = float(h1_c.ewm(span=self.p["mtf_ema_fast"], adjust=False).mean().iloc[-1])
            h1_ema_200 = float(h1_c.ewm(span=self.p["mtf_ema_slow"], adjust=False).mean().iloc[-1])
            h1_trend_up = h1_ema_50 > h1_ema_200
            h1_trend_down = h1_ema_50 < h1_ema_200

        h4_trend_up = False
        h4_trend_down = False
        if h4_candles is not None and len(h4_candles) > self.p["mtf_ema_slow"]:
            h4_c = h4_candles["close"].astype(float)
            h4_ema_50 = float(h4_c.ewm(span=self.p["mtf_ema_fast"], adjust=False).mean().iloc[-1])
            h4_ema_200 = float(h4_c.ewm(span=self.p["mtf_ema_slow"], adjust=False).mean().iloc[-1])
            h4_trend_up = h4_ema_50 > h4_ema_200
            h4_trend_down = h4_ema_50 < h4_ema_200
            
        # Overall Structural Trend (H1 and H4 MUST align for high probability bounce)
        # We only trade if the higher timeframes support the bounce direction.
        mtf_bullish = h1_trend_up and h4_trend_up
        mtf_bearish = h1_trend_down and h4_trend_down
        
        # We no longer strictly block if MTF is neutral because a strong M5 bounce with HA confirmation 
        # is often the start of a new momentum wave. We just use MTF to boost confidence.
        # However, we DO block if the higher timeframe is actively trending strongly against us.
        block_buy = (h1_trend_down and h4_trend_down) or (h4_trend_down and not h1_trend_up) or (h1_trend_down and not h4_trend_up)
        block_sell = (h1_trend_up and h4_trend_up) or (h4_trend_up and not h1_trend_down) or (h1_trend_up and not h4_trend_down)
        
        # print(f"DEBUG MTF | H1 Up:{h1_trend_up} Down:{h1_trend_down} | H4 Up:{h4_trend_up} Down:{h4_trend_down} | BlockBuy:{block_buy} BlockSell:{block_sell}")

        # 5. Fair Value Gap (FVG) Detection — using shared module
        try:
            fvg = detect_fvg(candles, atr_value=float(atr_val), min_body_atr_ratio=0.5)
            fvg_bullish = bool(fvg["bullish"])
            fvg_bearish = bool(fvg["bearish"])
            fvg_zone_top = float(fvg["zone_top"])
            fvg_zone_bottom = float(fvg["zone_bottom"])
        except Exception:
            fvg_bullish = False
            fvg_bearish = False
            fvg_zone_top = 0.0
            fvg_zone_bottom = 0.0
        
        # Check if current price is filling the FVG zone (returning to it)
        price_in_bullish_fvg = fvg_bullish and (current_close >= fvg_zone_bottom and current_close <= fvg_zone_top + atr_val * 0.5)
        price_in_bearish_fvg = fvg_bearish and (current_close <= fvg_zone_top and current_close >= fvg_zone_bottom - atr_val * 0.5)

        action = Action.HOLD
        reason = ""
        sl_price = 0.0
        tp_price = 0.0
        confidence = 0.0

        if m5_bullish_setup:
            if not ha_bullish:
                return self.create_hold(symbol=symbol, reason="M5 BUY blocked by HA Filter: Not Bullish")
            if block_buy:
                return self.create_hold(symbol=symbol, reason="M5 BUY setup blocked by H1/H4 Downtrend")
            if fvg_bearish and price_in_bearish_fvg:
                return self.create_hold(symbol=symbol, reason="M5 BUY blocked by Bearish FVG fill (Reversal Warning)")
                
            action = Action.BUY
            confidence = 0.85
            reason = f"M5 Bounce EMA180 ({ema_210_val:.2f})"
            if mtf_bullish:
                confidence = 0.90
                reason += " + MTF Aligned"
            if price_in_bullish_fvg:
                confidence = min(0.95, confidence + 0.05)
                reason += " + Bullish FVG"
                
            # SL below EMA 210 and recent low
            recent_series = candles["low"].iloc[-5:]
            if len(recent_series) > 0:
                recent_low = float(recent_series.min())
                if pd.isna(recent_low):
                    recent_low = current_low
            else:
                recent_low = current_low
                
            base_sl = min(ema_210_val, recent_low)
            sl_price = float(base_sl - (atr_val * self.p["sl_atr_mult"]))
            
            # print(f"DEBUG {symbol} | M5 BUY SETUP VALID. SL={sl_price:.2f}")

        elif m5_bearish_setup:
            if not ha_bearish:
                return self.create_hold(symbol=symbol, reason="M5 SELL blocked by HA Filter: Not Bearish")
            if block_sell:
                return self.create_hold(symbol=symbol, reason="M5 SELL setup blocked by H1/H4 Uptrend")
            if fvg_bullish and price_in_bullish_fvg:
                return self.create_hold(symbol=symbol, reason="M5 SELL blocked by Bullish FVG fill (Reversal Warning)")
                
            action = Action.SELL
            confidence = 0.85
            reason = f"M5 Reject EMA180 ({ema_210_val:.2f})"
            if mtf_bearish:
                confidence = 0.90
                reason += " + MTF Aligned"
            if price_in_bearish_fvg:
                confidence = min(0.95, confidence + 0.05)
                reason += " + Bearish FVG"
                
            # SL above EMA 210 and recent high
            recent_series = candles["high"].iloc[-5:]
            if len(recent_series) > 0:
                recent_high = float(recent_series.max())
                if pd.isna(recent_high):
                    recent_high = current_high
            else:
                recent_high = current_high
                
            base_sl = max(ema_210_val, recent_high)
            sl_price = float(base_sl + (atr_val * self.p["sl_atr_mult"]))
            
            # print(f"DEBUG {symbol} | M5 SELL SETUP VALID. SL={sl_price:.2f}")

        if action == Action.HOLD:
             return self.create_hold(symbol=symbol, reason="Waiting for clearer M5 EMA180 interaction")

        # Basic SL validation
        sl_dist = float(abs(current_close - sl_price))
        if sl_dist < atr_val * 0.5:
             if action == Action.BUY:
                 sl_price = float(current_close - (atr_val * 1.0))
             else:
                 sl_price = float(current_close + (atr_val * 1.0))
             sl_dist = float(abs(current_close - sl_price))

        if action == Action.BUY:
             tp_price = float(current_close + (sl_dist * self.p["rr_ratio"]))
        else:
             tp_price = float(current_close - (sl_dist * self.p["rr_ratio"]))

        # logger.debug("ema210_mtf_signal", extra={
        #     "symbol": symbol, "action": action.value,
        #     "ema_210": round(ema_210_val, 2), "close": round(current_close, 2),
        #     "h1_up": h1_trend_up, "h4_up": h4_trend_up,
        #     "h1_down": h1_trend_down, "h4_down": h4_trend_down,
        # })

        return Decision(
            symbol=symbol,
            action=action,
            confidence=float(confidence),
            reason=str(reason),
            stop_loss=float(sl_price),
            take_profit=float(tp_price),
            risk_reward_ratio=float(self.p["rr_ratio"]),
            strategy_name=str(self.name),
            timeframe=str(self.timeframe),
            tags=["ema180", "mtf_filtered", "fvg"],
            debug={
                "ema180": float(round(ema_210_val, 2)),
                "atr": float(round(atr_val, 2)),
                "fvg_bull": bool(fvg_bullish), "fvg_bear": bool(fvg_bearish),
                "h1_up": bool(h1_trend_up), "h4_up": bool(h4_trend_up),
                "h1_down": bool(h1_trend_down), "h4_down": bool(h4_trend_down),
            }
        )
