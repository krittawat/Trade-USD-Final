# -*- coding: utf-8 -*-
"""
OPUS Position Manager — ATR-based Trailing Stop & Profit Locking
Monitors open positions and applies:
1. Anti-Stophunt SL: Places SL beyond ATR swing zones
2. ATR Trailing Stop: Trails SL by ATR distance as price moves favorably
3. Multi-Tier Profit Lock: Locks profit at tiered levels (+1R, +2R, +3R)
"""
import MetaTrader5 as mt5
import logging
from trader.data.mapper import mapper
from trader.data.fetcher import fetcher
from trader.features.volatility import compute_atr

logger = logging.getLogger("opus_logger")

# Trailing Stop Config (dynamic, ATR-based)
TRAIL_CONFIG = {
    "atr_multiplier": 2.5,    # Ghost Protocol: 2.5x ATR base
    "ghost_buffer_pct": 0.20,  # +20% extra buffer on top (total ~3x ATR)
    "activation_r": 1.0,      # Start trailing after +1R profit
    "lock_tiers": [            # Profit lock tiers
        {"r_multiple": 1.0, "lock_pct": 0.3},   # At +1R → lock 30% of profit
        {"r_multiple": 2.0, "lock_pct": 0.5},   # At +2R → lock 50%
        {"r_multiple": 3.0, "lock_pct": 0.7},   # At +3R → lock 70%
    ],
    "min_sl_move": 0.5,       # Min points to move SL (avoid spam)
}


def get_atr_for_symbol(symbol: str, timeframe=None) -> float:
    """Fetch current ATR for a symbol from live data."""
    if timeframe is None:
        timeframe = mt5.TIMEFRAME_M5
    df = fetcher.get_rates(symbol, timeframe, 50)
    if df is None or len(df) < 20:
        return 0
    df = compute_atr(df, period=14)
    atr = df['atr'].iloc[-1]
    return float(atr) if atr == atr else 0  # NaN check


def compute_anti_stophunt_sl(entry: float, side: str, atr: float) -> float:
    """
    Ghost Protocol Anti-Stophunt SL:
    Base = 2.5x ATR + 20% ghost buffer = effective ~3x ATR
    Smart money hunts at 1x-2x ATR. We hide SL beyond that zone.
    """
    mult = TRAIL_CONFIG["atr_multiplier"]
    ghost = TRAIL_CONFIG["ghost_buffer_pct"]
    total_distance = atr * mult * (1 + ghost)  # 2.5 * 1.2 = 3.0x ATR
    if side == "BUY":
        return round(entry - total_distance, 2)
    else:
        return round(entry + total_distance, 2)


def compute_atr_tp(entry: float, side: str, atr: float) -> dict:
    """
    ATR-based TP levels: TP1=1.5R, TP2=3.0R, TP3=5.0R
    Uses the anti-stophunt SL distance as 1R.
    """
    risk = atr * TRAIL_CONFIG["atr_multiplier"]
    if side == "BUY":
        return {
            "tp1": round(entry + (risk * 1.5), 2),
            "tp2": round(entry + (risk * 3.0), 2),
            "tp3": round(entry + (risk * 5.0), 2),
        }
    else:
        return {
            "tp1": round(entry - (risk * 1.5), 2),
            "tp2": round(entry - (risk * 3.0), 2),
            "tp3": round(entry - (risk * 5.0), 2),
        }


def manage_open_positions():
    """
    Main position management loop.
    Scans ALL open positions (bot + manual) and applies:
    - ATR Trailing Stop
    - Multi-Tier Profit Locking
    """
    positions = mt5.positions_get()
    if not positions:
        return

    managed = 0
    for pos in positions:
        try:
            _manage_single_position(pos)
            managed += 1
        except Exception as e:
            logger.error(f"Position manager error for ticket {pos.ticket}: {e}", exc_info=True)

    if managed > 0:
        logger.info(f"🛡️ Position Manager: managing {managed} open position(s)")


def _manage_single_position(pos):
    """Manage trailing stop and profit locking for a single position."""
    symbol = pos.symbol
    standard_symbol = mapper.to_standard(symbol)
    ticket = pos.ticket
    side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
    entry = pos.price_open
    current_sl = pos.sl
    current_tp = pos.tp

    # Get current ATR
    atr = get_atr_for_symbol(standard_symbol)
    if atr <= 0:
        return

    # Get current price
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return

    current_price = tick.bid if side == "BUY" else tick.ask

    # ═══════════════════════════════════════════════════
    # PHASE 0: Auto-set SL/TP if MISSING (manual orders)
    # ═══════════════════════════════════════════════════
    needs_update = False
    new_sl_init = current_sl
    new_tp_init = current_tp

    # Auto-set SL if missing (SL = 0)
    if current_sl == 0 or current_sl == 0.0:
        new_sl_init = compute_anti_stophunt_sl(entry, side, atr)
        needs_update = True
        logger.info(
            f"🛡️ AUTO-SL #{ticket} {side} {standard_symbol} | "
            f"Entry={entry:.2f} → SL={new_sl_init:.2f} (1.2x ATR={atr:.2f})"
        )

    # Auto-set TP if missing (TP = 0)
    if current_tp == 0 or current_tp == 0.0:
        tp_levels = compute_atr_tp(entry, side, atr)
        new_tp_init = tp_levels["tp1"]
        needs_update = True
        logger.info(
            f"🎯 AUTO-TP #{ticket} {side} {standard_symbol} | "
            f"Entry={entry:.2f} → TP1={tp_levels['tp1']:.2f} TP2={tp_levels['tp2']:.2f} TP3={tp_levels['tp3']:.2f}"
        )

    if needs_update:
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": new_sl_init,
            "tp": new_tp_init,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"✅ SL/TP set for #{ticket} — SL={new_sl_init:.2f} TP={new_tp_init:.2f}")
            current_sl = new_sl_init
            current_tp = new_tp_init
        else:
            err = result.comment if result else "None"
            logger.warning(f"⚠️ Auto SL/TP failed #{ticket}: {err}")

    # ═══════════════════════════════════════════════════
    # PHASE 1: Trailing Stop & Profit Lock (only when in profit)
    # ═══════════════════════════════════════════════════
    risk_1r = atr * TRAIL_CONFIG["atr_multiplier"]  # 1R = SL distance
    if risk_1r <= 0:
        return

    if side == "BUY":
        profit_distance = current_price - entry
    else:
        profit_distance = entry - current_price

    r_multiple = profit_distance / risk_1r

    # Calculate new SL based on tier
    new_sl = current_sl
    trail_reason = ""

    # Tier locking: check from highest to lowest
    for tier in reversed(TRAIL_CONFIG["lock_tiers"]):
        if r_multiple >= tier["r_multiple"]:
            locked_profit = profit_distance * tier["lock_pct"]
            if side == "BUY":
                tier_sl = round(entry + locked_profit, 2)
            else:
                tier_sl = round(entry - locked_profit, 2)

            # Only move SL if it improves (never move SL backward)
            if side == "BUY" and tier_sl > new_sl:
                new_sl = tier_sl
                trail_reason = f"Profit Lock +{tier['r_multiple']:.0f}R ({tier['lock_pct']*100:.0f}%)"
            elif side == "SELL" and (tier_sl < new_sl or current_sl == 0):
                new_sl = tier_sl
                trail_reason = f"Profit Lock +{tier['r_multiple']:.0f}R ({tier['lock_pct']*100:.0f}%)"
            break

    # ATR Trailing: if past activation, also check pure ATR trail
    if r_multiple >= TRAIL_CONFIG["activation_r"]:
        if side == "BUY":
            atr_trail_sl = round(current_price - (atr * TRAIL_CONFIG["atr_multiplier"]), 2)
            if atr_trail_sl > new_sl:
                new_sl = atr_trail_sl
                trail_reason = f"ATR Trail ({TRAIL_CONFIG['atr_multiplier']}x ATR)"
        else:
            atr_trail_sl = round(current_price + (atr * TRAIL_CONFIG["atr_multiplier"]), 2)
            if atr_trail_sl < new_sl or current_sl == 0:
                new_sl = atr_trail_sl
                trail_reason = f"ATR Trail ({TRAIL_CONFIG['atr_multiplier']}x ATR)"

    # Should we move SL?
    if new_sl == current_sl:
        return

    # Check minimum movement to avoid spam
    sl_move = abs(new_sl - current_sl)
    if sl_move < TRAIL_CONFIG["min_sl_move"]:
        return

    # Only improve SL (never widen risk)
    if side == "BUY" and new_sl <= current_sl and current_sl > 0:
        return
    if side == "SELL" and new_sl >= current_sl and current_sl > 0:
        return

    # Execute SL modification
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": ticket,
        "sl": new_sl,
        "tp": current_tp,
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(
            f"🔄 TRAIL #{ticket} {side} {standard_symbol} | "
            f"R={r_multiple:.1f} | SL: {current_sl:.2f} → {new_sl:.2f} | "
            f"Reason: {trail_reason}"
        )
    else:
        err = result.comment if result else "None"
        logger.warning(f"Trail SL modify failed #{ticket}: {err}")
