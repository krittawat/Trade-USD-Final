
import pandas as pd
import pandas as pd
try:
    import app.analysis.indicators as ind
except ImportError:
    from app.utils.safe_ta import SafeTA as ta
from .base_strategy import BaseStrategy, StrategyDecision

class OmegaEmperorStrategy(BaseStrategy):
    """
    OMEGA EMPEROR v2.0 - Hybrid (Trend + Sideways)
    - Trend Mode (ADX > 25): Brutal Scalping (EMA + RSI aggressive).
    - Sideways Mode (ADX < 25): Mean Reversion (Bollinger Bands).
      - Buy at Lower Band.
      - Sell at Upper Band.
    - Result: Plugs the 'Sideways Hole' and turns range-bound chop into profit.
    """

    def __init__(self):
        self.name = "OMEGA_EMPEROR"
        self.version = "2.0 (Hybrid Shield)"
        
    def analyze(self, **kwargs) -> StrategyDecision:
        df = kwargs.get('df')
        if df is None or len(df) < 50:
            return StrategyDecision("NO_TRADE", "Not enough data")
            
        current = df.iloc[-1]
        price = current['close']
        
        # 1. Indicators
        rsi = ta.rsi(df['close'], length=14).iloc[-1]
        atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
        
        # EMA for Trend
        ema_fast = ta.ema(df['close'], length=9).iloc[-1]
        ema_slow = ta.ema(df['close'], length=21).iloc[-1]
        
        # ADX for Regime
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_df is not None and not adx_df.empty:
            adx = adx_df['ADX_14'].iloc[-1]
        else:
            adx = 30 # Default to trend if error
            
        # Bollinger Bands for Sideways
        bb = ta.bbands(df['close'], length=20, std=2.0)
        # Safe Access via iloc (Lower=0, Mid=1, Upper=2)
        lower_band = bb.iloc[-1, 0]
        middle_band = bb.iloc[-1, 1]
        upper_band = bb.iloc[-1, 2]
        
        signal = "NO_TRADE"
        reason = "Wait"
        
        # ==========================================
        # ⚔️ MODE 1: EMPEROR TREND (ADX > 25)
        # ==========================================
        if adx > 25:
            # Brutal Logic (High Sensitivity)
            if ema_fast > ema_slow:
                if rsi < 80: # Relaxed condition for testing
                    signal = "BUY"
                    reason = f"Emperor Trend: Up (ADX {adx:.1f}) + RSI Dip"
            elif ema_fast < ema_slow:
                if rsi > 20: # Relaxed condition for testing
                    signal = "SELL"
                    reason = f"Emperor Trend: Down (ADX {adx:.1f}) + RSI Rally"
        else:
            # Mean Reversion Logic (Relaxed)
            if price <= lower_band * 1.001: 
                signal = "BUY"
                reason = f"Sideways Shield: Lower BB (ADX {adx:.1f})"
            elif price >= upper_band * 0.999:
                signal = "SELL"
                reason = f"Sideways Shield: Upper BB (ADX {adx:.1f})"
                
        # Debug Print (Only first 5 candles to avoid spam)
        # print(f"DEBUG: P={price:.2f} ADX={adx:.2f} RSI={rsi:.2f} Sig={signal}")
        
        # 3. Dynamic TP/SL
        if signal != "NO_TRADE":
            if adx <= 25:
                # Sideways: TP at Middle Band (Safe)
                if signal == "BUY":
                    tp = middle_band
                    sl = price - (atr * 1.5)
                else:
                    tp = middle_band
                    sl = price + (atr * 1.5)
            else:
                # Trend: Let it run a bit
                volatility_mult = 0.6 # Faster TP for Recovery
                tp_dist = atr * volatility_mult
                sl_dist = atr * 1.5 # Tighter SL to protect equity
                
                if signal == "BUY":
                    tp = price + tp_dist
                    sl = price - sl_dist
                else:
                    tp = price - tp_dist
                    sl = price + sl_dist
        else:
            tp = 0.0
            sl = 0.0
            
        return StrategyDecision(
            signal=signal,
            entry_price=price,
            tp=tp,
            sl=sl,
            reason=reason,
            confidence=0.9 if signal != "NO_TRADE" else 0.0,
            risk_pct=2.0
        )

    def get_status(self):
        return {"name": self.name, "version": self.version}

omega_emperor = OmegaEmperorStrategy()
