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
import math
from backend.trader.config.paths import SETTINGS_PATH
from backend.trader.data.mapper import mapper
from backend.trader.data.fetcher import fetcher
from backend.trader.execution.terminal_guard import log_trade_block
from backend.trader.execution.trailing_policy import (
    base_stop_distance,
    build_trailing_config,
    compute_trailing_stop,
)
from backend.trader.features.volatility import compute_atr
from backend.trader.risk.mt5_history import currency_loss_limit_to_dd_pct

logger = logging.getLogger("opus_logger")

# Load config
with open(SETTINGS_PATH, encoding="utf-8") as _f:
    _CFG = json.load(_f)
    _STRATEGY_CFG = _CFG.get("strategy", {})
    _RISK_CFG = _CFG.get("risk_limits", {})

TRAIL_CONFIG = build_trailing_config(_STRATEGY_CFG.get("trailing_stop", {}))

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


def _normalize_price(value: float, symbol_info) -> float:
    price = float(value or 0.0)
    if symbol_info is None:
        return price
    digits = int(getattr(symbol_info, "digits", 2) or 2)
    tick_size = float(
        getattr(symbol_info, "trade_tick_size", 0.0)
        or getattr(symbol_info, "point", 0.0)
        or 0.0
    )
    if tick_size > 0:
        return float(round(round(price / tick_size) * tick_size, digits))
    return float(round(price, digits))


def _format_price(value: float, symbol_info) -> str:
    normalized = _normalize_price(value, symbol_info)
    digits = int(getattr(symbol_info, "digits", 2) or 2) if symbol_info is not None else 2
    return f"{normalized:.{digits}f}"


def _round_volume_down(value: float, symbol_info) -> float:
    volume = float(value or 0.0)
    if symbol_info is None:
        return max(0.0, round(volume, 2))
    step = float(getattr(symbol_info, "volume_step", 0.01) or 0.01)
    if step <= 0:
        return max(0.0, round(volume, 2))
    rounded = math.floor(volume / step) * step
    return float(round(max(0.0, rounded), 8))


def _resolve_min_sl_move(standard_symbol: str, atr: float, symbol_info, cfg: dict) -> float:
    overrides = (cfg.get("min_sl_move_by_symbol", {}) or {}) if isinstance(cfg, dict) else {}
    override_value = float(overrides.get(standard_symbol, 0.0) or 0.0)

    point = float(getattr(symbol_info, "point", 0.0) or 0.0)
    stops_level = float(getattr(symbol_info, "trade_stops_level", 0.0) or 0.0)
    point_floor = max(point * 5.0, point * stops_level, point)

    if override_value > 0:
        return float(max(point_floor, override_value))

    configured = float(cfg.get("min_sl_move", 0.0) or 0.0) if isinstance(cfg, dict) else 0.0
    atr_scaled = base_stop_distance(atr, cfg) * 0.20 if atr > 0 else 0.0
    candidates = [value for value in (configured, atr_scaled) if value > 0]
    if not candidates:
        return float(point_floor)
    return float(max(point_floor, min(candidates)))


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


def _get_symbol_max_sl_distance(standard_symbol: str, atr: float) -> float:
    abs_caps = _RISK_CFG.get("max_sl_distance_abs", {}) or {}
    atr_caps = _RISK_CFG.get("max_sl_atr_multiplier", {}) or {}

    abs_cap = float(abs_caps.get(standard_symbol, 0.0) or 0.0)
    atr_mult = float(atr_caps.get(standard_symbol, 0.0) or 0.0)

    caps = []
    if abs_cap > 0:
        caps.append(abs_cap)
    if atr > 0 and atr_mult > 0:
        caps.append(atr * atr_mult)
    if not caps:
        return 0.0
    return float(min(caps))


def compute_anti_stophunt_sl(entry: float, side: str, atr: float, symbol_info=None) -> float:
    """
    Ghost Protocol Anti-Stophunt SL:
    Base = 2.5x ATR + 20% ghost buffer = effective ~3x ATR
    Smart money hunts at 1x-2x ATR. We hide SL beyond that zone.
    """
    total_distance = base_stop_distance(atr, TRAIL_CONFIG)
    if side == "BUY":
        return _normalize_price(entry - total_distance, symbol_info)
    return _normalize_price(entry + total_distance, symbol_info)


def compute_atr_tp(entry: float, side: str, atr: float, symbol_info=None) -> dict:
    """
    ATR-based TP levels: TP1=1.5R, TP2=3.0R, TP3=5.0R
    Uses the anti-stophunt SL distance as 1R.
    """
    risk = base_stop_distance(atr, TRAIL_CONFIG)
    if side == "BUY":
        return {
            "tp1": _normalize_price(entry + (risk * 1.5), symbol_info),
            "tp2": _normalize_price(entry + (risk * 3.0), symbol_info),
            "tp3": _normalize_price(entry + (risk * 5.0), symbol_info),
        }
    return {
        "tp1": _normalize_price(entry - (risk * 1.5), symbol_info),
        "tp2": _normalize_price(entry - (risk * 3.0), symbol_info),
        "tp3": _normalize_price(entry - (risk * 5.0), symbol_info),
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
        panic_threshold = float(_CFG.get("hedge_threshold_dd_pct", 15.0) or 15.0)
        loss_limit_currency = float((_RISK_CFG.get("max_daily_loss_currency", 0.0) or 0.0))
        effective_panic_threshold = currency_loss_limit_to_dd_pct(
            loss_limit_currency,
            balance,
            panic_threshold,
        )
        if floating_dd_pct >= effective_panic_threshold:
            logger.critical(
                f"🚨🚨 [PANIC EXIT] DRAWDOWN HIT {floating_dd_pct:.2f}% "
                f"(Threshold: {effective_panic_threshold:.2f}%). CLOSING ALL POSITIONS!"
            )
            from backend.trader.execution.mt5_order import Executor
            temp_ex = Executor(mode="live") # Use live to force real close if in live mode
            
            temp_ex.close_all_positions()
            return 0

    positions = mt5.positions_get()
    if not positions:
        return 0

    blocked_reason = log_trade_block("position management", level="critical")
    if blocked_reason:
        logger.critical(
            f"Position manager skipped for {len(positions)} open position(s): {blocked_reason}"
        )
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
    addon_risk_usd = base_stop_distance(atr, TRAIL_CONFIG) * PYRAMID_ADDON_MAX_R
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

    risk_1r = base_stop_distance(atr, TRAIL_CONFIG)
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
    lot = _round_volume_down(min(1.0, raw_lot), sym_info)
    lot = max(float(sym_info.volume_min or 0.0), lot)

    # SL for add-on = entry of main position (break-even level)
    addon_sl = _normalize_price(pos.price_open, sym_info)

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return

    price = _normalize_price(tick.ask if side == "BUY" else tick.bid, sym_info)
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
            f"Add-on {lot} lot @ {_format_price(price, sym_info)} "
            f"SL={_format_price(addon_sl, sym_info)} | "
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
        
    close_volume = _round_volume_down(current_volume * close_pct, sym_info)
    close_volume = max(float(sym_info.volume_min or 0.0), close_volume)
    
    # If the close volume is practically the entire position, don't partial close or just let it hit TP
    if current_volume - close_volume < sym_info.volume_min:
        return
        
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return
        
    price = _normalize_price(tick.bid if side == "BUY" else tick.ask, sym_info)
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
            f"Closed {close_volume} lot ({close_pct*100:.0f}%) @ {_format_price(price, sym_info)}"
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
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return

    current_price = _normalize_price(tick.bid if side == "BUY" else tick.ask, symbol_info)

    # ═══════════════════════════════════════════════════
    # PHASE 0: Auto-set SL/TP if MISSING (manual orders)
    # ═══════════════════════════════════════════════════
    needs_update = False
    new_sl_init = current_sl
    new_tp_init = current_tp

    # Auto-set SL if missing (SL = 0)
    if current_sl == 0 or current_sl == 0.0:
        new_sl_init = compute_anti_stophunt_sl(entry, side, atr, symbol_info)
        needs_update = True
        logger.info(
            f"🛡️ AUTO-SL #{ticket} {side} {standard_symbol} | "
            f"Entry={_format_price(entry, symbol_info)} → SL={_format_price(new_sl_init, symbol_info)} "
            f"({TRAIL_CONFIG['atr_multiplier'] * (1 + TRAIL_CONFIG['ghost_buffer_pct']):.2f}x ATR)"
        )

    # Auto-set TP if missing (TP = 0)
    if current_tp == 0 or current_tp == 0.0:
        tp_levels = compute_atr_tp(entry, side, atr, symbol_info)
        new_tp_init = tp_levels["tp1"]
        needs_update = True
        logger.info(
            f"🎯 AUTO-TP #{ticket} {side} {standard_symbol} | "
            f"Entry={_format_price(entry, symbol_info)} → "
            f"TP1={_format_price(tp_levels['tp1'], symbol_info)} "
            f"TP2={_format_price(tp_levels['tp2'], symbol_info)} "
            f"TP3={_format_price(tp_levels['tp3'], symbol_info)}"
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
            logger.info(
                f"✅ SL/TP set for #{ticket} — "
                f"SL={_format_price(new_sl_init, symbol_info)} "
                f"TP={_format_price(new_tp_init, symbol_info)}"
            )
            current_sl = new_sl_init
            current_tp = new_tp_init
        else:
            err = result.comment if result else "None"
            logger.warning(f"⚠️ Auto SL/TP failed #{ticket}: {err}")

    # Hard cap existing SL distance by configured abs/ATR rules.
    # This keeps legacy/manual/wide SL from exceeding account safety envelope.
    if current_sl and current_sl > 0:
        max_sl_distance = _get_symbol_max_sl_distance(standard_symbol, atr)
        current_sl_distance = abs(entry - current_sl)
        if max_sl_distance > 0 and current_sl_distance > max_sl_distance:
            capped_sl = (
                _normalize_price(entry - max_sl_distance, symbol_info)
                if side == "BUY"
                else _normalize_price(entry + max_sl_distance, symbol_info)
            )

            if symbol_info:
                point = symbol_info.point or 0.01
                min_stop_dist = (symbol_info.trade_stops_level or 0) * point
                safety_buffer = max(min_stop_dist, point * 5)
                if side == "BUY":
                    capped_sl = _normalize_price(min(capped_sl, current_price - safety_buffer), symbol_info)
                else:
                    capped_sl = _normalize_price(max(capped_sl, current_price + safety_buffer), symbol_info)

            improved = (side == "BUY" and capped_sl > current_sl) or (side == "SELL" and capped_sl < current_sl)
            if improved:
                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": symbol,
                    "position": ticket,
                    "sl": capped_sl,
                    "tp": current_tp,
                }
                result = mt5.order_send(request)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.warning(
                        f"🛡️ [SL CAP LIVE] #{ticket} {side} {standard_symbol} "
                        f"SL {_format_price(current_sl, symbol_info)} -> {_format_price(capped_sl, symbol_info)} "
                        f"(dist {current_sl_distance:.3f} -> {max_sl_distance:.3f})"
                    )
                    current_sl = capped_sl
                else:
                    err = result.comment if result else "None"
                    logger.warning(f"⚠️ Live SL cap failed #{ticket}: {err}")

    # ═══════════════════════════════════════════════════
    # PHASE 1: Trailing Stop & Profit Lock (only when in profit)
    # ═══════════════════════════════════════════════════
    risk_1r = base_stop_distance(atr, TRAIL_CONFIG)  # 1R = full initial SL distance
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

    info = mt5.account_info()
    floating_dd_pct = 0.0
    if info:
        balance = info.balance
        equity = info.equity
        floating_dd_pct = ((balance - equity) / (balance + 1e-9) * 100)
    trailing = compute_trailing_stop(
        entry=entry,
        current_sl=current_sl,
        current_price=current_price,
        side=side,
        atr=atr,
        standard_symbol=standard_symbol,
        floating_dd_pct=floating_dd_pct,
        cfg=TRAIL_CONFIG,
        now_utc=dt.datetime.now(dt.timezone.utc),
    )
    new_sl = _normalize_price(float(trailing["new_sl"]), symbol_info)
    trail_reason = str(trailing.get("reason", "") or "")
    r_multiple = float(trailing.get("r_multiple", r_multiple))

    # Should we move SL?
    if not trail_reason or new_sl == current_sl:
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
    min_sl_move = _resolve_min_sl_move(standard_symbol, atr, symbol_info, TRAIL_CONFIG)
    if sl_move < min_sl_move:
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
            f"R={r_multiple:.1f} | "
            f"SL: {_format_price(current_sl, symbol_info)} → {_format_price(new_sl, symbol_info)} | "
            f"Reason: {trail_reason}"
        )
    else:
        err = result.comment if result else "None"
        logger.warning(f"Trail SL modify failed #{ticket}: {err}")
