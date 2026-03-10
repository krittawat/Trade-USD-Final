"""
Position Health Checker — แยกจาก MasterLoop._check_positions().

ตรวจสุขภาพ position (LIVE mode):
    0. Hedging Guard
    1. Close Detection → Telegram + Brain Learning
    2. Break-Even move
    3. Ghost Guard (virtual SL/TP)
    4. ATR fetch + trailing + profit lock via PositionGuardian
    5. Legacy trailing manager
    6. Efficiency exits (time decay + RSI pulse)
    7. PostFill verify SL
"""

import asyncio
import time

from app.core.logging import get_logger

logger = get_logger(__name__)


class PositionHealthChecker:
    """
    Monitors health of all open positions each cycle.

    Extracted from MasterLoop._check_positions() for maintainability.
    """

    def __init__(self):
        # Dependencies (injected from MasterLoop)
        self.mt5 = None
        self.settings = None
        self.mode = None
        self.telegram = None
        self.db = None

        # Risk modules
        self.hedge_manager = None
        self.postfill = None
        self.ghost_guard = None
        self.guardian = None
        self.trailing_manager = None
        self.tp_manager = None
        self.cooldown_mgr = None
        self.risk_dampener = None

        # Brain
        self.brain_memory = None
        self._outcome_analyzer = None

    def set_dependencies(self, **deps):
        for key, val in deps.items():
            setattr(self, key, val)

    async def check(
        self,
        account,
        tracked_positions: dict,
        last_decisions: dict,
        cycle_count: int,
    ) -> None:
        """
        Check all position health — break-even, trailing, profit lock, etc.
        """
        from app.risk.breakeven import should_move_to_breakeven, mark_be_moved
        from app.mt5.market_data import fetch_candles
        from app.risk.tp_manager import TPTier, TPConfig
        from app.core.mode import TradingMode

        if not self.mt5:
            return

        positions = await asyncio.to_thread(self.mt5.get_positions)

        # ─── 0. Hedging Guard ───
        if account and getattr(self.settings, 'hedge_enable', False):
            await self.hedge_manager.monitor_risks(positions, account.equity, account.balance)

        # ─── 1. Close Detection ───
        current_tickets = {p["ticket"]: p for p in positions}

        if tracked_positions:
            closed_tickets = set(tracked_positions.keys()) - set(current_tickets.keys())
            for ticket in closed_tickets:
                prev = tracked_positions[ticket]
                deal_info = await asyncio.to_thread(self.mt5.get_closed_deal_info, ticket)

                profit = deal_info["profit"] if deal_info else prev.get("profit", 0.0)
                close_price = deal_info["price_open"] if deal_info else 0.0
                reason = deal_info.get("comment", "") if deal_info else "unknown"

                logger.info("position_closed_detected", extra={
                    "ticket": ticket, "symbol": prev.get("symbol", ""),
                    "profit": profit, "reason": reason,
                })

                if self.telegram:
                    asyncio.create_task(self.telegram.notify_trade_close(
                        symbol=prev.get("symbol", "?"), ticket=ticket,
                        action=prev.get("type", ""), lot_size=prev.get("volume", 0.0),
                        profit=profit, entry_price=prev.get("price_open", 0.0),
                        close_price=close_price, reason=reason, mode=self.mode.value,
                    ))

                # Brain record
                if self.brain_memory:
                    try:
                        entry_price = prev.get("price_open", 0.0)
                        sl_price = prev.get("sl", 0.0)
                        risk_dist = abs(entry_price - sl_price) if sl_price > 0 else 1.0
                        rr = profit / (risk_dist * prev.get("volume", 0.01) * 100) if risk_dist > 0 else 0.0

                        self.brain_memory.record_trade_outcome(
                            strategy_name=prev.get("strategy", "unknown"),
                            symbol=prev.get("symbol", ""),
                            regime=prev.get("regime", "UNKNOWN"),
                            session=prev.get("session", ""),
                            profit_usd=profit, risk_reward=round(rr, 2),
                        )

                        # Pattern learning
                        entry_patterns = prev.get("patterns", [])
                        if entry_patterns:
                            is_win = profit > 0
                            self.brain_memory.record_pattern_outcomes(
                                symbol=prev.get("symbol", ""),
                                regime=prev.get("regime", "UNKNOWN"),
                                pattern_win_rates={p: (1.0 if is_win else 0.0) for p in entry_patterns},
                                patterns_found={p: 1 for p in entry_patterns},
                            )
                    except Exception as e:
                        logger.warning("brain_record_error", extra={"ticket": ticket, "error": str(e)})

                # Outcome Analyzer
                try:
                    self._outcome_analyzer.record_trade_close(
                        ticket=ticket, pnl=profit, hold_bars=0, hold_seconds=0.0,
                        exit_type=reason or "unknown",
                    )
                except Exception:
                    pass

                # Overtrading Protection
                closed_symbol = prev.get("symbol", "")
                if profit < 0:
                    self.cooldown_mgr.record_loss(closed_symbol)
                    self.risk_dampener.record_loss(closed_symbol)
                elif profit > 0:
                    self.cooldown_mgr.record_win(closed_symbol)
                    self.risk_dampener.record_win(closed_symbol)

        # ─── Update tracked positions ───
        for p in positions:
            ticket = p["ticket"]
            if ticket in tracked_positions:
                tracked_positions[ticket]["profit"] = p["profit"]
                tracked_positions[ticket]["sl"] = p.get("sl", 0)
            else:
                sym = p["symbol"]
                last_dec = last_decisions.get(sym, {})
                strategy_name = last_dec.get("strategy", "unknown")

                # Mark as newly tracked — skip Time Decay this cycle
                if not hasattr(self, '_newly_tracked_tickets'):
                    self._newly_tracked_tickets = set()
                self._newly_tracked_tickets.add(ticket)

                if self.telegram and strategy_name == "unknown" and cycle_count > 1:
                    # Only notify for TRULY new positions (not startup detection)
                    # Calculate actual risk from SL distance
                    sl_price = p.get("sl", 0.0)
                    entry = p["price_open"]
                    vol = p["volume"]
                    risk_usd = 0.0
                    risk_pct = 0.0

                    if sl_price > 0:
                        dist = abs(entry - sl_price)
                        contract_size = 1.0  # default fallback
                        if self.mt5:
                            try:
                                sym_profile = await asyncio.to_thread(self.mt5.get_symbol_info, sym)
                                if sym_profile:
                                    contract_size = sym_profile.contract_size
                            except Exception as e:
                                logger.warning("manual_trade_profile_error", extra={
                                    "symbol": sym, "error": str(e),
                                })
                        risk_usd = dist * vol * contract_size
                        if account and account.equity > 0:
                            risk_pct = (risk_usd / account.equity) * 100.0

                    asyncio.create_task(self.telegram.notify_trade_open(
                        symbol=sym, action=p["type"], lot_size=vol,
                        entry_price=entry, stop_loss=sl_price,
                        take_profit=p.get("tp", 0.0), risk_usd=risk_usd, risk_pct=risk_pct,
                        strategy_name="Manual/External", ticket=ticket, mode=self.mode.value,
                    ))
                elif cycle_count <= 1:
                    logger.info("startup_position_tracked", extra={
                        "ticket": ticket, "symbol": sym, "strategy": strategy_name,
                    })

                tracked_positions[ticket] = {
                    "symbol": sym, "type": p["type"], "volume": p["volume"],
                    "price_open": p["price_open"], "profit": p["profit"],
                    "sl": p.get("sl", 0),
                    "strategy": strategy_name if strategy_name != "unknown" else "Manual/External",
                    "regime": last_dec.get("regime", "UNKNOWN"),
                    "session": last_dec.get("session", ""),
                }

                # Auto TP setup
                if getattr(self.settings, 'trailing_auto_setup', True):
                    tp_mode = getattr(self.settings, 'tp_management_mode', 'off')
                    if tp_mode != "off":
                        tiers = [
                            TPTier(r_target=self.settings.tp_partial_tier1_r,
                                   close_pct=self.settings.tp_partial_tier1_pct, move_sl_to="be"),
                            TPTier(r_target=self.settings.tp_partial_tier2_r,
                                   close_pct=self.settings.tp_partial_tier2_pct, move_sl_to="prev_tp"),
                            TPTier(r_target=self.settings.tp_partial_tier3_r,
                                   close_pct=self.settings.tp_partial_tier3_pct, move_sl_to=None),
                        ]
                        tp_cfg = TPConfig(
                            mode=tp_mode, tiers=tiers,
                            atr_tp_multiplier=getattr(self.settings, 'tp_dynamic_atr_mult', 3.0),
                            trailing_tp_atr_distance=getattr(self.settings, 'tp_trailing_distance_atr', 0.5),
                        )
                        self.tp_manager.set_tp(ticket, tp_cfg, volume=p["volume"])

        # Clean stale tracked positions
        active_tickets = {p["ticket"] for p in positions}
        for t in list(tracked_positions.keys()):
            if t not in active_tickets:
                del tracked_positions[t]

        # ─── Break-Even ───
        for pos in positions:
            ticket = pos["ticket"]
            entry = pos["price_open"]
            current = pos["price_current"]
            sl = pos["sl"]
            is_buy = pos["type"] == "BUY"

            if sl > 0 and should_move_to_breakeven(
                ticket=ticket, entry_price=entry, current_price=current,
                stop_loss=sl, r_multiple=self.settings.breakeven_r_multiple, is_buy=is_buy,
            ):
                # Anti-Hunt BE: dodge round numbers + micro-buffer
                from app.risk.sl_calculator import calculate_be_with_dodge
                direction = "BUY" if is_buy else "SELL"

                # Determine dodge buffer based on symbol
                sym = pos["symbol"]
                s = sym.upper()
                if "XAU" in s or "GOLD" in s:
                    dodge_buf = 0.50   # $0.50 for Gold
                elif "XAG" in s or "SILVER" in s:
                    dodge_buf = 0.010  # $0.010 for Silver
                elif "BTC" in s:
                    dodge_buf = 5.0    # $5.00 for BTC
                elif "JPY" in s:
                    dodge_buf = 0.020  # 0.020 for JPY pairs
                else:
                    dodge_buf = 0.00010  # 1 pip for Forex

                be_sl = calculate_be_with_dodge(entry, direction, sym, dodge_buf)

                success = await asyncio.to_thread(self.mt5.modify_sl, ticket, be_sl)
                if success:
                    mark_be_moved(ticket)
                    logger.info("be_move_success", extra={
                        "ticket": ticket, "symbol": pos["symbol"],
                        "entry": entry, "be_sl": be_sl,
                        "dodge_buffer": dodge_buf,
                        "stage": "be_move", "result": "ok",
                    })

        # ─── Ghost Guard ───
        await asyncio.to_thread(self.ghost_guard.check_all, positions)

        # ─── ATR + Guardian ───
        atr_values: dict[str, float] = {}
        candles_cache_all: dict = {}
        position_symbols = {p["symbol"] for p in positions}
        for sym in position_symbols:
            broker_sym = sym
            if self.mt5 and hasattr(self.mt5, 'adapter'):
                broker_sym = self.mt5.adapter.map_symbol(sym)

            c = await asyncio.to_thread(fetch_candles, broker_sym, timeframe="M5", count=50, cycle=cycle_count)
            if c is not None and len(c) >= 15:
                candles_cache_all[sym] = c
                import numpy as np
                high, low, close = c["high"].values, c["low"].values, c["close"].values
                tr = np.maximum(
                    high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
                )
                atr_values[sym] = float(np.mean(tr[-14:]))

        guardian_result = await asyncio.to_thread(self.guardian.process_all, positions, atr_values, candles_cache_all)

        if self.telegram:
            for key, emoji, label in [
                ("manual_protected", "🛡️", "manual trades protected"),
                ("trailing_actions", "📈", "trailing SL updates"),
                ("lock_actions", "🔒", "profit locks triggered"),
            ]:
                count = guardian_result.get(key, 0)
                if count > 0:
                    asyncio.create_task(self.telegram._send(f"{emoji} Guardian: {count} {label}"))

        # ─── Legacy Trailing Manager ───
        await self.trailing_manager.process_all_positions()

        # ─── Efficiency Exits ───
        # Skip positions that were just tracked this cycle (bot restart detection)
        newly_tracked = getattr(self, '_newly_tracked_tickets', set())
        # Dedup: only send exit notification once per ticket
        if not hasattr(self, '_efficiency_exit_sent'):
            self._efficiency_exit_sent: set[int] = set()
        for pos_dict in positions:
            ticket = pos_dict["ticket"]
            if ticket in newly_tracked:
                continue  # Don't time-decay a position we just discovered
            if ticket in self._efficiency_exit_sent:
                continue  # Already sent exit for this ticket — skip spam
            symbol = pos_dict["symbol"]
            entry = pos_dict["price_open"]
            current = pos_dict["price_current"]
            sl = pos_dict.get("sl", 0)
            is_buy = pos_dict["type"] == "BUY"
            direction = "BUY" if is_buy else "SELL"

            profit_pts = (current - entry) if is_buy else (entry - current)
            sl_dist = (entry - sl if is_buy else sl - entry) if sl > 0 else 5.0
            if sl_dist <= 0:
                sl_dist = 5.0
            current_r = profit_pts / sl_dist

            open_time = pos_dict.get("time", 0)
            if open_time > 0:
                elapsed_min = (time.time() - open_time) / 60.0 if open_time < 1e12 else 0

                class _PosRef:
                    def __init__(self, t): self.ticket = t

                td_result = self.trailing_manager.check_time_decay(_PosRef(ticket), elapsed_min, current_r)
                if td_result and td_result.get("action") == "CLOSE":
                    continue

            if symbol in candles_cache_all:
                import pandas_ta as _ta
                _c = candles_cache_all[symbol]
                _rsi = _ta.rsi(_c["close"], length=14)
                if _rsi is not None and len(_rsi) > 0:
                    rsi_val = float(_rsi.iloc[-1])

                    class _PosRef2:
                        def __init__(self, t): self.ticket = t

                    pulse_result = self.trailing_manager.check_pulse_exit(_PosRef2(ticket), rsi_val, direction, current_r)
                    if pulse_result and pulse_result.get("action") == "CLOSE":
                        pass


        # Clear newly tracked tickets for next cycle
        if hasattr(self, '_newly_tracked_tickets'):
            self._newly_tracked_tickets.clear()
        # Purge stale entries from efficiency exit dedup set
        self._efficiency_exit_sent -= (self._efficiency_exit_sent - active_tickets)

        # ─── PostFill ───
        await self.postfill.verify_all_active()
