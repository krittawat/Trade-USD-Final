# -*- coding: utf-8 -*-
"""
Micro-Scalper Strategy ($100 Account Optimization)
==================================================
GOAL: 300 - 600 THB / Day ($10 - $20 USD).
Designed for XAUUSD, XAGUSD, BTCUSD on Exness Standard (1:500 Leverage).

PHILOSOPHY:
    Low absolute profit per trade ($1 - $3) but high win rate (>70%).
    Focuses on M1/M5 short-term Liquidity Sweeps confirmed by FVG.
    Uses tight Spread-Proof SLs and very quick Take Profits.

CORE LOGIC:
    1. Short-term Lookback (15 bars) to find micro-swings.
    2. Liquidity Sweep: Price takes out the swing point.
    3. FVG Confirmation: Needs>0.5x ATR gap (lower threshold than Alpha V5 for frequency).
    4. TP1 at 1.0R to guarantee income for consistent daily target.
    5. Max Risk limited to tight SL.
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("micro_scalper")

CONFIGS = {
    "DEFAULT": {
        "sweep_lookback": 15,       # Micro-swings (Short term)
        "min_fvg_atr": 0.5,         # 0.5x ATR FVG for higher signal frequency
        "sl_buffer_atr": 0.3,       # Tight SL buffer to increase Lot Size capability on $100 account
        "tp1_rr": 1.0,              # TP1 at 1R for fast daily target farming 
        "tp2_rr": 1.5,
        "tp3_rr": 2.5,
    },
    "XAU": {
        "sweep_lookback": 15,
        "min_fvg_atr": 0.6,
        "sl_buffer_atr": 0.5,       # Gold's spread is wider, need slight buffer
        "tp1_rr": 1.0,
        "tp2_rr": 1.5,
        "tp3_rr": 2.5,
    },
    "XAG": {
        "sweep_lookback": 15,
        "min_fvg_atr": 0.6,
        "sl_buffer_atr": 0.5,
        "tp1_rr": 1.0,
        "tp2_rr": 1.5,
        "tp3_rr": 2.5,
    },
    "BTC": {
        "sweep_lookback": 12,
        "min_fvg_atr": 0.5,
        "sl_buffer_atr": 0.8,       # Widened from 0.3 → 0.8 to survive BTC noise sweeps
        "tp1_rr": 1.2,              # Raised from 1.0 to compensate wider SL
        "tp2_rr": 2.0,
        "tp3_rr": 3.0,
    }
}

def _get_cfg(symbol: str) -> dict:
    sym = symbol.upper()
    if "XAU" in sym: return CONFIGS["XAU"]
    if "XAG" in sym: return CONFIGS["XAG"]
    if "BTC" in sym: return CONFIGS["BTC"]
    return CONFIGS["DEFAULT"]


def find_fvg(df: pd.DataFrame, index: int, direction: str) -> float:
    if index < 2: return 0.0
    bar_0 = df.iloc[index-2]
    bar_1 = df.iloc[index-1]
    bar_2 = df.iloc[index]
    
    if direction == "BULLISH":
        if bar_2['low'] > bar_0['high']:
            return bar_2['low'] - bar_0['high']
    else:
        if bar_2['high'] < bar_0['low']:
            return bar_0['low'] - bar_2['high']
    return 0.0

def signal_micro_scalper(df: pd.DataFrame, context: dict) -> dict:
    if len(df) < 50:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    cfg = _get_cfg(symbol)

    latest = df.iloc[-1]
    close = float(latest['close'])
    atr = float(latest.get('atr', close * 0.005))
    if atr <= 0 or np.isnan(atr): atr = close * 0.005

    # ─── 0. HTF Trend Filter ──────────────────────────
    ema_200 = float(latest.get('ema_200', close))
    uptrend_htf = close > ema_200
    downtrend_htf = close < ema_200

    idx = len(df) - 1
    recent_window = df.iloc[max(0, idx - cfg['sweep_lookback']):idx-1]
    
    if len(recent_window) < cfg['sweep_lookback'] - 3:
        return None

    swing_high = recent_window['high'].max()
    swing_low = recent_window['low'].min()

    # ─── BULLISH LOGIC (Buy) ─────────────────────────
    bull_fvg_size = find_fvg(df, idx, "BULLISH")
    if bull_fvg_size > 0:
        sweep_occurred = False
        sweep_lowest = 999999.0
        for i in range(idx-3, idx+1):
            if df.iloc[i]['low'] < swing_low:
                sweep_occurred = True
                sweep_lowest = min(sweep_lowest, df.iloc[i]['low'])
        
        if sweep_occurred and bull_fvg_size >= (atr * cfg['min_fvg_atr']):
            # Scalper: Prefer trend alignment for higher WR
            conf = 0.85
            reasons = [
                f"⚡ Micro-Scalp Sweep (Below {swing_low:.2f})",
                f"🚀 FVG Confirmation (+{bull_fvg_size/atr:.1f}x ATR)"
            ]
            
            if uptrend_htf:
                conf += 0.05
                reasons.append("📈 Trend Aligned")
            else:
                conf -= 0.10 # Reduced confidence for counter-trend scalps
                
            sl = sweep_lowest - (atr * cfg['sl_buffer_atr'])
            risk = close - sl
            if risk <= 0: return None
            
            return {
                "symbol": symbol, "side": "BUY", "entry_type": "MARKET",
                "entry_price": close, "sl": round(sl, 5),
                "tp1": round(close + risk * 0.8, 5), # Ultra-fast TP1 at 0.8R
                "tp2": round(close + risk * 1.2, 5), 
                "tp3": round(close + risk * 2.0, 5),
                "rationale": reasons,
                "confidence": conf,
                "model": "MICRO_SCALPER",
            }


    # ─── BEARISH LOGIC (Sell) ─────────────────────────
    bear_fvg_size = find_fvg(df, idx, "BEARISH")
    if bear_fvg_size > 0:
        sweep_occurred = False
        sweep_highest = 0.0
        for i in range(idx-3, idx+1):
            if df.iloc[i]['high'] > swing_high:
                sweep_occurred = True
                sweep_highest = max(sweep_highest, df.iloc[i]['high'])
                
        if sweep_occurred and bear_fvg_size >= (atr * cfg['min_fvg_atr']):
            conf = 0.85
            reasons = [
                f"⚡ Micro-Scalp Sweep (Above {swing_high:.2f})",
                f"🚀 FVG Confirmation (-{bear_fvg_size/atr:.1f}x ATR)"
            ]

            if downtrend_htf:
                conf += 0.05
                reasons.append("📉 Trend Aligned")
            else:
                conf -= 0.10
                
            sl = sweep_highest + (atr * cfg['sl_buffer_atr'])
            risk = sl - close
            if risk <= 0: return None
            
            return {
                "symbol": symbol, "side": "SELL", "entry_type": "MARKET",
                "entry_price": close, "sl": round(sl, 5),
                "tp1": round(close - risk * 0.8, 5), # Ultra-fast TP1 at 0.8R
                "tp2": round(close - risk * 1.2, 5), 
                "tp3": round(close - risk * 2.0, 5),
                "rationale": reasons,
                "confidence": conf,
                "model": "MICRO_SCALPER",
            }

    return None
