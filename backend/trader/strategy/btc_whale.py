# -*- coding: utf-8 -*-
"""
BTC Whale Breakout Strategy (Crypto Intelligence)
Features:
1. Volatility Squeeze Detection (ATR compression / Bollinger Bands tightness)
2. Whale Volume Spike (tick_volume > 2.5x 20-period moving average)
3. Fibo Pullback (61.8 - 78.6%) or Momentum confirmation
4. EMA 200 Trend Filter
5. Generous SL at 2.0x ATR to survive typical crypto sweeps.
"""
import logging
import numpy as np
import pandas as pd
logger = logging.getLogger("btc_whale")


def get_dynamic_atr_multiplier(current_atr: float, baseline_atr: float, base_multiplier: float = 1.5) -> float:
    """Dynamic ATR Multiplier: widen SL when volatile, tighten when quiet."""
    if current_atr <= 0 or baseline_atr <= 0:
        return base_multiplier
    volatility_ratio = current_atr / baseline_atr
    adaptive_ratio = max(0.8, min(1.3, volatility_ratio))
    return round(base_multiplier * adaptive_ratio, 2)

def signal_btc_whale(df: pd.DataFrame, context: dict) -> dict:
    """
    Whale Order Flow Breakout strategy specifically tuned for BTCUSD.
    """
    if len(df) < 205:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    
    # We only want this firing on BTC/Crypto
    if 'BTC' not in symbol.upper() and 'CRYPTO' not in symbol.upper():
        return None
        
    latest = df.iloc[-1]
    
    close = float(latest['close'])
    high = float(latest['high'])
    low = float(latest['low'])
    volume = float(latest.get('tick_volume', 0))
    atr = float(latest.get('atr', 0))
    if atr <= 0 or np.isnan(atr):
        atr = close * 0.005 # Fallback
        
    baseline_atr = float(latest.get('atr_baseline', atr))
    sl_multiplier = get_dynamic_atr_multiplier(atr, baseline_atr, base_multiplier=2.0)

    ema_200 = float(latest.get('ema_200', close))
    
    # ─── 1. Whale Volume Spike Detection ───
    # Look back 20 periods for average volume
    vol_lookback = 20
    avg_vol = df['tick_volume'].iloc[-vol_lookback-1:-1].mean()
    
    # Is current volume highly anomalous? (Whale activity)
    is_whale_volume = volume > (avg_vol * 2.5)
    
    # ─── 2. Volatility Squeeze Detection ───
    # Are we breaking out of a tight range?
    recent_atr_avg = df['atr'].iloc[-vol_lookback-1:-1].mean()
    # A squeeze usually implies ATR was lower than average right before the breakout spike
    was_squeezed = recent_atr_avg < df['atr'].iloc[-vol_lookback*2:-vol_lookback].mean()
    
    # ─── 3. Fibo / Momentum Logic ───
    # Identify the recent breakout direction
    recent_high = df['high'].iloc[-20:-1].max()
    recent_low = df['low'].iloc[-20:-1].min()
    
    price_range = recent_high - recent_low
    fibo_618 = recent_low + (price_range * 0.618)
    fibo_382 = recent_low + (price_range * 0.382)
    
    # Bullish Breakout: strongly closing above recent high with volume
    bull_breakout = close > recent_high and is_whale_volume
    # Bullish Pullback: holding the 61.8% support after a run
    bull_pullback = (close > fibo_618) and (low <= fibo_618) and close > ema_200
    
    # Bearish Breakout: strongly closing below recent low with volume
    bear_breakout = close < recent_low and is_whale_volume
    # Bearish Pullback: holding the 38.2% resistance (which is 61.8 from top)
    bear_pullback = (close < fibo_382) and (high >= fibo_382) and close < ema_200

    # ─── 4. Trend Filter ───
    uptrend = close > ema_200
    downtrend = close < ema_200

    # ─── 5. Scoring System (0-10) ───
    bull_score = 0
    bear_score = 0
    
    if bull_breakout or bull_pullback: bull_score += 4
    if is_whale_volume: bull_score += 2
    if was_squeezed: bull_score += 1
    if uptrend: bull_score += 2
    
    if bear_breakout or bear_pullback: bear_score += 4
    if is_whale_volume: bear_score += 2
    if was_squeezed: bear_score += 1
    if downtrend: bear_score += 2

    # Need high confidence (Score >= 7)
    if bull_score >= 7:
        reasons = [f"BTC Whale Bullish Signal", f"Confidence {bull_score}/10", f"Vol: {volume:.0f}"]
        sl_dist = atr * sl_multiplier  # Crypto requires wider SL + adaptive
        tp_dist = atr * 3.0  # Scale out target
        return _build("BUY", close, sl_dist, tp_dist, symbol, reasons, bull_score / 10.0)

    if bear_score >= 7:
        reasons = [f"BTC Whale Bearish Signal", f"Confidence {bear_score}/10", f"Vol: {volume:.0f}"]
        sl_dist = atr * sl_multiplier
        tp_dist = atr * 3.0
        return _build("SELL", close, sl_dist, tp_dist, symbol, reasons, bear_score / 10.0)

    return None


def _build(side: str, price: float, sl_dist: float, tp_dist: float, symbol: str, reasons: list, confidence: float) -> dict:
    if side == "BUY":
        sl = price - sl_dist
        risk = price - sl
        tp1 = price + risk * 1.5 # Target scale-out at 1.5R
        tp2 = price + tp_dist
        tp3 = price + tp_dist * 2.0
    else:
        sl = price + sl_dist
        risk = sl - price
        tp1 = price - risk * 1.5
        tp2 = price - tp_dist
        tp3 = price - tp_dist * 2.0
        
    return {
        "symbol": symbol, "side": side, "entry_type": "MARKET",
        "entry_price": price, "sl": round(sl, 2),
        "tp1": round(tp1, 2), "tp2": round(tp2, 2), "tp3": round(tp3, 2),
        "rationale": reasons, "confidence": min(1.0, max(0.0, confidence)),
        "model": "BTC_WHALE",
    }
