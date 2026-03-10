
import pandas as pd
import logging
from typing import Optional, Dict, List
from .antigravity import Signal, EntrySignal
from ..data.indicators import IndicatorEngine
from ..risk.risk_manager import RiskManager

logger = logging.getLogger("GoldSniperMini")

class GoldSniperMiniStrategy:
    """
    Gold Sniper Mini Strategy
    Focused on high-precision entries for Gold (XAUUSD) using fast EMA crosses,
    ADX trend filter, and ATR-based risk management.
    
    Logic:
    1. Trend: EMA 50 for main trend direction. ADX > 25 for trend strength.
    2. Entry: EMA 5 crosses EMA 13.
    3. Momentum: RSI must be above 50 for BUY, below 50 for SELL.
    4. Guard: RSI must not be overbought (>75) for BUY or oversold (<25) for SELL.
    5. SL/TP: ATR-based (1.5x SL, 3.0x TP).
    """
    
    RSI_MAX_BUY = 75
    RSI_MIN_SELL = 25
    ADX_MIN_TREND = 25
    MIN_CONFIDENCE = 70
    COOLDOWN_PERIOD = 15 # Minutes between same-direction signals

    def __init__(self, risk_manager: RiskManager = None):
        self.indicator_engine = IndicatorEngine()
        self.risk_manager = risk_manager or RiskManager()
        self.last_signal_time: Dict[str, int] = {} # Symbol-direction -> timestamp

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> EntrySignal:
        if len(df) < 60:
            return self._no_signal("Insufficient data (min 60 periods)")

        symbol = df.attrs.get('symbol', '').upper()
        # Handle both Timestamp (Live) and numeric (Backtest) time formats
        t_val = df.iloc[-1]['time']
        if hasattr(t_val, 'timestamp'):
            current_time = int(t_val.timestamp())
        else:
            current_time = int(t_val / 1000) # Assume ms for numeric inputs
        
        # Asset-specific configuration
        if "BTC" in symbol:
            sl_mult = 3.0 # Wider for BTC
            tp_mult = 6.0
            adx_min = 30
        elif "XAG" in symbol:
            sl_mult = 2.0
            tp_mult = 4.0
            adx_min = 25
        else: # Default Gold (XAU)
            sl_mult = 1.5
            tp_mult = 3.0
            adx_min = 25

        # Ensure indicators
        if 'EMA_5' not in df.columns:
            df = self.indicator_engine.calculate_ema(df, [5, 13, 50])
            df = self.indicator_engine.calculate_rsi(df, 14)
            df = self.indicator_engine.calculate_adx(df, 14)
            df = self.indicator_engine.calculate_stoch(df, 14, 3, 3)
            df = self.indicator_engine.calculate_atr(df, 14)
            df = self.indicator_engine.calculate_volume_avg(df, 20)

        indicators = self._get_sniper_indicators(df)
        
        # Check for BUY signal
        if direction in ("AUTO", "BUY", "BOTH"):
            # Check Cooldown
            last_buy = self.last_signal_time.get(f"{symbol}_BUY", 0)
            if current_time - last_buy > self.COOLDOWN_PERIOD * 60:
                buy_signal = self._check_buy_conditions(df, indicators, sl_mult, tp_mult, adx_min)
                if buy_signal.is_valid():
                    self.last_signal_time[f"{symbol}_BUY"] = current_time
                    return buy_signal

        # Check for SELL signal
        if direction in ("AUTO", "SELL", "BOTH"):
            # Check Cooldown
            last_sell = self.last_signal_time.get(f"{symbol}_SELL", 0)
            if current_time - last_sell > self.COOLDOWN_PERIOD * 60:
                sell_signal = self._check_sell_conditions(df, indicators, sl_mult, tp_mult, adx_min)
                if sell_signal.is_valid():
                    self.last_signal_time[f"{symbol}_SELL"] = current_time
                    return sell_signal

        return self._no_signal("Sniper scanning...")

    def _get_sniper_indicators(self, df: pd.DataFrame) -> Dict:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        return {
            'close': float(last['close']),
            'open': float(last['open']),
            'high': float(last['high']),
            'low': float(last['low']),
            'ema_5': float(last.get('EMA_5', 0)),
            'ema_13': float(last.get('EMA_13', 0)),
            'ema_5_prev': float(prev.get('EMA_5', 0)),
            'ema_13_prev': float(prev.get('EMA_13', 0)),
            'ema_50': float(last.get('EMA_50', 0)),
            'rsi': float(last.get('RSI_14', 50)),
            'adx': float(last.get('ADX_14', 0)),
            'stoch_k': float(last.get('STOCHk_14_3_3', 0)),
            'stoch_d': float(last.get('STOCHd_14_3_3', 0)),
            'atr': float(last.get('ATRr_14', last.get('ATR_14', 0))),
            'volume_ratio': float(last.get('Volume_Ratio', 1.0))
        }

    def _check_buy_conditions(self, df: pd.DataFrame, indicators: Dict, sl_mult: float, tp_mult: float, adx_min: float) -> EntrySignal:
        confidence = 0
        reasons = []
        
        close = indicators['close']
        ema_5 = indicators['ema_5']
        ema_13 = indicators['ema_13']
        ema_5_prev = indicators['ema_5_prev']
        ema_13_prev = indicators['ema_13_prev']
        ema_50 = indicators['ema_50']
        rsi = indicators['rsi']
        adx = indicators['adx']
        stoch_k = indicators['stoch_k']
        stoch_d = indicators['stoch_d']
        atr = indicators['atr']
        
        # 1. EMA Cross (Primary Signal) (+40)
        if ema_5 > ema_13 and ema_5_prev <= ema_13_prev:
            confidence += 40
            reasons.append("🎯 EMA 5/13 Cross Up (Sniper Entry)")
        elif ema_5 > ema_13:
            confidence += 20
            reasons.append("✅ EMA 5/13 Bullish Alignment")
        else:
            return self._no_signal("EMA alignment not bullish")

        # 2. Main Trend Filter (+20)
        if close > ema_50:
            confidence += 20
            reasons.append(f"✅ Price > EMA 50 (Trend Up)")
        else:
            confidence -= 10
            reasons.append("⚠️ Price below EMA 50")

        # 3. RSI Momentum (+20)
        if 50 < rsi < self.RSI_MAX_BUY:
            confidence += 20
            reasons.append(f"✅ RSI Momentum ({rsi:.1f})")
        elif rsi >= self.RSI_MAX_BUY:
            confidence -= 30
            reasons.append(f"❌ RSI Overextended ({rsi:.1f})")
        else:
            confidence -= 20
            reasons.append(f"❌ RSI weak ({rsi:.1f})")

        # 3.1 Stochastic Confirmation (+15)
        # Buy when Stoch K > D and not overbought
        if stoch_k > stoch_d and stoch_k < 80:
            confidence += 15
            reasons.append(f"✅ Stoch Confirmation (K:{stoch_k:.1f} > D:{stoch_d:.1f})")
        elif stoch_k >= 80:
            confidence -= 20
            reasons.append(f"⚠️ Stoch Overbought ({stoch_k:.1f})")

        # 4. ADX Trend Strength (+20)
        if adx > adx_min:
            confidence += 20
            reasons.append(f"✅ Strong Trend (ADX {adx:.1f} > {adx_min})")
        else:
            confidence += 5
            reasons.append(f"⚠️ Trend weak (ADX {adx:.1f})")

        # 5. Price Action Confirmation
        if close > indicators['open']: # Bullish candle
            confidence += 5
            reasons.append("✅ Bullish Candle Confirmation")

        # Risk Management (ATR-based)
        sl = close - (sl_mult * atr)
        tp = close + (tp_mult * atr)
        
        return EntrySignal(
            signal=Signal.BUY if confidence >= self.MIN_CONFIDENCE else Signal.NONE,
            confidence=float(max(0, min(100, confidence))),
            entry_method="SNIPER_EMA_CROSS_BUY",
            entry_price=float(close),
            stop_loss=float(sl),
            take_profit=float(tp),
            reasons=reasons
        )

    def _check_sell_conditions(self, df: pd.DataFrame, indicators: Dict, sl_mult: float, tp_mult: float, adx_min: float) -> EntrySignal:
        confidence = 0
        reasons = []
        
        close = indicators['close']
        ema_5 = indicators['ema_5']
        ema_13 = indicators['ema_13']
        ema_5_prev = indicators['ema_5_prev']
        ema_13_prev = indicators['ema_13_prev']
        ema_50 = indicators['ema_50']
        rsi = indicators['rsi']
        adx = indicators['adx']
        stoch_k = indicators['stoch_k']
        stoch_d = indicators['stoch_d']
        atr = indicators['atr']
        
        # 1. EMA Cross (Primary Signal) (+40)
        if ema_5 < ema_13 and ema_5_prev >= ema_13_prev:
            confidence += 40
            reasons.append("🎯 EMA 5/13 Cross Down (Sniper Entry)")
        elif ema_5 < ema_13:
            confidence += 20
            reasons.append("✅ EMA 5/13 Bearish Alignment")
        else:
            return self._no_signal("EMA alignment not bearish")

        # 2. Main Trend Filter (+20)
        if close < ema_50:
            confidence += 20
            reasons.append(f"✅ Price < EMA 50 (Trend Down)")
        else:
            confidence -= 10
            reasons.append("⚠️ Price above EMA 50")

        # 3. RSI Momentum (+20)
        if self.RSI_MIN_SELL < rsi < 50:
            confidence += 20
            reasons.append(f"✅ RSI Momentum ({rsi:.1f})")
        elif rsi <= self.RSI_MIN_SELL:
            confidence -= 30
            reasons.append(f"❌ RSI Overextended/Oversold ({rsi:.1f})")
        else:
            confidence -= 20
            reasons.append(f"❌ RSI weak ({rsi:.1f})")

        # 3.1 Stochastic Confirmation (+15)
        # Sell when Stoch K < D and not oversold
        if stoch_k < stoch_d and stoch_k > 20:
            confidence += 15
            reasons.append(f"✅ Stoch Confirmation (K:{stoch_k:.1f} < D:{stoch_d:.1f})")
        elif stoch_k <= 20:
            confidence -= 20
            reasons.append(f"⚠️ Stoch Oversold ({stoch_k:.1f})")

        # 4. ADX Trend Strength (+20)
        if adx > adx_min:
            confidence += 20
            reasons.append(f"✅ Strong Trend (ADX {adx:.1f} > {adx_min})")
        else:
            confidence += 5
            reasons.append(f"⚠️ Trend weak (ADX {adx:.1f})")

        # 5. Price Action Confirmation
        if close < indicators['open']: # Bearish candle
            confidence += 5
            reasons.append("✅ Bearish Candle Confirmation")

        # Risk Management (ATR-based)
        sl = close + (sl_mult * atr)
        tp = close - (tp_mult * atr)
        
        return EntrySignal(
            signal=Signal.SELL if confidence >= self.MIN_CONFIDENCE else Signal.NONE,
            confidence=float(max(0, min(100, confidence))),
            entry_method="SNIPER_EMA_CROSS_SELL",
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
gold_sniper_mini_strategy = GoldSniperMiniStrategy()
