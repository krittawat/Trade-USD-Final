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
import pandas as pd
import pandas_ta as ta
import logging
from typing import Dict, Any
from .base_strategy import BaseStrategy, StrategyDecision

logger = logging.getLogger("GoldSniperMini")


class GoldSniperMiniStrategy(BaseStrategy):

    RSI_MAX_BUY = 75
    RSI_MIN_SELL = 25
    ADX_MIN_TREND = 25
    MIN_CONFIDENCE = 70
    COOLDOWN_PERIOD = 15  # Minutes between same-direction signals

    def __init__(self):
        self.name = "GOLD_SNIPER_MINI"
        self.params = {}
        self.last_signal_time: Dict[str, int] = {}

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "style": "EMA Cross Sniper",
            "adx_min": self.ADX_MIN_TREND,
        }

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO", **kwargs) -> StrategyDecision:
        if df is None or len(df) < 60:
            return StrategyDecision(signal="NO_TRADE", reason="Insufficient data (min 60)")

        symbol = df.attrs.get('symbol', '').upper()

        # Handle both Timestamp (Live) and numeric (Backtest) time formats
        # 'time' is typically the DatetimeIndex (set in market_data.py), not a column
        if 'time' in df.columns:
            t_val = df.iloc[-1]['time']
        else:
            t_val = df.index[-1]  # time is the index

        if hasattr(t_val, 'timestamp'):
            current_time = int(t_val.timestamp())
        else:
            current_time = int(t_val / 1000) if t_val > 1e10 else int(t_val)

        # Asset-specific configuration
        if "BTC" in symbol:
            sl_mult, tp_mult, adx_min = 3.0, 6.0, 30
        elif "XAG" in symbol:
            sl_mult, tp_mult, adx_min = 2.0, 4.0, 25
        else:  # Default Gold (XAU)
            sl_mult, tp_mult, adx_min = 1.5, 3.0, 25

        # Ensure indicators
        df = self._ensure_indicators(df)
        indicators = self._get_sniper_indicators(df)

        # Check for BUY signal
        if direction in ("AUTO", "BUY", "BOTH"):
            last_buy = self.last_signal_time.get(f"{symbol}_BUY", 0)
            if current_time - last_buy > self.COOLDOWN_PERIOD * 60:
                buy_result = self._check_buy_conditions(indicators, sl_mult, tp_mult, adx_min)
                if buy_result.signal == "BUY":
                    self.last_signal_time[f"{symbol}_BUY"] = current_time
                    return buy_result

        # Check for SELL signal
        if direction in ("AUTO", "SELL", "BOTH"):
            last_sell = self.last_signal_time.get(f"{symbol}_SELL", 0)
            if current_time - last_sell > self.COOLDOWN_PERIOD * 60:
                sell_result = self._check_sell_conditions(indicators, sl_mult, tp_mult, adx_min)
                if sell_result.signal == "SELL":
                    self.last_signal_time[f"{symbol}_SELL"] = current_time
                    return sell_result

        return StrategyDecision(signal="WAIT", reason="Sniper scanning...")

    def _ensure_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate indicators using pandas_ta."""
        if 'EMA_5' not in df.columns:
            df['EMA_5'] = ta.ema(df['close'], length=5)
            df['EMA_13'] = ta.ema(df['close'], length=13)
            df['EMA_50'] = ta.ema(df['close'], length=50)
            df['RSI_14'] = ta.rsi(df['close'], length=14)
            adx = ta.adx(df['high'], df['low'], df['close'], length=14)
            if adx is not None:
                df['ADX_14'] = adx['ADX_14']
            else:
                df['ADX_14'] = 0
            stoch = ta.stoch(df['high'], df['low'], df['close'], k=14, d=3, smooth_k=3)
            if stoch is not None:
                df['STOCHk_14_3_3'] = stoch.iloc[:, 0]
                df['STOCHd_14_3_3'] = stoch.iloc[:, 1]
            else:
                df['STOCHk_14_3_3'] = 0
                df['STOCHd_14_3_3'] = 0
            df['ATR_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)
            # Volume ratio
            vol = df['tick_volume'] if 'tick_volume' in df.columns else df.get('volume', pd.Series([1]*len(df)))
            vol_avg = vol.rolling(20).mean()
            df['Vol_Ratio'] = vol / vol_avg.replace(0, 1)
        return df

    def _get_sniper_indicators(self, df: pd.DataFrame) -> Dict:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        return {
            'close': float(last['close']),
            'open': float(last['open']),
            'ema_5': float(last.get('EMA_5', 0)),
            'ema_13': float(last.get('EMA_13', 0)),
            'ema_5_prev': float(prev.get('EMA_5', 0)),
            'ema_13_prev': float(prev.get('EMA_13', 0)),
            'ema_50': float(last.get('EMA_50', 0)),
            'rsi': float(last.get('RSI_14', 50)),
            'adx': float(last.get('ADX_14', 0)),
            'stoch_k': float(last.get('STOCHk_14_3_3', 0)),
            'stoch_d': float(last.get('STOCHd_14_3_3', 0)),
            'atr': float(last.get('ATR_14', 0)),
            'volume_ratio': float(last.get('Vol_Ratio', 1.0)),
        }

    def _check_buy_conditions(self, ind: Dict, sl_mult: float, tp_mult: float, adx_min: float) -> StrategyDecision:
        confidence = 0
        reasons = []
        close = ind['close']

        # 1. EMA Cross (Primary Signal) (+40)
        if ind['ema_5'] > ind['ema_13'] and ind['ema_5_prev'] <= ind['ema_13_prev']:
            confidence += 40
            reasons.append("EMA 5/13 Cross Up")
        elif ind['ema_5'] > ind['ema_13']:
            confidence += 20
            reasons.append("EMA 5/13 Bullish")
        else:
            return StrategyDecision(signal="WAIT", reason="EMA not bullish")

        # 2. Main Trend Filter (+20)
        if close > ind['ema_50']:
            confidence += 20
            reasons.append("Price > EMA 50")
        else:
            confidence -= 10

        # 3. RSI Momentum (+20)
        rsi = ind['rsi']
        if 50 < rsi < self.RSI_MAX_BUY:
            confidence += 20
            reasons.append(f"RSI {rsi:.0f}")
        elif rsi >= self.RSI_MAX_BUY:
            confidence -= 30
        else:
            confidence -= 20

        # 3.1 Stochastic Confirmation (+15)
        if ind['stoch_k'] > ind['stoch_d'] and ind['stoch_k'] < 80:
            confidence += 15
            reasons.append("Stoch OK")
        elif ind['stoch_k'] >= 80:
            confidence -= 20

        # 4. ADX Trend Strength (+20)
        if ind['adx'] > adx_min:
            confidence += 20
            reasons.append(f"ADX {ind['adx']:.0f}")

        # 5. Bullish candle
        if close > ind['open']:
            confidence += 5

        # Risk Management
        atr = ind['atr']
        sl = close - (sl_mult * atr)
        tp = close + (tp_mult * atr)

        signal = "BUY" if confidence >= self.MIN_CONFIDENCE else "WAIT"
        return StrategyDecision(
            signal=signal,
            entry_price=close,
            sl=sl,
            tp=tp,
            reason=" | ".join(reasons) if reasons else "Scanning...",
            confidence=float(max(0, min(100, confidence))) / 100.0,
        )

    def _check_sell_conditions(self, ind: Dict, sl_mult: float, tp_mult: float, adx_min: float) -> StrategyDecision:
        confidence = 0
        reasons = []
        close = ind['close']

        if ind['ema_5'] < ind['ema_13'] and ind['ema_5_prev'] >= ind['ema_13_prev']:
            confidence += 40
            reasons.append("EMA 5/13 Cross Down")
        elif ind['ema_5'] < ind['ema_13']:
            confidence += 20
            reasons.append("EMA 5/13 Bearish")
        else:
            return StrategyDecision(signal="WAIT", reason="EMA not bearish")

        if close < ind['ema_50']:
            confidence += 20
            reasons.append("Price < EMA 50")
        else:
            confidence -= 10

        rsi = ind['rsi']
        if self.RSI_MIN_SELL < rsi < 50:
            confidence += 20
            reasons.append(f"RSI {rsi:.0f}")
        elif rsi <= self.RSI_MIN_SELL:
            confidence -= 30
        else:
            confidence -= 20

        if ind['stoch_k'] < ind['stoch_d'] and ind['stoch_k'] > 20:
            confidence += 15
            reasons.append("Stoch OK")
        elif ind['stoch_k'] <= 20:
            confidence -= 20

        if ind['adx'] > adx_min:
            confidence += 20
            reasons.append(f"ADX {ind['adx']:.0f}")

        if close < ind['open']:
            confidence += 5

        atr = ind['atr']
        sl = close + (sl_mult * atr)
        tp = close - (tp_mult * atr)

        signal = "SELL" if confidence >= self.MIN_CONFIDENCE else "WAIT"
        return StrategyDecision(
            signal=signal,
            entry_price=close,
            sl=sl,
            tp=tp,
            reason=" | ".join(reasons) if reasons else "Scanning...",
            confidence=float(max(0, min(100, confidence))) / 100.0,
        )


# Global Instance
gold_sniper_mini_strategy = GoldSniperMiniStrategy()
