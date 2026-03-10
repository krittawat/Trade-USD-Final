"""
Gold Reversal Grid Strategy
Strategy: Mean Reversion with Grid / Averaging
Logic:
1. Signal: Price touches/exceeds Bollinger Bands (20, 2.0) AND RSI(14) is Overbought (>70) or Oversold (<30).
2. Counter-trend: Sell at Upper Band + OB, Buy at Lower Band + OS.
3. Grid: If price moves against us, we add layers at ATR-based intervals.
"""
import pandas as pd
import MetaTrader5 as mt5
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass
from .antigravity import Signal, EntrySignal
from ..data.indicators import IndicatorEngine
from ..risk.risk_manager import RiskManager
from ..core.safe_mt5 import safe_mt5

logger = logging.getLogger("GoldReversalGrid")

class MultiAssetGridStrategy:
    # Thresholds
    RSI_OB = 65  
    RSI_OS = 35  
    MIN_CONFIDENCE = 70
    VOL_MULT_THRESHOLD = 1.2 # Volume must be 20% above average
    
    # Asset Specific Settings (SL widened further for metals)
    ASSET_CONFIG = {
        "XAU": {"atr_mult": 3.0, "grid_step": 2.0},   # Widened from 2.5 for Gold volatility
        "BTC": {"atr_mult": 3.0, "grid_step": 3.0},   # Keep as is for BTC
        "XAG": {"atr_mult": 2.5, "grid_step": 1.5},   # Widened from 2.0 for Silver
        "DEFAULT": {"atr_mult": 2.5, "grid_step": 2.0}
    }

    def __init__(self, risk_manager: RiskManager = None):
        self.indicator_engine = IndicatorEngine()
        self.risk_manager = risk_manager or RiskManager()

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO", htf_trend: Optional[str] = None) -> EntrySignal:
        if len(df) < 30:
            return self._no_signal("Insufficient data")

        # Ensure indicators
        if 'BB_Upper' not in df.columns:
            df = self.indicator_engine.calculate_all(df)

        indicators = self.indicator_engine.get_latest_indicators(df)
        
        # Check for Overbought Reversal (SELL)
        if direction in ("AUTO", "SELL", "BOTH"):
            sell_signal = self._check_sell_conditions(df, indicators, htf_trend)
            if sell_signal.is_valid():
                return sell_signal

        # Check for Oversold Reversal (BUY)
        if direction in ("AUTO", "BUY", "BOTH"):
            buy_signal = self._check_buy_conditions(df, indicators, htf_trend)
            if buy_signal.is_valid():
                return buy_signal

        return self._no_signal("Waiting for Reversal Zone")

    def _check_mtf_trend(self, symbol: str) -> str:
        """Fetch H1 Trend for confirmation using SafeMT5"""
        rates = safe_mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 50)
        if rates is None: return "UNKNOWN"
        df_h1 = pd.DataFrame(rates)
        ema50 = df_h1['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        curr_price = df_h1['close'].iloc[-1]
        return "UP" if curr_price > ema50 else "DOWN"
            
    def _check_volume_filter(self, df: pd.DataFrame) -> bool:
        """Check if current volume is sufficient for a reversal"""
        if 'tick_volume' not in df.columns: return True
        avg_vol = df['tick_volume'].rolling(20).mean().iloc[-1]
        curr_vol = df['tick_volume'].iloc[-1]
        return curr_vol >= (avg_vol * self.VOL_MULT_THRESHOLD)

    def _check_buy_conditions(self, df: pd.DataFrame, indicators: Dict, htf_trend: Optional[str] = None) -> EntrySignal:
        confidence = 0
        reasons = []
        
        close = indicators['close']
        bb_lower = indicators['bb_lower']
        rsi = indicators['rsi']
        atr = indicators['atr']
        
        # 1. Price vs BB Lower (+50)
        if close <= bb_lower:
            confidence += 50
            reasons.append(f"✅ Price {close:.2f} <= BB Lower {bb_lower:.2f}")
        elif close <= bb_lower + (atr * 0.2):
            confidence += 30
            reasons.append("⚠️ Price near BB Lower")
            
        # 2. RSI Oversold (+40)
        if rsi <= self.RSI_OS:
            confidence += 40
            reasons.append(f"✅ RSI Oversold ({rsi:.1f})")
        elif rsi <= self.RSI_OS + 5:
            confidence += 20
            reasons.append(f"⚠️ RSI near Oversold ({rsi:.1f})")

        # 3. Confirmation: Candle rejection (optional logic)
        last_candle = df.iloc[-1]
        if last_candle['close'] > last_candle['open']: # Bullish candle
            confidence += 10
            reasons.append("✅ Bullish rejection candle")
            
        # 4. MTF Trend Alignment (+20)
        symbol = df.attrs.get('symbol', 'BTCUSD')
        current_h1_trend = htf_trend if htf_trend else self._check_mtf_trend(symbol)
        if current_h1_trend == "UP":
            confidence += 20
            reasons.append("✅ H1 Trend is UP (Alignment)")
        elif current_h1_trend == "DOWN":
            confidence -= 20
            reasons.append("⚠️ Counter-Trend (H1 is DOWN)")
            
        # 5. Volume Confirmation (+15)
        if self._check_volume_filter(df):
            confidence += 15
            reasons.append("✅ Volume Spike detected (Liquidity)")
        else:
            confidence -= 10
            reasons.append("⚠️ Low liquidity for reversal")

        # Risk Params for Reversal
        asset_key = "BTC" if "BTC" in symbol else ("XAU" if "XAU" in symbol else "DEFAULT")
        cfg = self.ASSET_CONFIG.get(asset_key, self.ASSET_CONFIG["DEFAULT"])
        
        sl_mult = cfg["atr_mult"]
        sl = close - (atr * sl_mult) 
        tp = close + (atr * sl_mult)
        
        return EntrySignal(
            signal=Signal.BUY if confidence >= self.MIN_CONFIDENCE else Signal.NONE,
            confidence=float(confidence),
            entry_method="BB_REVERSAL_BUY",
            entry_price=float(close),
            stop_loss=float(sl),
            take_profit=float(tp),
            reasons=reasons
        )

    def calculate_grid_levels(self, symbol: str, entry_price: float, direction: str, current_atr: float) -> List[Dict]:
        """Calculate recovery grid levels based on ATR distance"""
        asset_key = "BTC" if "BTC" in symbol else ("XAU" if "XAU" in symbol else "DEFAULT")
        cfg = self.ASSET_CONFIG.get(asset_key, self.ASSET_CONFIG["DEFAULT"])
        step_mult = cfg["grid_step"]
        
        levels = []
        for i in range(1, 4): # 3 Recovery layers
            dist = current_atr * step_mult * i
            price = entry_price - dist if direction == "BUY" else entry_price + dist
            levels.append({
                "layer": i,
                "price": float(price),
                "volume_mult": 1.0 + (i * 0.5) # Increase lot size per layer
            })
        return levels

    def _check_sell_conditions(self, df: pd.DataFrame, indicators: Dict, htf_trend: Optional[str] = None) -> EntrySignal:
        confidence = 0
        reasons = []
        
        close = indicators['close']
        bb_upper = indicators['bb_upper']
        rsi = indicators['rsi']
        atr = indicators['atr']
        
        # 1. Price vs BB Upper (+50)
        if close >= bb_upper:
            confidence += 50
            reasons.append(f"✅ Price {close:.2f} >= BB Upper {bb_upper:.2f}")
        elif close >= bb_upper - (atr * 0.2):
            confidence += 30
            reasons.append("⚠️ Price near BB Upper")
            
        # 2. RSI Overbought (+40)
        if rsi >= self.RSI_OB:
            confidence += 40
            reasons.append(f"✅ RSI Overbought ({rsi:.1f})")
        elif rsi >= self.RSI_OB - 5:
            confidence += 20
            reasons.append(f"⚠️ RSI near Overbought ({rsi:.1f})")

        # 3. Confirmation: Bearish candle
        last_candle = df.iloc[-1]
        if last_candle['close'] < last_candle['open']:
            confidence += 10
            reasons.append("✅ Bearish rejection candle")
            
        # 4. MTF Trend Alignment (+20)
        symbol = df.attrs.get('symbol', 'BTCUSD')
        current_h1_trend = htf_trend if htf_trend else self._check_mtf_trend(symbol)
        if current_h1_trend == "DOWN":
            confidence += 20
            reasons.append("✅ H1 Trend is DOWN (Alignment)")
        elif current_h1_trend == "UP":
            confidence -= 20
            reasons.append("⚠️ Counter-Trend (H1 is UP)")
            
        # 5. Volume Confirmation (+15)
        if self._check_volume_filter(df):
            confidence += 15
            reasons.append("✅ Volume Spike detected (Liquidity)")
        else:
            confidence -= 10
            reasons.append("⚠️ Low liquidity for reversal")

        # Risk Params
        asset_key = "BTC" if "BTC" in symbol else ("XAU" if "XAU" in symbol else "DEFAULT")
        cfg = self.ASSET_CONFIG.get(asset_key, self.ASSET_CONFIG["DEFAULT"])
        
        sl_mult = cfg["atr_mult"]
        sl = close + (atr * sl_mult)
        tp = close - (atr * sl_mult)
        
        return EntrySignal(
            signal=Signal.SELL if confidence >= self.MIN_CONFIDENCE else Signal.NONE,
            confidence=float(confidence),
            entry_method="BB_REVERSAL_SELL",
            entry_price=float(close),
            stop_loss=float(sl),
            take_profit=float(tp),
            reasons=reasons
        )

    def _no_signal(self, reason: str) -> EntrySignal:
        return EntrySignal(
            signal=Signal.NONE,
            confidence=0,
            entry_method="NONE",
            entry_price=0,
            stop_loss=0,
            take_profit=0,
            reasons=[reason]
        )

# Global Instance
gold_reversal_strategy = MultiAssetGridStrategy()
