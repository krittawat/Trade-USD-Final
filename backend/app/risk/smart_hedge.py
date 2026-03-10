"""
Smart Hedge — Defensive Hedging for Losing Positions.

Rules:
    1. Only hedge when existing position is losing > 1R (SL distance)
    2. Hedge lot = 50% of original position
    3. Hedge TP = tight (0.5 ATR) — quick profit to offset loss
    4. Hedge SL = 1.5 ATR (protect hedge itself)
    5. Max 1 hedge per original position
    6. Cooldown: min 5 minutes between hedges per symbol
    7. Auto-close hedge when original position returns to breakeven

NOT the same as counter-trend trading:
    - Counter-trend = new speculative trade against trend (disabled)
    - Smart Hedge = defensive protection for existing losing position
"""

import asyncio
import time
from typing import Dict, Optional

import MetaTrader5 as mt5
import numpy as np

from app.core.logging import get_logger
from app.mt5.client import MT5Client
from app.core.config import Settings

logger = get_logger(__name__)

# ═══════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════
HEDGE_LOT_RATIO = 0.5        # Hedge lot = 50% of original
HEDGE_TP_ATR_MULT = 0.5      # Tight TP = 0.5 ATR
HEDGE_SL_ATR_MULT = 1.5      # Protect hedge = 1.5 ATR
HEDGE_COOLDOWN_SEC = 300      # 5-minute cooldown per symbol
MIN_LOSS_R_TO_HEDGE = 1.0     # Only hedge when loss > 1R (SL distance)
MAX_HEDGES_PER_SYMBOL = 1     # Max 1 simultaneous hedge per symbol
HEDGE_MAGIC = 888888          # Magic number to identify hedge orders


class SmartHedge:
    """
    Smart Hedge — opens defensive opposite position when main trade
    is losing > 1R. Uses tight TP for quick recovery profit.
    """

    def __init__(self, mt5_client: MT5Client, settings: Settings):
        self.mt5 = mt5_client
        self.settings = settings
        self._last_hedge_time: Dict[str, float] = {}
        self._active_hedges: Dict[int, int] = {}  # main_ticket -> hedge_ticket

    async def check_and_hedge(self, positions: list[dict]) -> None:
        """
        Scan all open positions. If any is losing > 1R, open a hedge.
        Also auto-close hedge if main recovers to breakeven.
        """
        if not self.mt5 or not self.mt5.is_connected():
            return

        # Get all MT5 positions
        mt5_positions = mt5.positions_get()
        if not mt5_positions:
            return

        # Identify existing hedges (magic=888888)
        hedge_tickets = set()
        for p in mt5_positions:
            if p.magic == HEDGE_MAGIC:
                hedge_tickets.add(p.ticket)

        # Count active hedges per symbol
        hedge_count_by_symbol: Dict[str, int] = {}
        for p in mt5_positions:
            if p.magic == HEDGE_MAGIC:
                sym = p.symbol
                hedge_count_by_symbol[sym] = hedge_count_by_symbol.get(sym, 0) + 1

        # Check main positions (non-hedge)
        for pos in mt5_positions:
            if pos.magic == HEDGE_MAGIC:
                continue  # Skip hedge positions themselves

            symbol = pos.symbol
            ticket = pos.ticket

            # --- Auto-close hedge if main recovered ---
            if ticket in self._active_hedges:
                hedge_ticket = self._active_hedges[ticket]
                # Check if main position is now profitable or at breakeven
                if pos.profit >= 0:
                    await self._close_hedge(hedge_ticket, symbol, reason="main_recovered")
                    del self._active_hedges[ticket]
                    continue
                # Check if hedge was already closed
                hedge_exists = any(p.ticket == hedge_ticket for p in mt5_positions)
                if not hedge_exists:
                    del self._active_hedges[ticket]

            # --- Check if position needs hedging ---
            # Already has a hedge?
            if ticket in self._active_hedges:
                continue

            # Max hedges per symbol reached?
            if hedge_count_by_symbol.get(symbol, 0) >= MAX_HEDGES_PER_SYMBOL:
                continue

            # Cooldown check
            last_time = self._last_hedge_time.get(symbol, 0)
            if time.time() - last_time < HEDGE_COOLDOWN_SEC:
                continue

            # Position must be LOSING
            if pos.profit >= 0:
                continue

            # Calculate loss in R (SL distance)
            if pos.sl <= 0:
                continue  # No SL = can't calculate R

            sl_dist = abs(pos.price_open - pos.sl)
            if sl_dist <= 0:
                continue

            current_loss = abs(pos.price_open - pos.price_current)
            loss_in_r = current_loss / sl_dist

            if loss_in_r < MIN_LOSS_R_TO_HEDGE:
                continue  # Loss not significant enough

            # ═══ HEDGE TIME ═══
            await self._open_hedge(pos, sl_dist)

    async def _open_hedge(self, main_pos, sl_dist: float) -> None:
        """Open a hedge position opposite to the main position."""
        symbol = main_pos.symbol

        # Determine hedge direction (opposite of main)
        if main_pos.type == 0:  # BUY -> hedge with SELL
            order_type = mt5.ORDER_TYPE_SELL
            hedge_dir = "SELL"
        else:  # SELL -> hedge with BUY
            order_type = mt5.ORDER_TYPE_BUY
            hedge_dir = "BUY"

        # Calculate hedge lot (50% of main)
        hedge_lot = round(main_pos.volume * HEDGE_LOT_RATIO, 2)
        if hedge_lot < 0.01:
            hedge_lot = 0.01

        # Get current price
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error("smart_hedge_no_tick", extra={"symbol": symbol})
            return

        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        # Calculate ATR-based TP and SL for hedge
        tp_dist = sl_dist * HEDGE_TP_ATR_MULT
        hedge_sl_dist = sl_dist * HEDGE_SL_ATR_MULT

        if order_type == mt5.ORDER_TYPE_BUY:
            tp = price + tp_dist
            sl = price - hedge_sl_dist
        else:
            tp = price - tp_dist
            sl = price + hedge_sl_dist

        # Round to proper digits
        sym_info = mt5.symbol_info(symbol)
        digits = sym_info.digits if sym_info else 5
        tp = round(tp, digits)
        sl = round(sl, digits)
        price = round(price, digits)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": hedge_lot,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": HEDGE_MAGIC,
            "comment": f"SmartHedge #{main_pos.ticket}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        logger.warning("smart_hedge_opening", extra={
            "symbol": symbol,
            "main_ticket": main_pos.ticket,
            "main_loss": round(main_pos.profit, 2),
            "hedge_dir": hedge_dir,
            "hedge_lot": hedge_lot,
            "hedge_tp": tp,
            "hedge_sl": sl,
        })

        result = await asyncio.to_thread(mt5.order_send, request)

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            self._active_hedges[main_pos.ticket] = result.order
            self._last_hedge_time[symbol] = time.time()
            logger.info("smart_hedge_opened", extra={
                "symbol": symbol,
                "hedge_ticket": result.order,
                "main_ticket": main_pos.ticket,
                "hedge_lot": hedge_lot,
            })
        else:
            retcode = result.retcode if result else "NO_RESULT"
            comment = result.comment if result else ""
            logger.error("smart_hedge_failed", extra={
                "symbol": symbol,
                "retcode": retcode,
                "comment": comment,
            })

    async def _close_hedge(self, hedge_ticket: int, symbol: str, reason: str) -> None:
        """Close a hedge position."""
        # Get the hedge position details
        positions = mt5.positions_get(ticket=hedge_ticket)
        if not positions:
            return  # Already closed

        pos = positions[0]

        # Close opposite
        if pos.type == 0:  # BUY -> close with SELL
            close_type = mt5.ORDER_TYPE_SELL
            price = mt5.symbol_info_tick(symbol).bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(symbol).ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": hedge_ticket,
            "price": price,
            "deviation": 20,
            "magic": HEDGE_MAGIC,
            "comment": f"SmartHedge close: {reason}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = await asyncio.to_thread(mt5.order_send, request)

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info("smart_hedge_closed", extra={
                "symbol": symbol,
                "hedge_ticket": hedge_ticket,
                "reason": reason,
                "hedge_profit": round(pos.profit, 2),
            })
        else:
            logger.error("smart_hedge_close_failed", extra={
                "symbol": symbol,
                "hedge_ticket": hedge_ticket,
                "retcode": result.retcode if result else "NO_RESULT",
            })
