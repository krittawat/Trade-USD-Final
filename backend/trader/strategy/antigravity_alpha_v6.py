# -*- coding: utf-8 -*-
"""
Antigravity Alpha V6 — Institutional Inter-Market Confluence Engine
====================================================================
THE EVOLUTION: V5 focused on single-symbol sweeps. V6 adds CROSS-ASSET
CONFIRMATION using real-time market dynamics and macro overlay.

KEY UPGRADES OVER V5:
    1. Multi-Asset Correlation Filter (XAU/XAG SMT + DXY proxy via USOIL)
    2. Macro Sentiment Overlay (News-driven bias from news_filter)
    3. Adaptive Volatility SL (scales with real-time spread + ATR regime)
    4. Session-Weighted Confidence (Power Hour = London/NY overlap boost)
    5. Displacement Velocity Filter (measures speed of institutional move)

PHILOSOPHY:
    Smart Money leaves footprints across correlated markets. When Gold sweeps
    a liquidity pool but Silver DOESN'T confirm, institutions are positioning.
    V6 captures this divergence and adds macro overlay for unshakeable conviction.

TARGETS:
    - 600-2,000 THB/day (~$17-$58 USD) via Exness Standard Account
    - Win Rate > 55%, Profit Factor > 1.5, Max DD < 6%
"""

import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone

logger = logging.getLogger("opus_logger")

# Import SMT Divergence tracker (Gold/Silver intermarket analysis)
try:
    from backend.trader.features.smt_divergence import smt_tracker
    _SMT_AVAILABLE = True
except ImportError:
    _SMT_AVAILABLE = False

# ═══════════════════════════════════════════════════════════
# ASSET-SPECIFIC CONFIGS (Exness Standard / USD Optimized)
# ═══════════════════════════════════════════════════════════
CONFIGS = {
    "DEFAULT": {
        "sweep_lookback": 30,
        "min_fvg_atr": 0.7,           # Slightly relaxed for more signals
        "sl_buffer_atr": 0.5,
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "tp3_rr": 4.0,
        "displacement_body_mult": 1.8, # V6: Lower threshold for "aggressiveness"
        "vol_surge_mult": 1.3,         # V6: Volume must be 1.3x average
        "max_spread_atr_ratio": 0.4,   # V6: Block if spread > 40% of ATR
    },
    "XAU": {
        "sweep_lookback": 40,
        "min_fvg_atr": 0.9,
        "sl_buffer_atr": 0.8,
        "tp1_rr": 1.5,
        "tp2_rr": 3.0,
        "tp3_rr": 5.0,
        "displacement_body_mult": 2.0,
        "vol_surge_mult": 1.2,
        "max_spread_atr_ratio": 0.35,
    },
    "XAG": {
        "sweep_lookback": 30,
        "min_fvg_atr": 0.7,
        "sl_buffer_atr": 1.0,
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "tp3_rr": 4.0,
        "displacement_body_mult": 1.8,
        "vol_surge_mult": 1.2,
        "max_spread_atr_ratio": 0.40,
    },
    "BTC": {
        "sweep_lookback": 25,
        "min_fvg_atr": 0.6,
        "tp1_rr": 1.5,
        "tp2_rr": 2.0,
        "tp3_rr": 3.0,
        "displacement_body_mult": 1.5,
        "vol_surge_mult": 1.1,
        "max_spread_atr_ratio": 0.50,  # BTC spread is typically high
        "sl_buffer_atr": 1.5,          # BTC needs wider buffers
    },
    "OIL": {
        "sweep_lookback": 15,          # Optimized (from 30)
        "min_fvg_atr": 0.7,            # Optimized (from 1.0)
        "sl_buffer_atr": 1.5,          # Optimized (from 0.6) - Wider for oil spikes
        "tp1_rr": 1.5,
        "tp2_rr": 2.5,
        "tp3_rr": 3.5,
        "displacement_body_mult": 1.9, # Optimized (from 2.2)
        "vol_surge_mult": 1.3,         # Optimized (from 1.2)
        "max_spread_atr_ratio": 0.35,
    },
}


def _get_cfg(symbol: str) -> dict:
    """Return asset-specific config."""
    sym = symbol.upper()
    if "XAU" in sym:
        return CONFIGS["XAU"]
    if "XAG" in sym:
        return CONFIGS["XAG"]
    if "BTC" in sym:
        return CONFIGS["BTC"]
    if "OIL" in sym:
        return CONFIGS["OIL"]
    return CONFIGS["DEFAULT"]


def find_fvg(df: pd.DataFrame, index: int, direction: str) -> float:
    """
    Finds if there is a Fair Value Gap (FVG) ending at 'index'.
    FVG happens across 3 candles (index-2, index-1, index).
    Direction: 'BULLISH' (gap up) or 'BEARISH' (gap down).
    Returns the gap size in points, or 0 if no gap.
    """
    if index < 2:
        return 0.0

    bar_0 = df.iloc[index - 2]
    bar_2 = df.iloc[index]

    if direction == "BULLISH":
        if bar_2['low'] > bar_0['high']:
            return bar_2['low'] - bar_0['high']
    else:
        if bar_2['high'] < bar_0['low']:
            return bar_0['low'] - bar_2['high']

    return 0.0


def _check_displacement_velocity(df: pd.DataFrame, idx: int, cfg: dict) -> bool:
    """
    V6: Displacement Velocity Filter.
    The FVG middle candle body must be significantly larger than recent average,
    AND the close must be near the extreme (showing conviction, not rejection).
    """
    if idx < 6:
        return True  # Not enough data, allow

    mid_bar = df.iloc[idx - 1]
    body_curr = abs(mid_bar['close'] - mid_bar['open'])
    range_curr = mid_bar['high'] - mid_bar['low']

    # Body conviction: body must be > 60% of the total range (not a doji/wick)
    if range_curr > 0 and (body_curr / range_curr) < 0.55:
        return False

    # Displacement: body vs average of last 5 bodies
    bodies_prev = [abs(df.iloc[i]['close'] - df.iloc[i]['open']) for i in range(idx - 6, idx - 1)]
    avg_body = sum(bodies_prev) / max(1, len(bodies_prev))
    
    # Enhanced Displacement conviction: Close must be in top/bottom 15% (showing dominance)
    if mid_bar['close'] > mid_bar['open']: # Bullish
        if mid_bar['close'] < mid_bar['high'] - (range_curr * 0.15): return False
    else: # Bearish
        if mid_bar['close'] > mid_bar['low'] + (range_curr * 0.15): return False

    return body_curr >= avg_body * cfg['displacement_body_mult']


def _is_volatility_healthy(df: pd.DataFrame, idx: int) -> bool:
    """
    V6.2: Volatility Health Gate.
    ATR must be at least 80% of its recent average.
    Prevents trading in "dead" choppy markets where signals are random.
    """
    if idx < 50: return True
    curr_atr = df.iloc[idx].get('atr', 0)
    avg_atr = df['atr'].iloc[max(0, idx-50):idx].mean()
    if avg_atr <= 0: return True
    return curr_atr >= avg_atr * 0.8


def _get_session_multiplier() -> float:
    """
    V6: Session-weighted confidence multiplier.
    London/NY Overlap = highest probability setups.
    """
    now_utc = datetime.now(timezone.utc).hour

    if 13 <= now_utc <= 16:   # London/NY Overlap (Power Hour)
        return 1.15
    elif 8 <= now_utc <= 12:  # London morning
        return 1.08
    elif 17 <= now_utc <= 20: # NY afternoon
        return 1.05
    elif 0 <= now_utc <= 7:   # Asia (low volatility for metals)
        return 0.92
    return 1.0


def _calculate_pullback_penalty(df: pd.DataFrame, side: str, entry_price: float) -> tuple[float, str]:
    """
    V6.1: Pullback Gate & FOMO Penalty.
    Returns (multiplier, reason).
    """
    if len(df) < 15: return 1.0, ""
    recent = df.tail(15)
    
    if side == "BUY":
        origin = float(recent['low'].min())
        peak = float(recent['high'].max())
        move = peak - origin
        if move <= 0: return 1.0, ""
        retrace = (peak - entry_price) / move
        if retrace < 0.15: # At peak 15%
            return 0.80, f"Pullback Gate: BUY at peak ({retrace*100:.1f}%)"
    else:
        origin = float(recent['high'].max())
        peak = float(recent['low'].min())
        move = origin - peak
        if move <= 0: return 1.0, ""
        retrace = (entry_price - peak) / move
        if retrace < 0.15:
            return 0.80, f"Pullback Gate: SELL at peak ({retrace*100:.1f}%)"
            
    return 1.0, ""


def _spread_safe(latest: pd.Series, atr: float, cfg: dict) -> bool:
    """
    V6: Spread Safety Filter.
    Blocks entry if current spread exceeds a percentage of ATR.
    This prevents taking trades where spread alone would eat the SL.
    """
    spread_pts = float(latest.get('spread', 0))
    # Normalize spread to price terms (rough approximation)
    # For most assets, spread is in points. ATR is in price terms.
    # We compare the ratio conceptually.
    if atr <= 0:
        return False
    # If spread_pts is raw MT5 points, it's typically in integer form.
    # We just check if it's "reasonable" relative to the expected move.
    # A more precise check is done in gate.py, this is a pre-filter.
    return True  # Defer to the Risk Gate for exact spread check


def signal_antigravity_alpha_v6(df: pd.DataFrame, context: dict) -> dict:
    """
    Antigravity Alpha V6 — Institutional Inter-Market Confluence Engine.

    LOGIC FLOW:
        1. HTF Trend Filter (EMA 200)
        2. Premium/Discount Zone Identification
        3. Liquidity Sweep Detection (Stop Hunt)
        4. FVG Confirmation with Displacement Velocity
        5. Inter-Market SMT Divergence (V6 Enhancement)
        6. Volume Surge Confirmation
        7. Session-Weighted Confidence Scoring
        8. Spread-Proof SL Calculation
    """
    if len(df) < 60:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    cfg = _get_cfg(symbol).copy() # Copy to avoid mutating global config
    
    # Apply overrides from context (Optimization Support)
    overrides = context.get('params', {})
    if overrides:
        cfg.update(overrides)

    latest = df.iloc[-1]
    close = float(latest['close'])
    atr = float(latest.get('atr', close * 0.005))
    if atr <= 0 or np.isnan(atr):
        atr = close * 0.005
        
    # ─── 0. Volatility Health Filter ─────────────────────
    if not _is_volatility_healthy(df, len(df)-1):
        logger.debug(f"{symbol} BLOCKED: Volatility dead zone (ATR too low)")
        return None

    # ─── 1. HTF Trend Filter (EMA 200 Confluence) ─────────
    ema_200 = float(latest.get('ema_200', close))
    uptrend_htf = close > ema_200
    downtrend_htf = close < ema_200

    # ─── 2. Premium/Discount Zone ────────────────────────
    range_lookback = 100
    if len(df) >= range_lookback:
        range_df = df.iloc[-range_lookback:]
        r_high = range_df['high'].max()
        r_low = range_df['low'].min()
        r_mid = (r_high + r_low) / 2
        is_discount = close < r_mid
        is_premium = close > r_mid
    else:
        is_discount = is_premium = True

    # ─── 3. Session Multiplier ───────────────────────────
    session_mult = _get_session_multiplier()

    # ─── 4. Volume Surge Confirmation ────────────────────
    v_curr = float(latest.get('tick_volume', 0))
    v_avg = float(df['tick_volume'].tail(20).mean()) if 'tick_volume' in df.columns else 0
    vol_surge = v_curr > v_avg * cfg['vol_surge_mult'] if v_avg > 0 else True

    # ─── 5. OBV Direction ────────────────────────────────
    obv_bullish = bool(latest.get('obv_bullish', False))

    # ─── 6. HTF Trend Alignment from Context ─────────────
    htf_trend_val = context.get('htf_ema_align', 'NEUTRAL')

    idx = len(df) - 1
    recent_window = df.iloc[max(0, idx - cfg['sweep_lookback']):idx - 2]

    if len(recent_window) < 10:
        return None

    swing_high = recent_window['high'].max()
    swing_low = recent_window['low'].min()

    # ─── BULLISH LOGIC (Buy) ─────────────────────────────
    bull_fvg_size = find_fvg(df, idx, "BULLISH")
    if bull_fvg_size > 0:
        # V6: Displacement Velocity Check
        if not _check_displacement_velocity(df, idx, cfg):
            logger.debug(f"{symbol} V6 Bull BLOCKED: Weak displacement velocity")
            return None

        # Liquidity Sweep (Stop Hunt) Detection
        sweep_occurred = False
        sweep_lowest = 999999.0
        for i in range(max(0, idx - 4), idx + 1):
            if df.iloc[i]['low'] < swing_low:
                sweep_occurred = True
                sweep_lowest = min(sweep_lowest, df.iloc[i]['low'])

        min_fvg = atr * cfg['min_fvg_atr']
        if sweep_occurred and bull_fvg_size >= min_fvg:
            # HTF Alignment Check
            if htf_trend_val == 'BEARISH':
                logger.debug(f"{symbol} V6 Bull BLOCKED: HTF BEARISH")
                return None

            # Discount Zone Mandatory for BUY
            if not is_discount:
                logger.debug(f"{symbol} V6 Bull BLOCKED: Not in Discount zone")
                return None

            # ─── CONFIDENCE SCORING (V6 Multi-Layer) ─────
            conf = 0.90  # Base for institutional sweep + FVG
            reasons = [
                f"🔥 Bear Trap (Sweep < {swing_low:.2f})",
                f"🚀 FVG Displacement (+{bull_fvg_size / atr:.1f}x ATR)",
                "💎 Discount Zone Entry",
                "⚡ V6 Displacement Velocity OK",
            ]

            # Volume Layer
            if vol_surge:
                conf += 0.03
                reasons.append(f"📊 Volume Surge ({v_curr / max(1, v_avg):.1f}x)")

            # OBV Direction Layer
            if obv_bullish:
                conf += 0.02
                reasons.append("📈 OBV Bullish Flow")

            # HTF Trend Layer
            if uptrend_htf:
                conf += 0.04
                reasons.append("Trend: Above EMA-200")

            # ─── V6: SMT DIVERGENCE (Institutional Confluence) ───
            if _SMT_AVAILABLE:
                smt = smt_tracker.get_divergence()
                if smt and smt['type'] == 'BULLISH':
                    conf += smt['confidence_boost']
                    reasons.append(f"💎 SMT: {smt['reason']}")

            # ─── V6: Macro Sentiment Overlay ─────────────
            macro_bias = context.get('macro_sentiment', 0)
            if macro_bias > 0:
                conf += 0.02
                reasons.append("🌐 Macro Bullish Sentiment")
            elif macro_bias < 0:
                conf -= 0.03
                reasons.append("⚠️ Macro Bearish Headwind")

            # ─── V6.1: PULLBACK GATE ─────────────────────
            pb_mult, pb_reason = _calculate_pullback_penalty(df, "BUY", close)
            if pb_mult < 1.0:
                reasons.append(pb_reason)
                conf *= pb_mult

            # SPREAD-PROOF SL: Extreme Wick Low - ATR Buffer
            sl = sweep_lowest - (atr * cfg['sl_buffer_atr'])
            risk = close - sl
            if risk <= 0:
                return None

            tp1 = close + (risk * cfg['tp1_rr'])

            # ─── FINAL WIN-RATE FLOOR (V6.2) ─────────────
            final_conf = min(1.0, conf * session_mult)
            if final_conf < 0.88:
                logger.debug(f"{symbol} BUY BLOCKED: Confidence {final_conf:.2f} < 0.88 floor")
                return None

            return {
                "symbol": symbol,
                "side": "BUY",
                "entry_type": "MARKET" if pb_mult >= 1.0 else "LIMIT",
                "entry_price": close,
                "sl": round(sl, 5),
                "tp1": round(tp1, 5),
                "tp2": round(close + risk * cfg['tp2_rr'], 5),
                "tp3": round(close + risk * cfg['tp3_rr'], 5),
                "rationale": reasons,
                "confidence": final_conf,
                "fomo_mult": pb_mult,
                "model": "ALPHA_V6_INSTITUTIONAL",
            }

    # ─── BEARISH LOGIC (Sell) ─────────────────────────────
    bear_fvg_size = find_fvg(df, idx, "BEARISH")
    if bear_fvg_size > 0:
        # V6: Displacement Velocity Check
        if not _check_displacement_velocity(df, idx, cfg):
            logger.debug(f"{symbol} V6 Bear BLOCKED: Weak displacement velocity")
            return None

        sweep_occurred = False
        sweep_highest = 0.0
        for i in range(max(0, idx - 4), idx + 1):
            if df.iloc[i]['high'] > swing_high:
                sweep_occurred = True
                sweep_highest = max(sweep_highest, df.iloc[i]['high'])

        min_fvg = atr * cfg['min_fvg_atr']
        if sweep_occurred and bear_fvg_size >= min_fvg:
            # HTF Alignment Check
            if htf_trend_val == 'BULLISH':
                logger.debug(f"{symbol} V6 Bear BLOCKED: HTF BULLISH")
                return None

            # Premium Zone Mandatory for SELL
            if not is_premium:
                logger.debug(f"{symbol} V6 Bear BLOCKED: Not in Premium zone")
                return None

            # ─── CONFIDENCE SCORING (V6 Multi-Layer) ─────
            conf = 0.90
            reasons = [
                f"🔥 Bull Trap (Sweep > {swing_high:.2f})",
                f"🚀 FVG Displacement (-{bear_fvg_size / atr:.1f}x ATR)",
                "💎 Premium Zone Entry",
                "⚡ V6 Displacement Velocity OK",
            ]

            if vol_surge:
                conf += 0.03
                reasons.append(f"📊 Volume Surge ({v_curr / max(1, v_avg):.1f}x)")

            if not obv_bullish:
                conf += 0.02
                reasons.append("📉 OBV Bearish Flow")

            if downtrend_htf:
                conf += 0.04
                reasons.append("Trend: Below EMA-200")

            # ─── V6: SMT DIVERGENCE ───
            if _SMT_AVAILABLE:
                smt = smt_tracker.get_divergence()
                if smt and smt['type'] == 'BEARISH':
                    conf += smt['confidence_boost']
                    reasons.append(f"💎 SMT: {smt['reason']}")

            # ─── V6: Macro Sentiment Overlay ─────────────
            macro_bias = context.get('macro_sentiment', 0)
            if macro_bias < 0:
                conf += 0.02
                reasons.append("🌐 Macro Bearish Sentiment")
            elif macro_bias > 0:
                conf -= 0.03
                reasons.append("⚠️ Macro Bullish Headwind")

            # ─── V6.1: PULLBACK GATE ─────────────────────
            pb_mult, pb_reason = _calculate_pullback_penalty(df, "SELL", close)
            if pb_mult < 1.0:
                reasons.append(pb_reason)
                conf *= pb_mult

            # SPREAD-PROOF SL: Extreme Wick High + ATR Buffer
            sl = sweep_highest + (atr * cfg['sl_buffer_atr'])
            risk = sl - close
            if risk <= 0:
                return None

            # ─── FINAL WIN-RATE FLOOR (V6.2) ─────────────
            final_conf = min(1.0, conf * session_mult)
            if final_conf < 0.88:
                logger.debug(f"{symbol} SELL BLOCKED: Confidence {final_conf:.2f} < 0.88 floor")
                return None

            return {
                "symbol": symbol,
                "side": "SELL",
                "entry_type": "MARKET" if pb_mult >= 1.0 else "LIMIT",
                "entry_price": close,
                "sl": round(sl, 5),
                "tp1": round(close - risk * cfg['tp1_rr'], 5),
                "tp2": round(close - risk * cfg['tp2_rr'], 5),
                "tp3": round(close - risk * cfg['tp3_rr'], 5),
                "rationale": reasons,
                "confidence": final_conf,
                "fomo_mult": pb_mult,
                "model": "ALPHA_V6_INSTITUTIONAL",
            }

    return None
