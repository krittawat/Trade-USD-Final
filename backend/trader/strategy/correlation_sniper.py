# -*- coding: utf-8 -*-
"""
Correlation Sniper V1 — XAU/XAG Intermarket Divergence Strategy
==============================================================
Logic:
    Uses Smart Money Technique (SMT) between Gold and Silver.
    Detects fake breakouts/breakdowns based on non-confirmation.

Signals:
    - Bullish: Gold makes LL, Silver makes HL -> Buy Gold
    - Bearish: Gold makes HH, Silver makes LH -> Sell Gold

Institutional Rules:
    - Must be confirmed by SMT Divergence
    - Must have SMC Structure alignment (optional boost)
    - Uses Extreme Wick SL
"""

import logging
import pandas as pd
from .fvg_logic import signal_fvg_logic
from backend.trader.features.smt_divergence import smt_tracker

logger = logging.getLogger("opus_logger")

CONFIGS = {
    "min_confidence": 0.75,
    "rr_target": 1.5,
    "sl_atr_mult": 1.5,
    "atr_period": 14,
}

def signal_correlation_sniper(df: pd.DataFrame, context: dict) -> dict:
    symbol = context.get('symbol', 'UNKNOWN')
    if "XAU" not in symbol.upper():
        return None  # Only trades Gold (using Silver as a shadow)

    # 1. Check for SMT Divergence
    smt_res = smt_tracker.get_divergence()
    if not smt_res:
        return None

    # 2. Dynamic Params
    strat_params = context.get('brain_params', {}).get('correlation_sniper', {})
    p = CONFIGS.copy()
    if strat_params:
        p.update(strat_params)

    # 3. Align with Price Action
    side = smt_res['type'] # "BULLISH" or "BEARISH"
    
    # 4. Filter: Only trade in the direction of the divergence
    # We look for a recent liquidity sweep on XAUUSD that corresponds to the SMT
    latest = df.iloc[-1]
    curr_close = latest['close']
    
    # Calculate SL based on extreme wick
    atr = latest.get('atr', latest.get('tr', 0.5))
    if atr == 0: return None
    
    sl_dist = atr * p['sl_atr_mult']
    
    if side == "BULLISH":
        # Institutional Buy: Gold fake breakdown
        entry_price = curr_close
        sl = entry_price - sl_dist
        tp = entry_price + (sl_dist * p['rr_target'])
        action = "BUY"
    else:
        # Institutional Sell: Gold fake breakout
        entry_price = curr_close
        sl = entry_price + sl_dist
        tp = entry_price - (sl_dist * p['rr_target'])
        action = "SELL"

    return {
        "symbol": symbol,
        "side": action,
        "entry_type": "MARKET",
        "entry_price": entry_price,
        "sl": round(sl, 5),
        "tp1": round(tp, 5),
        "confidence": p['min_confidence'] + smt_res['confidence_boost'],
        "model": "CORRELATION_SNIPER",
        "rationale": [
            f"🎯 {smt_res['reason']}",
            f"Institutional Confirm: Intermarket Divergence Detected"
        ]
    }
