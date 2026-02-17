"""
Sniper Pro Strategy - HTF-Confirmed Trend Pullback

Integrated into Antigravity Framework for Terminal Control.

Key Features:
1. D1 Trend Filter (EMA 50)
2. M15 Trend (EMA 200)
3. RSI Pullback Entry (< 35 for Buy, > 65 for Sell)
4. Candlestick Confirmation (Must have reversal candle)
5. Volatility-Adaptive SL/TP
"""
import pandas as pd
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger("SniperPro")

# Optional MT5 for direct H1 trend fetch (graceful if unavailable)
_mt5 = None
try:
    import MetaTrader5 as _mt5
except ImportError:
    pass

class Signal(Enum):
    NONE = "NONE"
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"


@dataclass
class SniperSignal:
    symbol: str
    signal: Signal
    confidence: float
    entry_method: str
    entry_price: float
    stop_loss: float
    take_profit: float
    tp1: float = 0.0
    tp2: float = 0.0
    reasons: list = field(default_factory=list)

    def is_valid(self) -> bool:
        return self.signal in (Signal.BUY, Signal.SELL) and self.confidence >= 60


class SniperProStrategy:
    """
    Sniper Pro: High-Precision Pullback Entries
    
    RULES:
    - Trade WITH the D1 trend only
    - Enter on M15 RSI pullback
    - Confirm with candlestick pattern
    - Wide SL for volatility, tight management
    """
    
    # Validated Parameters for "Sniper V2" (High Certainty)
    CONFIGS = {
        "DEFAULT": {
            "rsi_buy": 30, "rsi_sell": 70, 
            "stoch_k": 25, "bb_dev": 2.0,
            "adx": 20, 
            "sl_mult": 2.5, "tp_mult": 3.0,
            "min_conf": 75
        },
        "XAU": { # Gold "SNIPER ELITE" (Aggressive Mode)
            "rsi_buy": 25, "rsi_sell": 75,  # Tighter for high precision
            "stoch_k": 20, "bb_dev": 2.2,
            "adx": 25,                      # Must be strong trend
            "sl_mult": 2.0, "tp_mult": 4.0, # Widened SL for Gold volatility (was 1.5)
            "min_conf": 85,                 # Very high certainty required
            "management": {
                "use_trailing_stop": True,  # Enable trailing for winners
                "trailing_atr_mult": 1.5,
                "use_break_even": True,
                "break_even_r": 1.0         # Protect at 1R
            }
        },
        "XAG": { # Silver H1 "Rescue" (Profit +9.61)
            "rsi_buy": 35, "rsi_sell": 65, 
            "stoch_k": 25, "bb_dev": 2.0,
            "adx": 20, 
            "sl_mult": 2.5, "tp_mult": 5.0, # Widened SL for Silver volatility (was 2.0)
            "min_conf": 75,
            "management": {
                "use_trailing_stop": False,
                "use_break_even": True,
                "break_even_r": 0.5 
            }
        },
        "BTC": { # Bitcoin Winner (Back to V3: Profit ~6500)
            "rsi_buy": 35, "rsi_sell": 65, 
            "stoch_k": 20, "bb_dev": 2.0,
            "adx": 20, 
            "sl_mult": 3.5, "tp_mult": 5.0, # Widened SL for BTC volatility (was 2.5)
            "min_conf": 80,
            "management": {
                "use_trailing_stop": False, # Trail cut profit in half
                "use_break_even": False
            }
        }
    }

    HTF_EMA = 50
    LTF_EMA = 200
    VOL_MULT_THRESHOLD = 1.3 # 30% above average volume

    def __init__(self):
        pass


    def _get_actual_h1_trend(self, symbol: str) -> str:
        """Fetch real H1 data for trend confirmation using MT5 (if available)"""
        if _mt5 is None:
            return "UNKNOWN"
        try:
            rates = _mt5.copy_rates_from_pos(symbol, _mt5.TIMEFRAME_H1, 0, 50)
            if rates is None:
                return "UNKNOWN"
            df_h1 = pd.DataFrame(rates)
            ema50 = df_h1['close'].ewm(span=50, adjust=False).mean().iloc[-1]
            curr_price = df_h1['close'].iloc[-1]
            return "UP" if curr_price > ema50 else "DOWN"
        except Exception:
            return "UNKNOWN"

    def _check_volume_spike(self, df: pd.DataFrame) -> bool:
        """Confirm entry with a volume spike"""
        if 'tick_volume' not in df.columns: return True
        avg_vol = df['tick_volume'].rolling(20).mean().iloc[-1]
        curr_vol = df['tick_volume'].iloc[-1]
        return curr_vol >= (avg_vol * self.VOL_MULT_THRESHOLD)

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO", htf_trend: Optional[str] = None) -> SniperSignal:
        """
        Analyze M15 data with D1 Trend + ADX Power Filter.
        """
        if len(df) < 200:
            return self._no_signal("Insufficient data for Sniper Pro (need 200 bars)")
            
        # Select Config
        symbol = df.attrs.get('symbol', 'DEFAULT')
        cfg = self.CONFIGS["DEFAULT"].copy()
        if "XAU" in symbol.upper(): cfg = self.CONFIGS["XAU"].copy()
        elif "XAG" in symbol.upper(): cfg = self.CONFIGS["XAG"].copy()
        elif "BTC" in symbol.upper(): cfg = self.CONFIGS["BTC"].copy()


        # Calculate indicators
        # ... (keep existing indicator logic) ...
        if 'EMA_200' not in df.columns:
            df['EMA_200'] = df['close'].ewm(span=self.LTF_EMA, adjust=False).mean()
        
        # RSI 14
        if 'RSI_14' not in df.columns or df['RSI_14'].isna().all():
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['RSI_14'] = 100 - (100 / (1 + rs))

        # ADX 14 (Trend Strength Filter)
        if 'ADX_14' not in df.columns:
            # Simplified ADX calculation
            plus_dm = df['high'].diff()
            minus_dm = df['low'].diff().abs()
            plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
            minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
            
            tr_adx = pd.concat([
                df['high'] - df['low'], 
                (df['high'] - df['close'].shift()).abs(), 
                (df['low'] - df['close'].shift()).abs()
            ], axis=1).max(axis=1)
            
            atr14 = tr_adx.rolling(14).mean()
            plus_di = 100 * (plus_dm.rolling(14).mean() / atr14)
            minus_di = 100 * (minus_dm.rolling(14).mean() / atr14)
            dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
            df['ADX_14'] = dx.rolling(14).mean()

        last = df.iloc[-1]
        price = last['close']
        ema_200 = last.get('EMA_200', price)
        rsi = last.get('RSI_14', 50)
        adx = last.get('ADX_14', 0)
        
        # Stochastic (14, 3, 3)
        low_14 = df['low'].rolling(14).min()
        high_14 = df['high'].rolling(14).max()
        k_percent = 100 * ((df['close'] - low_14) / (high_14 - low_14))
        stoch_k = k_percent.rolling(3).mean().iloc[-1]
        
        # Bollinger Bands (20, dev)
        bb_sma = df['close'].rolling(20).mean()
        bb_std = df['close'].rolling(20).std()
        bb_upper = (bb_sma + (bb_std * cfg['bb_dev'])).iloc[-1]
        bb_lower = (bb_sma - (bb_std * cfg['bb_dev'])).iloc[-1]

        # Calculate ATR for SL/TP (Always needed)
        tr = pd.concat([
            df['high'] - df['low'], 
            (df['high'] - df['close'].shift()).abs(), 
            (df['low'] - df['close'].shift()).abs()
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(14).mean()
        atr = atr_series.iloc[-1] if not pd.isna(atr_series.iloc[-1]) else price * 0.01
        
        # Session Filter (Day Trading: Focus on Active Hours)
        # Avoid late Asian chop for Gold/Silver
        current_hour = last.name.hour if isinstance(last.name, pd.Timestamp) else 12 
        is_active_session = True
        if 'XAU' in symbol or 'XAG' in symbol:
            # Trade London Open (8) to US Close (21)
            if current_hour < 8 or current_hour > 21:
                is_active_session = False
        
        is_bullish = last['close'] > last['open']
        is_bearish = last['close'] < last['open']

        # Determine LTF Trend (M15)
        ltf_trend = "UP" if price > ema_200 else "DOWN"

        # Determine HTF Trend (Actual H1 or passed trend)
        current_htf_trend = htf_trend if htf_trend else self._get_actual_h1_trend(symbol)

        # ATR for SL/TP
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr_14_val = tr.rolling(window=14).mean().iloc[-1]
        
        if pd.isna(atr_14_val) or atr_14_val == 0:
            atr_14_val = price * 0.005  # Fallback 0.5%

        confidence = 50
        reasons = []
        entry_method = "NONE"

        # Check direction filter
        check_buy = direction in ("AUTO", "BUY", "BOTH")
        check_sell = direction in ("AUTO", "SELL", "BOTH")

        if check_buy and current_htf_trend == "UP" and ltf_trend == "UP":
            # 1. RSI Pullback Check
            if rsi < cfg['rsi_buy']:
                confidence += 20
                reasons.append(f"✅ RSI Pullback ({rsi:.1f} < {cfg['rsi_buy']})")
            
            # 2. Stochastic Check (Precision)
            if stoch_k < cfg['stoch_k']:
                confidence += 20
                reasons.append(f"✅ Stoch Oversold ({stoch_k:.1f})")
            else:
                reasons.append(f"⚠️ Stoch High ({stoch_k:.1f})")
                
            # 3. Bollinger Band Value Check
            if price <= bb_lower * 1.002: # 0.2% tolerance
                confidence += 20
                reasons.append(f"✅ BB Low Touch")
            else:
                 reasons.append(f"⚠️ Price not at Value (Above BB Low)")

            # 4. ADX Trend Strength Check
            if adx > cfg['adx']:
                confidence += 10
                reasons.append(f"✅ Trend Strength > 20")
                
            # 5. Session Check
            if is_active_session:
                confidence += 10
            else:
                confidence -= 20
                reasons.append("⚠️ Low Volatility Session")
                
            # 6. Volume Confirmation (+15)
            if self._check_volume_spike(df):
                confidence += 15
                reasons.append("✅ Volume Spike (Liquidity)")
            else:
                confidence -= 10
                reasons.append("⚠️ Weak Volume")

            if rsi < cfg['rsi_buy'] and confidence >= cfg['min_conf']:
                reasons.append(f"✅ Sniper V3 Setup")
                
                if is_bullish:
                    confidence += 20 # Confirmation Boost
                    reasons.append("✅ Bullish Trigger Candle")
                    entry_method = "SNIPER_ELITE_BUY"
                    
                    sl = price - (atr_14_val * cfg['sl_mult'])
                    tp = price + (atr_14_val * cfg['tp_mult'])
                    tp1 = price + atr_14_val
                    tp2 = price + (atr_14_val * 3)
                    
                    return SniperSignal(
                        symbol=symbol,
                        signal=Signal.BUY if confidence >= cfg['min_conf'] else Signal.WAIT,
                        confidence=float(confidence),
                        entry_method=entry_method,
                        entry_price=float(price),
                        stop_loss=float(sl),
                        take_profit=float(tp),
                        tp1=float(tp1),
                        tp2=float(tp2),
                        reasons=reasons
                    )
                else:
                    reasons.append("⏳ Waiting Bullish Confirmation")
                    return SniperSignal(
                        symbol=symbol,
                        signal=Signal.WAIT,
                        confidence=float(confidence),
                        entry_method="PENDING_CONFIRMATION",
                        entry_price=float(price),
                        stop_loss=0,
                        take_profit=0,
                        tp1=0,
                        tp2=0,
                        reasons=reasons
                    )

        # --- SELL SIGNAL ---
        if check_sell and current_htf_trend == "DOWN" and ltf_trend == "DOWN":
             # 1. RSI Throwback Check
            if rsi > cfg['rsi_sell']:
                confidence += 20
                reasons.append(f"✅ RSI Throwback ({rsi:.1f})")
                
            # 2. Stochastic Check
            if stoch_k > (100 - cfg['stoch_k']):
                confidence += 20
                reasons.append(f"✅ Stoch Overbought ({stoch_k:.1f})")
            else:
                reasons.append(f"⚠️ Stoch Low ({stoch_k:.1f})")

            # 3. Bollinger Band Value Check
            if price >= bb_upper * 0.998: # 0.2% tolerance
                confidence += 20
                reasons.append(f"✅ BB High Touch")
            else:
                reasons.append(f"⚠️ Price not at Value (Below BB High)")
            
            # 4. ADX Check
            if adx > cfg['adx']:
                confidence += 10
                reasons.append(f"✅ Trend Strength > 20")

            # 5. Session
            if not is_active_session:
                confidence -= 20
                reasons.append("⚠️ Low Volatility Session")

            # 6. Volume Confirmation (+15)
            if self._check_volume_spike(df):
                confidence += 15
                reasons.append("✅ Volume Spike (Liquidity)")
            else:
                confidence -= 10
                reasons.append("⚠️ Weak Volume")

            if rsi > cfg['rsi_sell'] and confidence >= cfg['min_conf']:
                 reasons.append(f"✅ Sniper V3 Setup")
            
                 if is_bearish:
                    confidence += 20
                    reasons.append("✅ Bearish Trigger Candle")
                    entry_method = "SNIPER_ELITE_SELL"
                    
                    sl = price + (atr_14_val * cfg['sl_mult'])
                    tp = price - (atr_14_val * cfg['tp_mult'])
                    tp1 = price - atr_14_val
                    tp2 = price - (atr_14_val * 3)
                    
                    return SniperSignal(
                        symbol=symbol,
                        signal=Signal.SELL if confidence >= cfg['min_conf'] else Signal.WAIT,
                        confidence=float(confidence),
                        entry_method=entry_method,
                        entry_price=float(price),
                        stop_loss=float(sl),
                        take_profit=float(tp),
                        tp1=float(tp1),
                        tp2=float(tp2),
                        reasons=reasons
                    )
                 else:
                    reasons.append("⏳ Waiting Bearish Confirmation")
                    # Calculate TP1 and TP2
                    tp1 = price - atr_14_val
                    tp2 = price - (atr_14_val * 3)

                    return SniperSignal(
                        symbol=symbol,
                        signal=Signal.WAIT,
                        confidence=float(confidence),
                        entry_method="PENDING_CONFIRMATION",
                        entry_price=float(price),
                        stop_loss=0,
                        take_profit=0,
                        tp1=float(tp1),
                        tp2=float(tp2),
                        reasons=reasons
                    )
                
        # --- NO SIGNAL / CONFLICT ---
        if current_htf_trend != ltf_trend:
            reasons.append(f"⚠️ Trend Conflict: D1={current_htf_trend}, M15={ltf_trend}")
        elif ltf_trend == "UP" and rsi >= cfg['rsi_buy']:
            reasons.append(f"⏳ Waiting RSI Pullback (Currently {rsi:.1f})")
        elif ltf_trend == "DOWN" and rsi <= cfg['rsi_sell']:
            reasons.append(f"⏳ Waiting RSI Throwback (Currently {rsi:.1f})")
        else:
            reasons.append("Scanning for opportunities...")

        return self._no_signal(reasons[0] if reasons else "No setup", symbol=symbol)

    def _no_signal(self, reason: str, symbol: str = "DEFAULT") -> SniperSignal:
        return SniperSignal(
            symbol=symbol,
            signal=Signal.WAIT,
            confidence=0,
            entry_method="NONE",
            entry_price=0,
            stop_loss=0,
            take_profit=0,
            reasons=[reason]
        )


# Global instance for import
sniper_pro_strategy = SniperProStrategy()
