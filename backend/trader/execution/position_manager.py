# -*- coding: utf-8 -*-
"""
OPUS Position Manager V2 — ATR Trailing + Profit Lock + Micro-Pyramid
V2 Changes:
- Micro-Pyramid add-on system with strict risk limits
- Pyramid only when: conf >= 0.80, +0.8R profit, addon risk <= 0.3R, total <= 2%
- Max 1 pyramid per position
"""
import json
import MetaTrader5 as mt5
import logging
import datetime as dt
from datetime import timedelta
from backend.trader.data.mapper import mapper
from backend.trader.data.fetcher import fetcher
from backend.trader.features.volatility import compute_atr

logger = logging.getLogger("opus_logger")

# Load config
with open("d:/VibeCode/Trade/backend/trader/config/settings.json") as _f:
    _CFG = json.load(_f)
    _STRATEGY_CFG = _CFG.get("strategy", {})
    _RISK_CFG = _CFG.get("risk_limits", {})

# Trailing Stop Config (dynamic, ATR-based)
TRAIL_CONFIG = {
    "atr_multiplier": 2.5,    # Ghost Protocol: 2.5x ATR base
    "ghost_buffer_pct": 0.20,  # +20% extra buffer on top (total ~3x ATR)
    "activation_r": 2.0,      # Relaxed: Start trailing after +2.0R profit (was 1.2R — allow breathing room)
    "lock_tiers": [            # Profit lock tiers
        {"r_multiple": 1.2, "lock_pct": 0.4},   # At +1.2R → lock 40% of profit (was 0.8R/50%)
        {"r_multiple": 1.5, "lock_pct": 0.6},   # At +1.5R → lock 60%
        {"r_multiple": 2.0, "lock_pct": 0.85},  # At +2.0R → lock 85%
        {"r_multiple": 3.0, "lock_pct": 0.95},  # At +3.0R → lock 95% (new tier)
    ],
    "min_sl_move": 0.5,       # Min points to move SL (avoid spam)
}

# Micro-Pyramid Config (V2)
PYRAMID_CFG = _STRATEGY_CFG.get("pyramid", {})
PYRAMID_ENABLED = PYRAMID_CFG.get("enabled", False)
PYRAMID_MIN_CONFIDENCE = PYRAMID_CFG.get("min_confidence", 0.80)
PYRAMID_MIN_R_PROFIT = PYRAMID_CFG.get("min_r_in_profit", 0.8)
PYRAMID_ADDON_MAX_R = PYRAMID_CFG.get("addon_max_risk_r", 0.3)
PYRAMID_MAX_TOTAL_RISK_PCT = PYRAMID_CFG.get("max_total_risk_pct", 2.0)
PYRAMID_MAX_PER_POS = PYRAMID_CFG.get("max_pyramids_per_pos", 1)

# Scale-out Config (V2)
SCALE_OUT_CFG = _STRATEGY_CFG.get("scale_out", {})
SCALE_OUT_ENABLED = SCALE_OUT_CFG.get("enabled", True)
SCALE_OUT_R_PROFIT = SCALE_OUT_CFG.get("r_profit", 1.5)
SCALE_OUT_PCT = SCALE_OUT_CFG.get("close_pct", 0.3) # Partial close 30%

# Track pyramid count per ticket: {ticket_id: count}
_pyramid_count: dict = {}

# Track partial close per ticket: {ticket_id: bool}
_partial_close_done: dict = {}


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


def manage_open_positions() -> int:
    """
    Main position management loop.
    Scans ALL open positions (bot + manual) and applies:
    - Emergency Account Protection (Hard Exit at 25% DD)
    - ATR Trailing Stop
    - Multi-Tier Profit Locking
    - Micro-Pyramid Add-On (V2)
    Returns: number of positions managed.
    """
    info = mt5.account_info()
    if info:
        balance = info.balance
        equity = info.equity
        floating_dd_pct = ((balance - equity) / balance * 100) if balance > 0 else 0
        
        # 🚨 [EMERGENCY] Panic Exit Protocol
        # If drawdown hits the critical threshold (e.g. 15%), close ALL to save capital
        panic_threshold = _CFG.get("hedge_threshold_dd_pct", 15.0)
        if floating_dd_pct >= panic_threshold:
            logger.critical(f"🚨🚨 [PANIC EXIT] DRAWDOWN HIT {floating_dd_pct:.2f}% (Threshold: {panic_threshold}%). CLOSING ALL POSITIONS!")
            from backend.trader.execution.mt5_order import Executor
            temp_ex = Executor(mode="live") # Use live to force real close if in live mode
            
            positions = mt5.positions_get()
            if positions:
                for p in positions:
                    temp_ex.close_symbol_positions(p.symbol)
            return 0

    positions = mt5.positions_get()
    if not positions:
        return 0

    managed = 0
    for pos in positions:
        try:
            _manage_single_position(pos)
            managed += 1
        except Exception as e:
            logger.error(f"Position #{pos.ticket}: {e}", exc_info=True)

    return managed


def _should_pyramid(pos, r_multiple: float, atr: float) -> bool:
    """
    Check if we should add a micro-pyramid position.
    Requirements (ALL must be true):
    1. Pyramid feature is enabled
    2. Position is in profit >= 0.8R
    3. Max pyramid count not reached for this ticket
    4. Total account risk still <= 2%
    """
    if not PYRAMID_ENABLED:
        return False

    ticket = pos.ticket
    current_pyramids = _pyramid_count.get(ticket, 0)

    # Max pyramids per position
    if current_pyramids >= PYRAMID_MAX_PER_POS:
        return False

    # Must be in sufficient profit
    if r_multiple < PYRAMID_MIN_R_PROFIT:
        return False

    # Check account equity risk
    info = mt5.account_info()
    if info is None:
        return False

    equity = info.equity
    # Calculate current total risk across all positions
    all_positions = mt5.positions_get()
    total_risk_usd = 0.0
    if all_positions:
        for p in all_positions:
            if p.sl > 0:
                sl_dist = abs(p.price_open - p.sl)
                sym_info = mt5.symbol_info(p.symbol)
                if sym_info:
                    pos_risk = sl_dist * p.volume * sym_info.trade_contract_size
                    total_risk_usd += pos_risk

    # Add-on risk = 0.3R
    addon_risk_usd = atr * TRAIL_CONFIG["atr_multiplier"] * PYRAMID_ADDON_MAX_R
    new_total_risk_pct = (total_risk_usd + addon_risk_usd) / (equity + 1e-9) * 100

    if new_total_risk_pct > PYRAMID_MAX_TOTAL_RISK_PCT:
        logger.debug(
            f"Pyramid blocked: total risk would be {new_total_risk_pct:.2f}% > {PYRAMID_MAX_TOTAL_RISK_PCT}%"
        )
        return False

    return True


def _execute_pyramid(pos, atr: float):
    """Execute a micro-pyramid add-on order."""
    global _pyramid_count
    ticket = pos.ticket
    symbol = pos.symbol
    side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"

    # Calculate add-on lot size (risk = 0.3R)
    info = mt5.account_info()
    if info is None:
        return

    risk_1r = atr * TRAIL_CONFIG["atr_multiplier"]
    addon_risk_distance = risk_1r * PYRAMID_ADDON_MAX_R  # 0.3R SL distance

    if addon_risk_distance <= 0:
        return

    # Lot = (equity * risk_pct / 100) / (sl_distance * contract_size)
    sym_info = mt5.symbol_info(symbol)
    if sym_info is None:
        return

    # Use small fraction of equity for add-on
    addon_risk_usd = info.equity * (PYRAMID_ADDON_MAX_R / 100.0)
    raw_lot = addon_risk_usd / (addon_risk_distance * sym_info.trade_contract_size + 1e-9)
    lot = max(sym_info.volume_min, min(1.0, round(raw_lot, 2)))

    # SL for add-on = entry of main position (break-even level)
    addon_sl = pos.price_open

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return

    price = tick.ask if side == "BUY" else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": addon_sl,
        "tp": pos.tp,  # Same TP as main
        "deviation": 20,
        "magic": 777001,  # Different magic for pyramid orders
        "comment": f"PYRAMID_{ticket}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        _pyramid_count[ticket] = _pyramid_count.get(ticket, 0) + 1
        standard_symbol = mapper.to_standard(symbol)
        logger.info(
            f"🔺 PYRAMID #{ticket} {side} {standard_symbol} | "
            f"Add-on {lot} lot @ {price:.2f} SL={addon_sl:.2f} | "
            f"Pyramids: {_pyramid_count[ticket]}/{PYRAMID_MAX_PER_POS}"
        )
    else:
        err = result.comment if result else "None"
        logger.warning(f"⚠️ Pyramid order failed #{ticket}: {err}")


def _execute_partial_close(pos, close_pct: float):
    """Execute a partial close (scale-out) for a given position."""
    global _partial_close_done
    ticket = pos.ticket
    symbol = pos.symbol
    side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
    
    current_volume = pos.volume
    sym_info = mt5.symbol_info(symbol)
    if sym_info is None:
        return
        
    close_volume = max(sym_info.volume_min, round(current_volume * close_pct, 2))
    
    # If the close volume is practically the entire position, don't partial close or just let it hit TP
    if current_volume - close_volume < sym_info.volume_min:
        return
        
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return
        
    price = tick.bid if side == "BUY" else tick.ask
    order_type = mt5.ORDER_TYPE_SELL if side == "BUY" else mt5.ORDER_TYPE_BUY
    
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": close_volume,
        "type": order_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": pos.magic,
        "comment": f"SCALE_{close_pct*100:.0f}%",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        _partial_close_done[ticket] = True
        logger.info(
            f"💰 SCALE-OUT #{ticket} {side} {symbol} | "
            f"Closed {close_volume} lot ({close_pct*100:.0f}%) @ {price:.2f}"
        )
    else:
        err = result.comment if result else "None"
        logger.warning(f"⚠️ Scale-out failed #{ticket}: {err}")


def _manage_single_position(pos):
    """Manage trailing stop, profit locking, and micro-pyramid for a single position."""
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

    # ═══════════════════════════════════════════════════
    # PHASE 1.1: Scale-Out (Partial Close) at +1.5R
    # ═══════════════════════════════════════════════════
    if SCALE_OUT_ENABLED and r_multiple >= SCALE_OUT_R_PROFIT and not _partial_close_done.get(ticket, False):
        _execute_partial_close(pos, SCALE_OUT_PCT)

    # ═══════════════════════════════════════════════════
    # PHASE 1.5: Micro-Pyramid Check (V2)
    # ═══════════════════════════════════════════════════
    if _should_pyramid(pos, r_multiple, atr):
        _execute_pyramid(pos, atr)

    # ═══════════════════════════════════════════════════
    # PHASE 1.6: Trailing stop initialization
    # ═══════════════════════════════════════════════════
    new_sl = current_sl
    trail_reason = ""

    # --- STEALTH / DEFENSIVE OVERRIDE ---
    # If the account is under stress (DD > 15%), we move to BE at just 0.5R to protect capital
    info = mt5.account_info()
    if info:
        balance = info.balance
        equity = info.equity
        floating_dd_pct = ((balance - equity) / (balance + 1e-9) * 100)
        
        activation_r = TRAIL_CONFIG["activation_r"]
        
        # --- Monday Open Sensitivity for XAU ---
        now_th = dt.datetime.now(dt.timezone.utc) + timedelta(hours=7)
        is_monday_morning = now_th.weekday() == 0 and now_th.hour < 10
        if is_monday_morning and "XAU" in standard_symbol.upper():
            activation_r = min(activation_r, 0.8)
            logger.debug(f"💎 XAU High-Sensitivity Mode: Reducing activation R to {activation_r}")

        # --- STEALTH BE (Low Drawdown Recovery) ---
        # Very aggressive break-even to protect capital when DD > 15%
        if floating_dd_pct > 15.0:
            activation_r = min(activation_r, 0.2) # Move to BE almost immediately when in slight profit
            logger.debug(f"🛡️ RECOVERY STEALTH BE ACTIVE (DD={floating_dd_pct:.1f}%): Activation R at {activation_r}")

        if r_multiple >= activation_r:
            if side == "BUY" and entry > new_sl:
                new_sl = entry
                trail_reason = f"Stealth Break-Even (+{activation_r}R)"
            elif side == "SELL" and (entry < new_sl or current_sl == 0):
                new_sl = entry
                trail_reason = f"Stealth Break-Even (+{activation_r}R)"

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
        # Tighter ATR Trail in Recovery Mode
        trail_mult = 1.5 if floating_dd_pct > 15.0 else TRAIL_CONFIG["atr_multiplier"]
        
        if side == "BUY":
            atr_trail_sl = round(current_price - (atr * trail_mult), 2)
            if atr_trail_sl > new_sl:
                new_sl = atr_trail_sl
                trail_reason = f"Aggressive ATR Trail ({trail_mult}x ATR)" if floating_dd_pct > 15.0 else f"ATR Trail ({trail_mult}x ATR)"
        else:
            atr_trail_sl = round(current_price + (atr * trail_mult), 2)
            if atr_trail_sl < new_sl or current_sl == 0:
                new_sl = atr_trail_sl
                trail_reason = f"Aggressive ATR Trail ({trail_mult}x ATR)" if floating_dd_pct > 15.0 else f"ATR Trail ({trail_mult}x ATR)"

    # Should we move SL?
    if new_sl == current_sl:
        return

    # 🛡️ [SPREAD GUARD] Don't modify during spikes (prevents rejections)
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info:
        spread = symbol_info.spread
        max_spread = _CFG.get("risk_limits", {}).get("max_spread_points", {}).get(standard_symbol, 1000)
        if spread > max_spread * 3.0:
            logger.warning(f"🛡️ [SPREAD GUARD] Spread {spread} too wide for {symbol}. Skipping SL update.")
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
