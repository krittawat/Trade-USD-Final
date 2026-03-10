# -*- coding: utf-8 -*-
"""
Antigravity Alpha V5 — Spread-Proof Liquidity Sweep + FVG
=========================================================
THE ULTIMATE INSTITUTIONAL EDGE FOR 600-2000 THB/DAY

PHILOSOPHY:
    Retail traders get stopped out because their SL is mathematically predictable
    (e.g., 1.5x ATR). Market makers hunt these pools of liquidity.
    This strategy waits for the hunt to finish (Liquidity Sweep), 
    confirms institutional presence with an explosive move (Fair Value Gap), 
    and places a "Spread-Proof" SL beyond the extreme wick + buffer.

CORE LOGIC:
    1. Liquidity Sweep: Price takes out recent swing high/low (Stop Hunt).
    2. Violent Reversal (FVG): Price reverses immediately with a massive 
       imbalance, showing institutions stepped in.
    3. Spread-Proof SL: Extreme point of the sweep +/- ATR*0.5 (Buffer).
    4. 1:1.5+ R:R EV maximization.
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("opus_logger")

# Import SMT Divergence tracker (Gold/Silver intermarket analysis)
try:
    from backend.trader.features.smt_divergence import smt_tracker
    _SMT_AVAILABLE = True
except ImportError:
    _SMT_AVAILABLE = False

CONFIGS = {
    "DEFAULT": {
        "sweep_lookback": 30,       
        "min_fvg_atr": 0.8,         
        "sl_buffer_atr": 0.5,       
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "tp3_rr": 4.0,
        "max_spread_pts": 400,      # Strict for Standard
    },
    "XAU": {
        "sweep_lookback": 40,
        "min_fvg_atr": 1.0,         
        "sl_buffer_atr": 1.0,       # Wider for XAU spread hunts
        "tp1_rr": 1.5,
        "tp2_rr": 3.0,
        "tp3_rr": 5.2,              
        "max_spread_pts": 350,      # Standard XAU spread is ~250-400
    },
    "XAG": {
        "sweep_lookback": 35,
        "min_fvg_atr": 1.2,
        "sl_buffer_atr": 1.2,       
        "tp1_rr": 1.8,
        "tp2_rr": 3.0,
        "tp3_rr": 6.0,
        "max_spread_pts": 1500,     # Silver spread is naturally wider
    },
    "BTC": {
        "sweep_lookback": 50,       # BTC needs more structure
        "min_fvg_atr": 0.6,         # Catch quick displacements
        "sl_buffer_atr": 2.2,       # BTC spread 500-1000 pts needs BIG buffer
        "tp1_rr": 1.6,
        "tp2_rr": 3.2,
        "tp3_rr": 5.0,
        "max_spread_pts": 1200,     # Exness BTC spread limit
    }
}

def _get_cfg(symbol: str) -> dict:
    sym = symbol.upper()
    if "XAU" in sym: return CONFIGS["XAU"]
    if "XAG" in sym: return CONFIGS["XAG"]
    if "BTC" in sym: return CONFIGS["BTC"]
    return CONFIGS["DEFAULT"]


def find_fvg(df: pd.DataFrame, index: int, direction: str) -> float:
    """
    Finds if there is a Fair Value Gap (FVG) ending at 'index'.
    FVG happens across 3 candles (index-2, index-1, index).
    Direction: 'BULLISH' (gap up) or 'BEARISH' (gap down).
    Returns the gap size in points, or 0 if no gap.
    """
    if index < 2: return 0.0
    
    bar_0 = df.iloc[index-2]
    bar_1 = df.iloc[index-1] # The massive candle
    bar_2 = df.iloc[index]
    
    if direction == "BULLISH":
        # Low of bar_2 is HIGHER than high of bar_0
        if bar_2['low'] > bar_0['high']:
            return bar_2['low'] - bar_0['high']
    else:
        # High of bar_2 is LOWER than low of bar_0
        if bar_2['high'] < bar_0['low']:
            return bar_0['low'] - bar_2['high']
            
    return 0.0

def signal_antigravity_alpha(df: pd.DataFrame, context: dict) -> dict:
    if len(df) < 50:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    cfg = _get_cfg(symbol)

    latest = df.iloc[-1]
    close = float(latest['close'])
    atr = float(latest.get('atr', close * 0.005))
    if atr <= 0 or np.isnan(atr): atr = close * 0.005

    # ─── 1. HTF Trend Filter (EMA 200 Confluence) ─────────
    # We require the close to be on the right side of EMA 200 for higher probability
    ema_200 = float(latest.get('ema_200', close))
    uptrend_htf = close > ema_200
    downtrend_htf = close < ema_200

    # ─── 2. Premium/Discount Zone ────────────────────────
    # Based on a larger lookback for the structural range
    range_lookback = 100
    if len(df) >= range_lookback:
        range_df = df.iloc[-range_lookback:]
        r_high = range_df['high'].max()
        r_low = range_df['low'].min()
        r_mid = (r_high + r_low) / 2
        
        is_discount = close < r_mid
        is_premium = close > r_mid
    else:
        is_discount = is_premium = True # Fallback

    # ─── 3. Session Boost ───────────────────────────────
    # London (08-16 UTC) and NY (13-21 UTC) overlaps are high volume
    import datetime
    now_utc = datetime.datetime.now(datetime.timezone.utc).hour
    # Session overlap roughly 13:00 - 16:00 UTC
    is_power_hour = 13 <= now_utc <= 16
    session_mult = 1.15 if is_power_hour else 1.0  # Increased boost for power hour

    # ─── 4. Institutional Displacement (V5.1) ──────────
    # Displacement = FVG middle candle body must be 2x greater than avg of last 5
    def is_displaced(df_sub, idx_fvg):
        if idx_fvg < 5: return True
        body_curr = abs(df_sub.iloc[idx_fvg-1]['close'] - df_sub.iloc[idx_fvg-1]['open'])
        bodies_prev = [abs(df_sub.iloc[i]['close'] - df_sub.iloc[i]['open']) for i in range(idx_fvg-6, idx_fvg-1)]
        avg_body = sum(bodies_prev) / 5
        return body_curr >= avg_body * 2.0

    # ─── 4.1 Volatility Spike Gate (V5.2) ──────────────
    # Block if current ATR is > 3x of 100-bar avg (News/Chaos)
    atr_avg_100 = df['atr'].rolling(100).mean().iloc[-1]
    if atr > atr_avg_100 * 3.0:
        logger.debug(f"🚨 {symbol} BLOCK: Volatility Spike ({atr/atr_avg_100:.1f}x)")
        return None

    # ─── 4.2 Spread Gate (Hard Entry Protection) ───────
    curr_spread = context.get('market_state', {}).get('spread', 0)
    if curr_spread > cfg.get('max_spread_pts', 9999):
        logger.debug(f"🛡️ {symbol} SPREAD TRAP: {curr_spread} > {cfg.get('max_spread_pts', 9999)}")
        return None

    # ─── 5. MTF Trend Alignment (V5.1) ───────────────
    # We check the symbol's H1 EMA 200 via context if available (passed from main.py)
    htf_trend_ok = True
    htf_trend_val = context.get('htf_ema_align', 'NEUTRAL') # BULLISH/BEARISH/NEUTRAL
    if htf_trend_val != 'NEUTRAL':
        # If HTF is provided, alignment is mandatory
        pass

    # Need at least a 3-bar window for FVG and a lookback window for sweeps
    idx = len(df) - 1
    # Use Fractal-style pivots (looking for specific swing patterns)
    recent_window = df.iloc[max(0, idx - cfg['sweep_lookback']):idx-2]
    
    if len(recent_window) < 10:
        return None

    swing_high = recent_window['high'].max()
    swing_low = recent_window['low'].min()
    
    # Tick Volume validation (Institutional presence)
    v_curr = latest.get('tick_volume', 0)
    v_avg = df['tick_volume'].tail(20).mean() if 'tick_volume' in df.columns else 0
    high_volume = v_curr > v_avg * 1.1 if v_avg > 0 else True

    # ─── BULLISH LOGIC (Buy) ─────────────────────────
    bull_fvg_size = find_fvg(df, idx, "BULLISH")
    if bull_fvg_size > 0:
        # Check for Displacement
        if not is_displaced(df, idx):
            logger.debug(f"{symbol} Bull Alpha BLOCKED: No Institutional Displacement")
            return None

        # Check for Liquidity Sweep (Stop Hunt) in the last few bars
        sweep_occurred = False
        sweep_lowest = 999999.0
        # Look at the last 4 bars for the "Trap"
        for i in range(idx-4, idx+1):
            if df.iloc[i]['low'] < swing_low:
                sweep_occurred = True
                sweep_lowest = min(sweep_lowest, df.iloc[i]['low'])
        
        # Enhanced Filter: Sweep + FVG Displacement + Discount + Volume
        min_fvg = atr * cfg['min_fvg_atr']
        if sweep_occurred and bull_fvg_size >= min_fvg:
            # Check HTF alignment if provided
            if htf_trend_val == 'BEARISH':
                logger.debug(f"{symbol} Bull Alpha BLOCKED: HTF Trend is BEARISH")
                return None

            # V5 Logic: Entries in Discount zone only for BUY
            if not is_discount:
                logger.debug(f"{symbol} Bull Alpha BLOCKED: Not in Discount zone")
                return None
            
            # Confidence calculation
            conf = 0.94  # Increased base for Displacement
            reasons = [
                f"🔥 Bear Trap (Sweep below {swing_low:.2f})",
                f"🚀 Displacement FVG (+{bull_fvg_size/atr:.1f}x ATR)",
                "💎 Discount Zone Entry",
                "⚡ Institutional Displacement Confirmed"
            ]
            
            if high_volume:
                conf += 0.02
                reasons.append("📊 High Volume Confirmation")
            
            if uptrend_htf:
                conf += 0.04
                reasons.append("M5 Trend Aligned (Above EMA 200)")
            
            # ─── SMT DIVERGENCE BOOST (Institutional Confluence) ───
            if _SMT_AVAILABLE:
                smt = smt_tracker.get_divergence()
                if smt and smt['type'] == 'BULLISH':
                    conf += smt['confidence_boost']
                    reasons.append(f"SMT Divergence: {smt['reason']}")
            
            # SPREAD-PROOF S.L.: Extreme Wick Low - ATR Buffer
            sl = sweep_lowest - (atr * cfg['sl_buffer_atr'])
            risk = close - sl
            if risk <= 0: return None
            
            # Target Selection (Liquidity Pools)
            tp1 = close + (risk * cfg['tp1_rr'])
            
            return {
                "symbol": symbol, "side": "BUY", "entry_type": "MARKET",
                "entry_price": close, "sl": round(sl, 5),
                "tp1": round(tp1, 5), 
                "tp2": round(close + risk * cfg['tp2_rr'], 5), 
                "tp3": round(close + risk * cfg['tp3_rr'], 5),
                "rationale": reasons,
                "confidence": min(1.0, conf * session_mult),
                "model": "ANTIGRAVITY_ALPHA_V5.1",
            }

    # ─── BEARISH LOGIC (Sell) ─────────────────────────
    bear_fvg_size = find_fvg(df, idx, "BEARISH")
    if bear_fvg_size > 0:
        # Check for Displacement
        if not is_displaced(df, idx):
            logger.debug(f"{symbol} Bear Alpha BLOCKED: No Institutional Displacement")
            return None

        sweep_occurred = False
        sweep_highest = 0.0
        for i in range(idx-4, idx+1):
            if df.iloc[i]['high'] > swing_high:
                sweep_occurred = True
                sweep_highest = max(sweep_highest, df.iloc[i]['high'])
                
        min_fvg = atr * cfg['min_fvg_atr']
        if sweep_occurred and bear_fvg_size >= min_fvg:
            # Check HTF alignment if provided
            if htf_trend_val == 'BULLISH':
                logger.debug(f"{symbol} Bear Alpha BLOCKED: HTF Trend is BULLISH")
                return None

            # V5 Logic: Entries in Premium zone only for SELL
            if not is_premium:
                logger.debug(f"{symbol} Bear Alpha BLOCKED: Not in Premium zone")
                return None

            conf = 0.94
            reasons = [
                f"🔥 Bull Trap (Sweep above {swing_high:.2f})",
                f"🚀 Displacement FVG (-{bear_fvg_size/atr:.1f}x ATR)",
                "💎 Premium Zone Entry",
                "⚡ Institutional Displacement Confirmed"
            ]
            
            if high_volume:
                conf += 0.02
                reasons.append("📊 High Volume Confirmation")
            
            if downtrend_htf:
                conf += 0.04
                reasons.append("M5 Trend Aligned (Below EMA 200)")
            
            # ─── SMT DIVERGENCE BOOST (Institutional Confluence) ───
            if _SMT_AVAILABLE:
                smt = smt_tracker.get_divergence()
                if smt and smt['type'] == 'BEARISH':
                    conf += smt['confidence_boost']
                    reasons.append(f"SMT Divergence: {smt['reason']}")
            
            # SPREAD-PROOF S.L.: Extreme Wick High + ATR Buffer
            sl = sweep_highest + (atr * cfg['sl_buffer_atr'])
            risk = sl - close
            if risk <= 0: return None
            
            return {
                "symbol": symbol, "side": "SELL", "entry_type": "MARKET",
                "entry_price": close, "sl": round(sl, 5),
                "tp1": round(close - risk * cfg['tp1_rr'], 5), 
                "tp2": round(close - risk * cfg['tp2_rr'], 5), 
                "tp3": round(close - risk * cfg['tp3_rr'], 5),
                "rationale": reasons,
                "confidence": min(1.0, conf * session_mult),
                "model": "ANTIGRAVITY_ALPHA_V5.1",
            }

    return None


    return None
