# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("btc_elite_v2")

DEFAULT_PARAMS = {
    # EMA
    "ema_fast": 7,
    "ema_mid": 21,
    "ema_slow": 60,
    "ema_trend": 200,

    # ADX
    "adx_period": 10,
    "adx_min": 15,       # Switch to Trend mode if ADX >= 15
    
    # RSI
    "rsi_period": 14,
    "rsi_extreme_high": 85,
    "rsi_extreme_low": 15,

    # Bollinger Bands
    "bb_len": 20,
    "bb_std": 2.0,
    "bb_squeeze_percentile": 25,

    # Volume
    "vol_ma": 20,
    "vol_spike_ratio": 1.3,

    # Risk
    "atr_period": 14,
    "sl_atr_mult": 1.5,
    "sl_buffer_atr": 0.3,
    "min_sl_pct": 0.002,     # 0.2% minimum SL
    
    # Adaptive RR
    "min_score_trade": 60,
    "rr_standard": 1.2,
    "rr_strong": 1.7,
    "rr_elite": 2.0,

    # Volatility filter
    "atr_pct_max": 0.05,     # Block > 5%
    "atr_pct_min": 0.002,    # Block < 0.2%
}

SYMBOL_PARAMS = {
    "BTCUSD": DEFAULT_PARAMS.copy(),
    "BTCUSDm": DEFAULT_PARAMS.copy(),
    "BTCUSDc": DEFAULT_PARAMS.copy(),
}

def _get_params(symbol: str) -> dict:
    for k, v in SYMBOL_PARAMS.items():
        if k in symbol: return v
    return DEFAULT_PARAMS

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

    eps = 1e-10
    di_plus = 100 * plus_dm_s / (atr_s + eps)
    di_minus = 100 * minus_dm_s / (atr_s + eps)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + eps)
    adx = dx.rolling(period).mean()

    return (
        float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0,
        float(di_plus.iloc[-1]) if not pd.isna(di_plus.iloc[-1]) else 0,
        float(di_minus.iloc[-1]) if not pd.isna(di_minus.iloc[-1]) else 0
    )

def _compute_rsi(close, period):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

def _compute_macd(close):
    fast = close.ewm(span=12, adjust=False).mean()
    slow = close.ewm(span=26, adjust=False).mean()
    macd_line = fast - slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    return float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0

def _score_trend(ema_f, ema_m, ema_s, ema_t, close):
    b, s = 0, 0
    br, sr = [], []
    if ema_f > ema_m > ema_s > ema_t:
        b += 15; br.append("L1:Full bull stack")
    elif ema_f > ema_m and close > ema_t:
        b += 8; br.append("L1:Partial bull")
        
    if ema_f < ema_m < ema_s < ema_t:
        s += 15; sr.append("L1:Full bear stack")
    elif ema_f < ema_m and close < ema_t:
        s += 8; sr.append("L1:Partial bear")
        
    if close > ema_s: b += 5; br.append("L1:>EMA50")
    if close < ema_s: s += 5; sr.append("L1:<EMA50")
    
    if ema_f > ema_m: b += 5; br.append("L1:EMA9>21")
    if ema_f < ema_m: s += 5; sr.append("L1:EMA9<21")
    return b, s, br, sr

def _score_momentum(adx, di_plus, di_minus, rsi, macd_hist):
    b, s = 0, 0
    br, sr = [], []
    if adx >= 25:
        if di_plus > di_minus: b += 10; br.append("L2:Strong ADX+")
        else: s += 10; sr.append("L2:Strong ADX-")
    elif adx >= 15:
        if di_plus > di_minus: b += 5; br.append("L2:Weak ADX+")
        else: s += 5; sr.append("L2:Weak ADX-")
        
    if 55 <= rsi <= 75: b += 5; br.append("L2:RSI BullZone")
    if 25 <= rsi <= 45: s += 5; sr.append("L2:RSI BearZone")
    
    if macd_hist > 0: b += 10; br.append("L2:MACD+")
    if macd_hist < 0: s += 10; sr.append("L2:MACD-")
    return b, s, br, sr

def _score_volatility(atr_pct, bb_squeezed, squeeze_released):
    b, s = 0, 0
    br, sr = [], []
    if atr_pct > 0.01:
        v_pts = min(10, int((atr_pct - 0.01) / 0.002))
        b += v_pts; br.append(f"L3:Vol expansion (+{v_pts})")
        s += v_pts; sr.append(f"L3:Vol expansion (+{v_pts})")
        
    if squeeze_released:
        b += 10; br.append("L3:BB Release")
        s += 10; sr.append("L3:BB Release")
    elif bb_squeezed:
        b -= 5; br.append("L3:Squeezing")
        s -= 5; sr.append("L3:Squeezing")
    return b, s, br, sr

def _score_volume(vol_current, vol_avg, vol_spike):
    b, s = 0, 0
    br, sr = [], []
    if vol_spike:
        b += 15; br.append("L4:Vol Spike")
        s += 15; sr.append("L4:Vol Spike")
    elif vol_current > vol_avg:
        b += 5; br.append("L4:Vol>Avg")
        s += 5; sr.append("L4:Vol>Avg")
    return b, s, br, sr

def _heiken_ashi(open_, high, low, close):
    ha_close = (open_ + high + low + close) / 4
    ha_open = [(open_.iloc[0] + close.iloc[0]) / 2]
    for i in range(1, len(close)):
        ha_open.append((ha_open[i-1] + ha_close.iloc[i-1]) / 2)
    ha_open = pd.Series(ha_open, index=close.index)
    return ha_open, ha_close

from app.analysis.structure import detect_structure

def _get_liquidity_levels(df: pd.DataFrame):
    """Get the 24-hour high and low for liquidity awareness."""
    recent_24h = df.iloc[-288:] # Approx 24h of M5 data
    return recent_24h['high'].max(), recent_24h['low'].min()

def signal_btc_elite_v2(df: pd.DataFrame, context: dict) -> dict:
    """
    BTC Elite V2 — SMC + Liquidity Awareness.
    """
    symbol = context.get('symbol', 'UNKNOWN')
    p = _get_params(symbol)
    
    if len(df) < p["ema_trend"] + 50: return None
    
    close = df['close']
    high = df['high']
    low = df['low']
    open_ = df['open']
    
    current_close = float(close.iloc[-1])
    
    # ─── 1. Advanced Structure (SMC) ───
    struct = detect_structure(df)
    if not struct: return None
    
    # ─── 2. Liquidity Awareness ───
    h24, l24 = _get_liquidity_levels(df)
    near_h24 = (h24 - current_close) < (current_close * 0.001) # Near top
    near_l24 = (current_close - l24) < (current_close * 0.001) # Near bottom

    # ─── 3. ATR & Volatility Filter ───
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(p["atr_period"]).mean().iloc[-1])
    if pd.isna(atr) or atr <= 0: return None
    
    atr_pct = atr / current_close if current_close > 0 else 0
    if atr_pct > p["atr_pct_max"] or atr_pct < p["atr_pct_min"]: return None
    
    # ─── 4. Indicators ───
    ema_f = close.ewm(span=p["ema_fast"], adjust=False).mean().iloc[-1]
    ema_m = close.ewm(span=p["ema_mid"], adjust=False).mean().iloc[-1]
    ema_t = close.ewm(span=p["ema_trend"], adjust=False).mean().iloc[-1]
    
    adx_val, di_plus, di_minus = _compute_adx(high, low, close, p["adx_period"])
    rsi_val = _compute_rsi(close, p["rsi_period"])
    macd_hist = _compute_macd(close)
    
    if rsi_val > p["rsi_extreme_high"] or rsi_val < p["rsi_extreme_low"]: return None
    
    # ─── 5. Scoring with SMC ───
    buy_score, sell_score = 0, 0
    buy_r, sell_r = [], []
    
    # Trend Layer (40 pts)
    if struct.get('trend') == "BULLISH":
        buy_score += 25; buy_r.append("SMC: Bullish Structure")
        if current_close > ema_t: buy_score += 15
    elif struct.get('trend') == "BEARISH":
        sell_score += 25; sell_r.append("SMC: Bearish Structure")
        if current_close < ema_t: sell_score += 15
        
    # Momentum Layer (30 pts)
    b2, s2, br2, sr2 = _score_momentum(adx_val, di_plus, di_minus, rsi_val, macd_hist)
    buy_score += b2; sell_score += s2; buy_r.extend(br2); sell_r.extend(sr2)
    
    # Liquidity Layer (30 pts)
    if near_l24 and struct.get('patterns', {}).get('pin_bull'):
        buy_score += 30; buy_r.append("LIQ: Support Swipe + Pin")
    if near_h24 and struct.get('patterns', {}).get('pin_bear'):
        sell_score += 30; sell_r.append("LIQ: Resistance Swipe + Pin")

    # ─── 6. Direction Verification ───
    # HA Filter
    ha_open, ha_close = _heiken_ashi(open_, high, low, close)
    ha_bullish = float(ha_close.iloc[-1]) > float(ha_open.iloc[-1])
    ha_bearish = float(ha_close.iloc[-1]) < float(ha_open.iloc[-1])
    
    if buy_score >= 70 and buy_score > sell_score:
        if not ha_bullish: return None
        total_score, action, reasons = buy_score, "BUY", buy_r
    elif sell_score >= 70 and sell_score > buy_score:
        if not ha_bearish: return None
        total_score, action, reasons = sell_score, "SELL", sell_r
    else:
        return None
        
    if total_score < p["min_score_trade"]: return None
    
    if total_score >= 80: rr = p["rr_elite"]
    elif total_score >= 70: rr = p["rr_strong"]
    else: rr = p["rr_standard"]
    
    conf = min(0.55 + (total_score - 55) * 0.01, 0.95)
    
    # ─── Smart SL / TP ───
    min_sl_dist = current_close * p["min_sl_pct"]
    sl_dist = max(atr * p["sl_atr_mult"], min_sl_dist)
    
    if action == "BUY":
        sl = current_close - sl_dist
        tp = current_close + (sl_dist * rr)
    else:
        sl = current_close + sl_dist
        tp = current_close - (sl_dist * rr)

    return {
        "symbol": symbol,
        "model": "BTC_ELITE_V2",
        "side": action,
        "entry_type": "MARKET",
        "entry_price": current_close,
        "sl": sl,
        "tp1": tp,
        "tp2": tp,
        "confidence": conf,
        "ml_score": conf * 100,
        "risk_reward": rr,
        "rationale": reasons[:6],
        "features": {
            "score": total_score,
            "adx": round(adx_val, 1),
            "rsi": round(rsi_val, 1),
            "atr_pct": round(atr_pct, 4),
        }
    }
