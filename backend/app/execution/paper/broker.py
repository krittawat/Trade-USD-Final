"""
Paper Execution Broker — "Virtual Exness"
Simulates order execution, fills, spreads, and slippage.
Keeps track of a ghost portfolio.
"""

import math
import uuid
import json
import os
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from threading import RLock

from app.core.logging import get_logger
from app.core.config import Settings
from app.domain.models import OrderPlan, AccountState, ExecutionResult, Action, SymbolProfile
from app.core.time import utc_now

logger = get_logger(__name__)

class PaperPosition:
    def __init__(
        self,
        ticket: int,
        symbol: str,
        action: Action,
        volume: float,
        entry_price: float,
        sl: float,
        tp: float,
        time_open: datetime,
        comment: str = "",
        magic: int = 0
    ):
        self.ticket = ticket
        self.symbol = symbol
        self.action = action
        self.volume = volume
        self.price_open = entry_price
        self.sl = sl
        self.tp = tp
        self.time = time_open
        self.comment = comment
        self.magic = magic
        
        # Runtime updates
        self.price_current = entry_price
        self.profit = 0.0
        self.swap = 0.0
        self.commission = 0.0

    def to_dict(self, digits: int = 2) -> dict:
        return {
            "ticket": self.ticket,
            "symbol": self.symbol,
            "type": "BUY" if self.action == Action.BUY else "SELL",
            "volume": self.volume,
            "price_open": self.price_open,
            "price_current": self.price_current,
            "sl": self.sl,
            "tp": self.tp,
            "profit": self.profit,
            "comment": self.comment,
            "magic": self.magic,
            "time": self.time, # Object
            "digits": digits
        }


class PaperBroker:
    """
    Simulates a broker for DRY_RUN mode.
    Maintains balance, equity, and open positions in memory (with persistence).
    """

    def __init__(self, settings: Settings, state_file_path: str = "backend/data/paper_state.json"):
        self.settings = settings
        self.state_file = state_file_path
        self._lock = RLock()
        
        # Account State
        self.balance = 10000.0 # Default fallback
        self.equity = 10000.0
        self.positions: Dict[int, PaperPosition] = {}
        self._next_ticket = 1000
        
        # Sim settings
        self.slippage_points = getattr(settings, "dry_run_slippage_points", 5)
        
        self.load_state()
        
        # Override balance if fresh start and config present (and no saved state)
        # Note: In a real logic we might want to respect saved state over config unless reset
        if len(self.positions) == 0 and abs(self.balance - 10000.0) < 0.01:
             init_bal = getattr(settings, "dry_run_initial_balance", 10000.0)
             self.balance = init_bal
             self.equity = init_bal

    def save_state(self):
        """Persist state to JSON."""
        with self._lock:
            data = {
                "balance": self.balance,
                "next_ticket": self._next_ticket,
                "positions": [
                    {
                        **p.to_dict(), 
                        "action": p.action.value, 
                        "time": p.time.isoformat()
                    } 
                    for p in self.positions.values()
                ]
            }
            try:
                os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
                with open(self.state_file, "w") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                logger.error("paper_save_failed", extra={"error": str(e)})

    def load_state(self):
        """Load state from JSON."""
        if not os.path.exists(self.state_file):
            return

        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
                self.balance = data.get("balance", 10000.0)
                self._next_ticket = data.get("next_ticket", 1000)
                
                self.positions = {}
                for p_data in data.get("positions", []):
                    action_enum = Action(p_data["action"]) if "action" in p_data else (Action.BUY if p_data.get("type") == "BUY" else Action.SELL)
                    
                    pos = PaperPosition(
                        ticket=p_data["ticket"],
                        symbol=p_data["symbol"],
                        action=action_enum,
                        volume=p_data["volume"],
                        entry_price=p_data["price_open"],
                        sl=p_data["sl"],
                        tp=p_data["tp"],
                        time_open=datetime.fromisoformat(p_data["time"]),
                        comment=p_data.get("comment", ""),
                        magic=p_data.get("magic", 0)
                    )
                    # Restore runtime state
                    pos.price_current = p_data.get("price_current", pos.price_open)
                    pos.profit = p_data.get("profit", 0.0)
                    
                    self.positions[pos.ticket] = pos
                    
        except Exception as e:
            logger.error("paper_load_failed", extra={"error": str(e)})

    def get_account_state(self) -> AccountState:
        with self._lock:
            # Recalculate equity based on latest prices (updated via tick simulatio or manually)
            # For exactitude, we need current prices.
            # In this architecture, execute_order or update_price should enable re-calc.
            # Here we just return last known.
            
            used_margin = 0.0 
            # Simplified margin: 1 lot = 100,000 units / leverage (e.g. 500) = 200 USD approx
            # This is very rough. 
            # Ideally: contract_size * price / leverage
            approx_margin_per_lot = 200.0 
            
            floating_pl = sum(p.profit for p in self.positions.values())
            used_margin = sum(p.volume for p in self.positions.values()) * approx_margin_per_lot
            
            self.equity = self.balance + floating_pl
            
            return AccountState(
                balance=self.balance,
                equity=self.equity,
                margin=used_margin,
                free_margin=self.equity - used_margin,
                floating_pl=floating_pl,
                open_positions=len(self.positions),
                daily_pl=0.0, # TODO: Track daily reset
                initial_balance=getattr(self.settings, "dry_run_initial_balance", 10000.0)
            )

    def execute_order(self, plan: OrderPlan, current_price: tuple[float, float], point: float) -> ExecutionResult:
        with self._lock:
            bid, ask = current_price
            
            # Simulate Slippage
            # Points to price = points * point
            slippage_price = self.slippage_points * point
            
            if plan.action == Action.BUY:
                # Buy @ Ask + Slippage
                fill_price = ask + slippage_price
                # Check funds/margin? (Skip for now in simple Dry Run)
            else:
                # Sell @ Bid - Slippage
                fill_price = bid - slippage_price
            
            ticket = self._next_ticket
            self._next_ticket += 1
            
            pos = PaperPosition(
                ticket=ticket,
                symbol=plan.symbol,
                action=plan.action,
                volume=plan.lot_size,
                entry_price=fill_price,
                sl=plan.stop_loss,
                tp=plan.take_profit or 0.0,
                time_open=utc_now(),
                comment=plan.comment,
                magic=plan.magic
            )
            
            self.positions[ticket] = pos
            self.save_state()
            
            logger.info(f"Paper Order Executed: {plan.action} {plan.lot_size} @ {fill_price}")
            
            return ExecutionResult(
                ticket=ticket,
                price=fill_price,
                volume=plan.lot_size,
                comment="Paper Fill",
                mode="DRY_RUN"
            )

    def _log_to_journal(self, pos: PaperPosition, exit_price: float, profit: float, reason: str, closed_vol: float):
        """Append trade result to CSV journal."""
        journal_path = os.path.join(os.path.dirname(self.state_file), "journal_dry_run.csv")
        
        # Ensure header
        if not os.path.exists(journal_path):
            try:
                with open(journal_path, "w", encoding="utf-8") as f:
                    f.write("timestamp,ticket,symbol,side,volume,entry_price,exit_price,sl,tp,reason,profit,balance,equity\n")
            except Exception: pass
            
        try:
            with open(journal_path, "a", encoding="utf-8") as f:
                ts = datetime.now(timezone.utc).isoformat()
                side = "BUY" if pos.action == Action.BUY else "SELL"
                # balance/equity AFTER this trade
                # We already updated balance before calling this
                
                line = f"{ts},{pos.ticket},{pos.symbol},{side},{closed_vol},{pos.price_open},{exit_price},{pos.sl},{pos.tp},{reason},{profit:.2f},{self.balance:.2f},{self.equity:.2f}\n"
                f.write(line)
        except Exception as e:
            logger.error("journal_write_failed", extra={"error": str(e)})

    def close_position(self, ticket: int, current_price: tuple[float, float], reason: str = "", contract_size: float = 100.0) -> bool:
        with self._lock:
            if ticket not in self.positions:
                return False
            
            pos = self.positions[ticket]
            bid, ask = current_price
            
            # Close Buy @ Bid, Close Sell @ Ask
            if pos.action == Action.BUY:
                close_price = bid
            else:
                close_price = ask
            
            if pos.action == Action.BUY:
                profit = (close_price - pos.price_open) * pos.volume * contract_size
            else:
                profit = (pos.price_open - close_price) * pos.volume * contract_size
                
            self.balance += profit
            self.equity = self.balance + sum(p.profit for p in self.positions.values() if p.ticket != ticket) # Update equity roughly
            
            self._log_to_journal(pos, close_price, profit, reason, pos.volume)
            
            del self.positions[ticket]
            self.save_state()
            
            logger.info(f"Paper Position Closed: #{ticket} PnL={profit:.2f} ({reason})")
            return True

    def update_valuations(self, market_data: Dict[str, tuple[float, float, float]]):
        """
        Update floating PnL for all positions based on current market data.
        market_data: {symbol: (bid, ask, contract_size)}
        """
        with self._lock:
            total_profit = 0.0
            for ticket, pos in self.positions.items():
                if pos.symbol in market_data:
                    bid, ask, contract_size = market_data[pos.symbol]
                    
                    if pos.action == Action.BUY:
                        # Value @ Bid
                        pos.price_current = bid
                        pos.profit = (bid - pos.price_open) * pos.volume * contract_size
                    else:
                        # Value @ Ask
                        pos.price_current = ask
                        pos.profit = (pos.price_open - ask) * pos.volume * contract_size
                total_profit += pos.profit
            
            self.equity = self.balance + total_profit

    def modify_sl(self, ticket: int, new_sl: float, new_tp: float = 0.0) -> bool:
        with self._lock:
            if ticket not in self.positions:
                return False
            
            self.positions[ticket].sl = new_sl
            if new_tp > 0:
                self.positions[ticket].tp = new_tp
            self.save_state()
            return True

    def partial_close(self, ticket: int, volume: float, current_price: tuple[float, float], contract_size: float = 100.0, reason: str = "") -> bool:
         with self._lock:
            if ticket not in self.positions:
                return False
            
            pos = self.positions[ticket]
            if volume >= pos.volume:
                return self.close_position(ticket, current_price, reason="Partial->Full", contract_size=contract_size)
            
            # Realize proportional profit
            bid, ask = current_price
            
            if pos.action == Action.BUY:
                close_price = bid
                realized = (close_price - pos.price_open) * volume * contract_size
            else:
                close_price = ask
                realized = (pos.price_open - close_price) * volume * contract_size
            
            self.balance += realized
            # Update equity?
            
            # Log partial
            # We need to temporarily pretend the pos volume is 'volume' to log correct closed vol?
            # Or just pass explicit volume to log
            self._log_to_journal(pos, close_price, realized, f"Partial|{reason}", volume)
            
            pos.volume = round(pos.volume - volume, 8)
            self.save_state()
            return True
