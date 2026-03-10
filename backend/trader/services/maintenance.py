# -*- coding: utf-8 -*-
"""
OPUS Maintenance Scheduler — Monday Automation
Handles specific time-based tasks:
1. 04:00 (UTC+7): Gap Check
2. 05:00 (UTC+7): USOILm Force Exit (if not profit)
3. 14:00 (UTC+7): Health Check Log
4. 20:00 (UTC+7): Daily Target Check
"""
import logging
import time
from datetime import datetime, timedelta
import pytz
import MetaTrader5 as mt5

logger = logging.getLogger("opus_logger")

class MaintenanceScheduler:
    def __init__(self, executor):
        self.executor = executor
        self.tz = pytz.timezone("Asia/Bangkok")
        self._last_gap_check = None
        self._last_usoil_exit = None
        self._last_health_check = None
        self._last_target_check = None
        
        # Risk flags
        self.gap_safety_active = False
        self.gap_safety_until = None

    def run_cycle(self, account_state: dict):
        """Run maintenance tasks based on local time (Bangkok)."""
        now = datetime.now(self.tz)
        current_time_str = now.strftime("%H:%M")
        
        # 1. 04:00 - Gap Check & Safety
        if "04:00" <= current_time_str < "05:00":
            if self._last_gap_check != now.date():
                self._run_gap_check()
                self._run_monday_audit() # Execute Audit after gap check
                self._last_gap_check = now.date()

        # 2. 05:00 - USOILm Force Exit
        if "05:00" <= current_time_str < "06:00":
            if self._last_usoil_exit != now.date():
                self._run_usoil_exit_logic(account_state)
                self._last_usoil_exit = now.date()

        # 3. 14:00 - Health Check (BTC Signals)
        if "14:00" <= current_time_str < "15:00":
            if self._last_health_check != now.date():
                logger.info("🕒 [MAINTENANCE] 14:00 Health Check: Monitoring BTC signal reception...")
                self._last_health_check = now.date()

        # 4. 20:00 - target Check (600 - 2000 THB)
        if "20:00" <= current_time_str < "21:00":
            if self._last_target_check != now.date():
                self._run_target_report(account_state)
                self._last_target_check = now.date()

    def _run_gap_check(self):
        """Checks for large price gaps at market open."""
        logger.info("🕒 [MAINTENANCE] 04:00: Checking for Market Open Gaps...")
        
        # Increased safety for XAU/XAG on Monday morning
        now = datetime.now(self.tz)
        if now.weekday() == 0: # Monday
            logger.warning("🚨 Monday Market Open: Initial Safety Delay Active.")
            self.gap_safety_active = True
            # Wait at least 30 mins, or until spreads stabilize (handled in is_safety_blocked)
            self.gap_safety_until = now + timedelta(minutes=30)
        else:
            self.gap_safety_active = False

    def is_safety_blocked(self) -> tuple:
        """Returns (is_blocked, reason)"""
        now = datetime.now(self.tz)
        if self.gap_safety_active:
            if now < self.gap_safety_until:
                return True, f"WAITING FOR OPENING VOLATILITY (until {self.gap_safety_until.strftime('%H:%M')})"
            
            # Dynamic Spread Check (Extended Safety)
            if now.weekday() == 0 and now.hour < 6:
                # Check major symbols (XAUUSD, BTCUSD) spread
                for sym in ["XAUUSD", "BTCUSD"]:
                    tick = mt5.symbol_info_tick(sym)
                    sym_info = mt5.symbol_info(sym)
                    if tick and sym_info:
                        if sym_info.spread > sym_info.spread_balance * 2.0: # If spread is 2x normal
                            return True, f"WAITING FOR SPREAD STABILITY: {sym} spread {sym_info.spread} > normal"

            self.gap_safety_active = False
        return False, ""

    def _run_monday_audit(self):
        """Audit all open positions on Monday Market Open."""
        logger.info("🛡️ [AUDIT] Running Monday Market Open Audit...")
        positions = mt5.positions_get()
        if not positions:
            logger.info("  No open positions to audit.")
            return

        from backend.trader.notification.telegram import notify_status
        report = ["📋 *MONDAY OPEN AUDIT REPORT*"]
        
        for pos in positions:
            symbol = pos.symbol
            profit = pos.profit
            pips = 0 # Approximate
            
            # Check for SL/TP Safety
            safety_status = "✅ SAFE"
            if pos.sl == 0:
                safety_status = "⚠️ NO STOP LOSS!"
            
            # Check for Gap Impact (Price at Friday Close vs Monday Open)
            # (Note: Requires Friday close price from DB or History, 
            # for now we report current PnL and SL status)
            
            report.append(f"• *{symbol}* #{pos.ticket}: PnL=${profit:.2f} | {safety_status}")
            
            if pos.sl == 0:
                logger.error(f"🚨 CRITICAL: Position #{pos.ticket} ({symbol}) has NO STOP LOSS on Monday Open!")

        notify_status("\n".join(report))

    def _run_usoil_exit_logic(self, account_state: dict):
        """Closes USOILm if it's lagging in profit at 05:00 Monday."""
        logger.info("🕒 [MAINTENANCE] 05:00: USOILm Maintenance Exit Check...")
        positions = mt5.positions_get(symbol="USOILm")
        if not positions:
            logger.info("  No USOILm positions found.")
            return

        total_usoil_profit = sum(p.profit for p in positions)
        if total_usoil_profit <= 0:
            logger.warning(f"  USOILm PnL is ${total_usoil_profit:.2f}. Executing force close to unlock Governor.")
            self.executor.close_symbol_positions("USOILm")
        else:
            logger.info(f"  USOILm is in profit (${total_usoil_profit:.2f}). Leaving open for potential gains.")

    def _run_target_report(self, account_state: dict):
        """Reports daily target progress at 20:00."""
        pnl_usd = account_state.get("daily_pnl", 0)
        pnl_thb = pnl_usd * 35.0 # Assuming fixed rate for report
        logger.info(f"🕒 [MAINTENANCE] 20:00 Target Report: PnL Today ${pnl_usd:.2f} (~{pnl_thb:.0f} THB)")
        if pnl_thb >= 600:
            logger.info("✅ Daily Profit Target (600 THB) achieved!")
        else:
            logger.info("ℹ️ Daily Profit Target (600 THB) not yet reached.")
