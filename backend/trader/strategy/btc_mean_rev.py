# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("btc_mean_rev")

DEFAULT_PARAMS = {
    # Bollinger Bands (wider for BTC volatility)
    "bb_len": 25,
    "bb_std": 2.2,
    
    # RSI (faster for BTC)
    "rsi_period": 10,
    "rsi_ob": 70,
    "rsi_os": 30,
    "rsi_extreme_high": 85,
    "rsi_extreme_low": 15,
    
    # ADX (filter out trends)
    "adx_period": 14,
    "max_adx": 25,
    
    # Risk Management
    "atr_period": 14,
    "sl_atr_mult": 1.5,
    "min_sl_pct": 0.002,     # 0.2% minimum SL
    "max_atr_pct": 0.04,     # Block if ATR > 4% of price (extreme whipsaw)
    
    # Heiken Ashi confirmation
    "require_ha_confirm": True,
}

SYMBOL_PARAMS = {
    "BTCUSD": DEFAULT_PARAMS.copy(),
    "BTCUSDm": DEFAULT_PARAMS.copy(),
    "BTCUSDc": DEFAULT_PARAMS.copy(),
}

def _get_params(symbol: str) -> dict:
    for k, v in SYMBOL_PARAMS.items():
        if k in symbol:
            return v
    return DEFAULT_PARAMS

def _compute_rsi(close, period):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

def _compute_adx(high, low, close, period):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    up_move = high - high.shift()
    down_move = low.shift() - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_s = pd.Series(plus_dm, index=high.index).rolling(period).mean()
    minus_dm_s = pd.Series(minus_dm, index=high.index).rolling(period).mean()
    atr_s = tr.rolling(period).mean()

    di_plus_s = 100 * plus_dm_s / (atr_s + 1e-10)
    di_minus_s = 100 * minus_dm_s / (atr_s + 1e-10)
    dx = 100 * (di_plus_s - di_minus_s).abs() / (di_plus_s + di_minus_s + 1e-10)
    adx_s = dx.rolling(period).mean()

    adx = float(adx_s.iloc[-1]) if not pd.isna(adx_s.iloc[-1]) else 0
    return adx

def _heiken_ashi(open_, high, low, close):
    ha_close = (open_ + high + low + close) / 4
    ha_open = [(open_.iloc[0] + close.iloc[0]) / 2]
    for i in range(1, len(close)):
        ha_open.append((ha_open[i-1] + ha_close.iloc[i-1]) / 2)
    ha_open = pd.Series(ha_open, index=close.index)
    return ha_open, ha_close

def signal_btc_mean_rev(df: pd.DataFrame, context: dict) -> dict:
    """
    BTC Mean-Reversion Strategy — Fade BB extremes in ranging markets.
    """
    symbol = context.get('symbol', 'UNKNOWN')
    
    if len(df) < 50:
        return None
        
    p = _get_params(symbol)
    
    close = df['close']
    high = df['high']
    low = df['low']
    open_ = df['open']
    
    current_close = float(close.iloc[-1])
    
    # ─── ATR ───
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(p['atr_period']).mean().iloc[-1])
    
    if pd.isna(atr) or atr <= 0:
        return None
        
    atr_pct = atr / current_close
    if atr_pct > p['max_atr_pct']:
        return None  # Volatility too high for mean reversion
        
    # ─── Trending Filter (ADX) ───
    adx = _compute_adx(high, low, close, p['adx_period'])
    if adx > p['max_adx']:
        return None  # Market is trending, mean reversion is dangerous
        
    # ─── Bollinger Bands ───
    bb_mid = close.rolling(p['bb_len']).mean()
    bb_std = close.rolling(p['bb_len']).std()
    bb_upper = bb_mid + (p['bb_std'] * bb_std)
    bb_lower = bb_mid - (p['bb_std'] * bb_std)
    
    bb_mid_val = float(bb_mid.iloc[-1])
    bb_upper_val = float(bb_upper.iloc[-1])
    bb_lower_val = float(bb_lower.iloc[-1])
    
    if any(pd.isna(v) for v in [bb_mid_val, bb_upper_val, bb_lower_val]):
        return None
        
    # ─── RSI ───
    rsi = _compute_rsi(close, p['rsi_period'])
    
    if rsi > p['rsi_extreme_high'] or rsi < p['rsi_extreme_low']:
        return None  # Market is in extreme exhaustion, wait for pullback
        
    # ─── Heiken Ashi Filter ───
    ha_open, ha_close = _heiken_ashi(open_, high, low, close)
    ha_bullish = float(ha_close.iloc[-1]) > float(ha_open.iloc[-1])
    ha_bearish = float(ha_close.iloc[-1]) < float(ha_open.iloc[-1])
    
    # ─── Volume ───
    vol_col = "tick_volume" if "tick_volume" in df.columns else "volume"
    vol = df[vol_col].astype(float) if vol_col in df.columns else None
    vol_current = float(vol.iloc[-1]) if vol is not None else 0
    vol_avg = float(vol.rolling(20).mean().iloc[-1]) if vol is not None else 0
    vol_spike = vol_avg > 0 and vol_current > vol_avg * 1.5
    
    # ─── Entry Logic ───
    action = None
    conf = 0.0
    reasons = []
    
    # BUY: close < BB Lower + RSI oversold
    if current_close < bb_lower_val and rsi < p['rsi_os']:
        if p['require_ha_confirm'] and not ha_bullish:
            return None
            
        action = "BUY"
        conf = 0.60
        reasons.append(f"MR BUY close<BB_L RSI={rsi:.1f}")
        
        if rsi < 20: conf += 0.15
        if vol_spike: conf += 0.10
        
    # SELL: close > BB Upper + RSI overbought
    elif current_close > bb_upper_val and rsi > p['rsi_ob']:
        if p['require_ha_confirm'] and not ha_bearish:
            return None
            
        action = "SELL"
        conf = 0.60
        reasons.append(f"MR SELL close>BB_U RSI={rsi:.1f}")
        
        if rsi > 80: conf += 0.15
        if vol_spike: conf += 0.10
        
    if not action:
        return None
        
    conf = min(conf, 0.95)
    
    # ─── SL / TP ───
    min_sl_dist = current_close * p['min_sl_pct']
    sl_dist = max(atr * p['sl_atr_mult'], min_sl_dist)
    
    if action == "BUY":
        sl = current_close - sl_dist
    else:
        sl = current_close + sl_dist
        
    tp = bb_mid_val
    
    risk = abs(current_close - sl)
    reward = abs(tp - current_close)
    rr = reward / risk if risk > 0 else 0
    
    # Require minimum RR 1.0 for Mean Reversion
    if rr < 1.0:
        return None

    return {
        "symbol": symbol,
        "model": "BTC_MEAN_REV",
        "side": action,
        "entry_type": "MARKET",
        "entry_price": current_close,
        "sl": sl,
        "tp1": tp,
        "tp2": tp,  # Mean reversion usually exits entirely at mean
        "confidence": conf,
        "ml_score": conf * 100,
        "risk_reward": rr,
        "rationale": reasons,
        "features": {
            "adx": round(adx, 1),
            "rsi": round(rsi, 1),
            "atr": round(atr, 2),
            "bb_lower": round(bb_lower_val, 2),
            "bb_upper": round(bb_upper_val, 2),
        }
    }
