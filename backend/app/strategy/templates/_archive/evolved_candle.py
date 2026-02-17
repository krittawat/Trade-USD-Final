import pandas as pd
import pandas_ta as ta
from app.strategy.base_strategy import StrategyBase, StrategyDecision, SignalType

class EvolvedCandleStrategy(StrategyBase):
    """
    Advanced Strategy: Candlestick Patterns + Indicator Confirmation
    Designed for 'Brutal' efficiency by combining Price Action with Momentum.
    """
    
    def analyze(self, df: pd.DataFrame, direction_mode: str = "AUTO") -> StrategyDecision:
        if df is None or len(df) < 50:
            return StrategyDecision(SignalType.WAIT, 0, "Insufficent Data")

        # 1. Calculate Indicators
        df['ema50'] = ta.ema(df['close'], length=50)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        # MACD
        macd = ta.macd(df['close'])
        df['macd'] = macd['MACD_12_26_9']
        df['macdsignal'] = macd['MACDs_12_26_9']
        
        # Bollinger Bands
        bb = ta.bbands(df['close'], length=20)
        if bb is not None and not bb.empty:
            df['bb_lower'] = bb.iloc[:, 0]
            df['bb_mid'] = bb.iloc[:, 1]
            df['bb_upper'] = bb.iloc[:, 2]
        else:
             df['bb_upper'] = df['close']
             df['bb_lower'] = df['close']

        current = df.iloc[-1]
        prev = df.iloc[-2]
        
        signal = SignalType.WAIT
        confidence = 0
        reason = "Scanning..."
        
        # 2. Identify Candlestick Patterns (Custom Logic without TA-Lib)
        body = abs(current['close'] - current['open'])
        range_val = current['high'] - current['low']
        is_green = current['close'] > current['open']
        is_red = not is_green
        
        prev_body = abs(prev['close'] - prev['open'])
        prev_is_red = prev['close'] < prev['open']
        prev_is_green = not prev_is_red
        
        # Engulfing (Bullish)
        bullish_engulfing = (prev_is_red and is_green and 
                             current['close'] > prev['open'] and 
                             current['open'] < prev['close'])
                             
        # Engulfing (Bearish)
        bearish_engulfing = (prev_is_green and is_red and 
                             current['close'] < prev['open'] and 
                             current['open'] > prev['close'])
                             
        # Hammer (Bullish Reversal)
        bottom_wick = min(current['open'], current['close']) - current['low']
        top_wick = current['high'] - max(current['open'], current['close'])
        is_hammer = (bottom_wick > 2 * body) and (top_wick < 0.5 * body)
        
        # Shooting Star (Bearish Reversal)
        is_shooting_star = (top_wick > 2 * body) and (bottom_wick < 0.5 * body)

        # 3. Combine with Indicators
        
        # --- BUY LOGIC ---
        if bullish_engulfing or (is_hammer and current['rsi'] < 40):
            # Check Confirmation
            if current['close'] > current['ema50']: # Trend Check
                signal = SignalType.BUY
                confidence = 85
                reason = "Bullish Engulfing/Hammer + Uptrend"
            elif current['rsi'] < 30: # Reversal Check
                signal = SignalType.BUY
                confidence = 80
                reason = "Bullish Reversal (Oversold)"
                
        # --- SELL LOGIC ---
        elif bearish_engulfing or (is_shooting_star and current['rsi'] > 60):
             # Check Confirmation
            if current['close'] < current['ema50']: # Trend Check
                signal = SignalType.SELL
                confidence = 85
                reason = "Bearish Engulfing/Star + Downtrend"
            elif current['rsi'] > 70: # Reversal Check
                signal = SignalType.SELL
                confidence = 80
                reason = "Bearish Reversal (Overbought)"

        return StrategyDecision(
            signal=signal,
            confidence=confidence,
            reason=reason,
            stop_loss=current['low'] - current['atr'] if signal == SignalType.BUY else current['high'] + current['atr'],
            take_profit=current['close'] + (current['atr']*2) if signal == SignalType.BUY else current['close'] - (current['atr']*2)
        )

evolved_strategy = EvolvedCandleStrategy()
