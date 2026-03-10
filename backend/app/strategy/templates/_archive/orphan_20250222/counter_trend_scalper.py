
import pandas as pd
import logging
from typing import Optional, Dict
from .antigravity import Signal, EntrySignal
from ..data.indicators import IndicatorEngine
from ..risk.risk_manager import RiskManager

logger = logging.getLogger("CounterTrendScalper")

class CounterTrendScalperStrategy:
    """
    Counter Trend Scalper Strategy (Sniper Mode)
    
    Logic:
    1.  Identify EXTREME overextended conditions.
    2.  Filter out strong trends (ADX > 30).
    3.  Enter with tight stop and wide target.
    
    Entry:
    - BUY: RSI < 25 AND Close < BB_Lower
    - SELL: RSI > 75 AND Close > BB_Upper
    
    Risk Management:
    - SL: 1.0 * ATR (Tight)
    - TP: 3.0 * ATR (High Reward)
    """
    
    MAX_ADX_FOR_COUNTER = 30 # STRICT Range Filter
    RSI_OVERBOUGHT = 75
    RSI_OVERSOLD = 25
    MIN_CONFIDENCE = 80
    COOLDOWN_PERIOD = 5 # Minutes

    def __init__(self, risk_manager: RiskManager = None):
        self.indicator_engine = IndicatorEngine()
        self.risk_manager = risk_manager or RiskManager()
        self.last_signal_time: Dict[str, int] = {}

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> EntrySignal:
        if len(df) < 60:
            return self._no_signal("Insufficient data")

        symbol = df.attrs.get('symbol', '').upper()
        # Handle both Timestamp (Live) and numeric (Backtest) time formats
        t_val = df.iloc[-1]['time']
        if hasattr(t_val, 'timestamp'):
            current_time = int(t_val.timestamp())
        else:
            current_time = int(t_val / 1000)

        # Asset-Specific Configuration
        # BTC is wilder, so we relax the ADX filter slightly but keep stops proportional (ATR handles scale)
        # Silver (XAG) moves fast, similar to Gold.
        if "BTC" in symbol:
             max_adx = 40 # Allow a bit more trendiness in BTC
             sl_mult = 1.2 # Slightly wider SL to avoid wickouts
             tp_mult = 3.5 # Bigger moves
        elif "XAG" in symbol:
             max_adx = 35
             sl_mult = 1.0
             tp_mult = 3.0
        else: # Gold (Default)
             max_adx = self.MAX_ADX_FOR_COUNTER
             sl_mult = 1.0
             tp_mult = 3.0
            
        # Ensure indicators
        if 'RSI_14' not in df.columns:
            df = self.indicator_engine.calculate_rsi(df, 14)
            df = self.indicator_engine.calculate_bb(df, 20, 2.5)
            df = self.indicator_engine.calculate_atr(df, 14)
            df = self.indicator_engine.calculate_adx(df, 14)

        indicators = self._get_indicators(df)
        
        # Global Trend Filter
        if indicators['adx'] > max_adx:
            return self._no_signal(f"Trend too strong for {symbol} (ADX: {indicators['adx']:.1f} > {max_adx})")
        
        # Check for BUY signal
        if direction in ("AUTO", "BUY", "BOTH"):
            last_buy = self.last_signal_time.get(f"{symbol}_BUY", 0)
            if current_time - last_buy > self.COOLDOWN_PERIOD * 60:
                buy_signal = self._check_buy_conditions(indicators, sl_mult, tp_mult)
                if buy_signal.is_valid():
                    self.last_signal_time[f"{symbol}_BUY"] = current_time
                    return buy_signal

        # Check for SELL signal
        if direction in ("AUTO", "SELL", "BOTH"):
            last_sell = self.last_signal_time.get(f"{symbol}_SELL", 0)
            if current_time - last_sell > self.COOLDOWN_PERIOD * 60:
                sell_signal = self._check_sell_conditions(indicators, sl_mult, tp_mult)
                if sell_signal.is_valid():
                    self.last_signal_time[f"{symbol}_SELL"] = current_time
                    return sell_signal

        return self._no_signal("Scanning for extremes...")

    def _get_indicators(self, df: pd.DataFrame) -> Dict:
        last = df.iloc[-1]
        return {
            'close': float(last['close']),
            'rsi': float(last.get('RSI_14', 50)),
            'bb_upper': float(last.get('BB_Upper', 0)),
            'bb_lower': float(last.get('BB_Lower', 0)),
            'atr': float(last.get('ATRr_14', last.get('ATR_14', 0))),
            'adx': float(last.get('ADX_14', 0)),
        }

    def _check_buy_conditions(self, indicators: Dict, sl_mult: float, tp_mult: float) -> EntrySignal:
        confidence = 0
        reasons = []
        
        close = indicators['close']
        rsi = indicators['rsi']
        bb_lower = indicators['bb_lower']
        atr = indicators['atr']
        
        # 1. RSI Oversold (+40)
        if rsi < self.RSI_OVERSOLD:
            confidence += 40
            reasons.append(f"✅ RSI Oversold ({rsi:.1f} < {self.RSI_OVERSOLD})")
        else:
            return self._no_signal("RSI not oversold")

        # 2. Bollinger Band Extreme (+40)
        # Dynamic BB Width: If the bands are too tight, the extreme is less meaningful.
        bb_width = (indicators['bb_upper'] - indicators['bb_lower']) / indicators['bb_middle'] * 100
        if close < bb_lower:
            if bb_width > 0.1: # Minimum volatility requirement
                confidence += 40
                reasons.append(f"✅ Price below Lower BB (Width: {bb_width:.2f}%)")
            else:
                return self._no_signal("BB Width too thin for counter-trend")
        else:
             return self._no_signal("Price not at BB extreme")

        # 3. Mean Reversion Pulse (Price stability check)
        # Check if we are starting to turn back or at least stopped falling
        if 'open' in df.columns and close > df.iloc[-2]['close']:
            confidence += 10
            reasons.append("✅ Mean Reversion Pulse: Price started turning")

        # SL / TP
        sl_dist = sl_mult * atr 
        tp_dist = tp_mult * atr 
        
        return EntrySignal(
            signal=Signal.BUY if confidence >= self.MIN_CONFIDENCE else Signal.NONE,
            confidence=float(max(0, min(100, confidence))),
            entry_method="SNIPER_COUNTER_BUY",
            entry_price=float(close),
            stop_loss=float(close - sl_dist),
            take_profit=float(close + tp_dist),
            reasons=reasons
        )

    def _check_sell_conditions(self, indicators: Dict, sl_mult: float, tp_mult: float) -> EntrySignal:
        confidence = 0
        reasons = []
        
        close = indicators['close']
        rsi = indicators['rsi']
        bb_upper = indicators['bb_upper']
        atr = indicators['atr']
        
        # 1. RSI Overbought (+40)
        if rsi > self.RSI_OVERBOUGHT:
            confidence += 40
            reasons.append(f"✅ RSI Overbought ({rsi:.1f} > {self.RSI_OVERBOUGHT})")
        else:
            return self._no_signal("RSI not overbought")

        # 2. Bollinger Band Extreme (+40)
        bb_width = (indicators['bb_upper'] - indicators['bb_lower']) / indicators['bb_middle'] * 100
        if close > bb_upper:
            if bb_width > 0.1:
                confidence += 40
                reasons.append(f"✅ Price above Upper BB (Width: {bb_width:.2f}%)")
            else:
                 return self._no_signal("BB Width too thin for counter-trend")
        else:
            return self._no_signal("Price not at BB extreme")

        # 3. Mean Reversion Pulse
        if 'open' in df.columns and close < df.iloc[-2]['close']:
            confidence += 10
            reasons.append("✅ Mean Reversion Pulse: Price started turning down")

        # SL / TP
        sl_dist = sl_mult * atr
        tp_dist = tp_mult * atr
        
        return EntrySignal(
            signal=Signal.SELL if confidence >= self.MIN_CONFIDENCE else Signal.NONE,
            confidence=float(max(0, min(100, confidence))),
            entry_method="SNIPER_COUNTER_SELL",
            entry_price=float(close),
            stop_loss=float(close + sl_dist),
            take_profit=float(close - tp_dist),
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
counter_trend_scalper_strategy = CounterTrendScalperStrategy()
