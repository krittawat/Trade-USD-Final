# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("btc_stop_hunt_v2")

DEFAULT_PARAMS = {
    # Swing Detection
    "swing_lookback_left": 20,
    "swing_lookback_right": 2,  
    
    # Sweep Rules
    "max_sweep_bars": 3,        
    "min_wick_ratio": 0.5,      
    
    # Volume Filter
    "vol_ma": 20,
    "vol_spike_ratio": 1.2,    
    
    # Volatility / Risk
    "atr_period": 14,
    "sl_buffer_atr": 1.0,       
    "min_sl_pct": 0.003,        
    "max_sl_pct": 0.02,         
    
    # Targets
    "rr_standard": 2.0,
    "rr_strong": 2.5,
    
    # Trend alignment
    "ema_trend": 200,
    "require_ema_alignment": True, 
}

SYMBOL_PARAMS = {
    "BTCUSD": DEFAULT_PARAMS.copy(),
    "BTCUSDm": DEFAULT_PARAMS.copy(),
    "BTCUSDc": DEFAULT_PARAMS.copy(),
    "XAUUSD": {
        **DEFAULT_PARAMS,
        "sl_buffer_atr": 0.3,
        "min_wick_ratio": 0.4,
    }
}

def _get_params(symbol: str) -> dict:
    for k, v in SYMBOL_PARAMS.items():
        if k in symbol:
            return v
    return DEFAULT_PARAMS

def signal_btc_stop_hunt_v2(df: pd.DataFrame, context: dict) -> dict:
    """
    BTC Stop Hunt V2
    Detects institutional liquidity sweeps (fake breakouts that reversed).
    """
    symbol = context.get('symbol', 'UNKNOWN')
    p = _get_params(symbol)
    
    min_bars = max(p["ema_trend"], 50)
    if len(df) < min_bars:
        return None
        
    close_ = df['close']
    high = df['high']
    low = df['low']
    open_ = df['open']
    
    current_close = float(close_.iloc[-1])
    current_high = float(high.iloc[-1])
    current_low = float(low.iloc[-1])
    current_open = float(open_.iloc[-1])

    # ─── ATR ───
    tr = pd.concat([
        high - low,
        (high - close_.shift()).abs(),
        (low - close_.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(p["atr_period"]).mean().iloc[-1])
    
    if pd.isna(atr) or atr <= 0:
        return None

    # ─── Volume ───
    vol_col = "tick_volume" if "tick_volume" in df.columns else "volume"
    vol = df[vol_col].astype(float) if vol_col in df.columns else None
    vol_current = float(vol.iloc[-1]) if vol is not None else 0
    vol_avg = float(vol.rolling(p["vol_ma"]).mean().iloc[-1]) if vol is not None else 1
    vol_ratio = vol_current / vol_avg if vol_avg > 0 else 0
    vol_spike = vol_ratio >= p["vol_spike_ratio"]

    # ─── Trend Filter ───
    ema_t_val = float(close_.ewm(span=p["ema_trend"], adjust=False).mean().iloc[-1])

    # ─── Find Swing Highs & Lows ───
    l_len = p["swing_lookback_left"]
    window_size = l_len * 2
    recent_data = df.iloc[-(window_size + 10): -3] # Exclude very recent
    
    if len(recent_data) < l_len:
        return None

    swing_high_idx = recent_data["high"].idxmax()
    swing_low_idx = recent_data["low"].idxmin()
    level_high = float(recent_data.loc[swing_high_idx, "high"])
    level_low = float(recent_data.loc[swing_low_idx, "low"])

    # ─── Sweep Detection ───
    # Wick math
    candle_range = current_high - current_low
    if candle_range <= 0: candle_range = 1e-5
    
    upper_wick = current_high - max(current_open, current_close)
    lower_wick = min(current_open, current_close) - current_low
    
    upper_wick_ratio = upper_wick / candle_range
    lower_wick_ratio = lower_wick / candle_range

    is_bull_trap = False
    is_bear_trap = False

    # Check for SELL setup (Bull Trap above resistance)
    if current_high > level_high and current_close < level_high:
        if upper_wick_ratio >= p["min_wick_ratio"]:
            is_bull_trap = True
            
    # Check for BUY setup (Bear Trap below support)
    if current_low < level_low and current_close > level_low:
        if lower_wick_ratio >= p["min_wick_ratio"]:
            is_bear_trap = True

    # Fallback check previous bar
    if not is_bull_trap and not is_bear_trap:
        prev_high = float(high.iloc[-2])
        prev_low = float(low.iloc[-2])
        prev_close = float(close_.iloc[-2])
        prev_open = float(open_.iloc[-2])
        
        p_range = max(prev_high - prev_low, 1e-5)
        p_uw_ratio = (prev_high - max(prev_open, prev_close)) / p_range
        p_lw_ratio = (min(prev_open, prev_close) - prev_low) / p_range
        
        if prev_high > level_high and prev_close < level_high and current_close < level_high:
            if p_uw_ratio >= p["min_wick_ratio"]:
                is_bull_trap = True
                current_high = prev_high # Use sweep high for SL
        
        if prev_low < level_low and prev_close > level_low and current_close > level_low:
            if p_lw_ratio >= p["min_wick_ratio"]:
                is_bear_trap = True
                current_low = prev_low

    trigger_price = current_close
    sl_price = None
    tp_price = None
    action = None
    conf = 0.0
    reasons = []

    if is_bull_trap:
        if p["require_ema_alignment"] and trigger_price > ema_t_val:
            return None
        action = "SELL"
        conf = 0.75
        reasons.append(f"Swept Res ({level_high:.0f})")
        if vol_spike:
            conf += 0.15
            reasons.append(f"Vol Spike ({vol_ratio:.1f}x)")
        sl_price = current_high + (atr * p["sl_buffer_atr"])
        
    elif is_bear_trap:
        if p["require_ema_alignment"] and trigger_price < ema_t_val:
            return None
        action = "BUY"
        conf = 0.75
        reasons.append(f"Swept Sup ({level_low:.0f})")
        if vol_spike:
            conf += 0.15
            reasons.append(f"Vol Spike ({vol_ratio:.1f}x)")
        sl_price = current_low - (atr * p["sl_buffer_atr"])
        
    else:
        return None

    # Risk Management Rules
    sl_dist = abs(trigger_price - sl_price)
    sl_pct = sl_dist / trigger_price
    
    if sl_pct < p["min_sl_pct"]:
        # Widen SL if too tight
        if action == "BUY":
            sl_price = trigger_price * (1 - p["min_sl_pct"])
        else:
            sl_price = trigger_price * (1 + p["min_sl_pct"])
        sl_dist = abs(trigger_price - sl_price)
        reasons.append("SL widened")
        
    if sl_pct > p["max_sl_pct"]:
        return None # SL too wide
    
    rr = p["rr_strong"] if vol_spike else p["rr_standard"]
    if action == "BUY":
        tp_price = trigger_price + (sl_dist * rr)
    else:
        tp_price = trigger_price - (sl_dist * rr)
        
    conf = min(conf, 0.95)

    return {
        "symbol": symbol,
        "model": "BTC_STOP_HUNT_V2",
        "side": action,
        "entry_type": "MARKET",
        "entry_price": trigger_price,
        "sl": sl_price,
        "tp1": tp_price,
        "tp2": tp_price,
        "confidence": conf,
        "ml_score": conf * 100,
        "risk_reward": rr,
        "rationale": reasons,
        "features": {
            "vol_ratio": round(vol_ratio, 2),
            "level_high": round(level_high, 2),
            "level_low": round(level_low, 2)
        }
    }
