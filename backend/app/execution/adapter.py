"""
Execution Adapter Interface & Implementations.
Abstracts the difference between LIVE (MT5) and DRY_RUN (Paper) execution.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.domain.models import OrderPlan, AccountState, ExecutionResult, SymbolProfile
from app.domain.enums import Action
from app.core.logging import get_logger
from app.mt5.client import MT5Client

logger = get_logger(__name__)


class ExecutionAdapter(ABC):
    """
    Abstract base class for execution adapters.
    Allows swappable execution backends (MT5 vs Paper).
    """

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if execution execution backend is connected/ready."""
        pass

    @abstractmethod
    def get_account_state(self) -> AccountState:
        """Get current account balance, equity, margin, etc."""
        pass

    @abstractmethod
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get open positions."""
        pass
    
    @abstractmethod
    def execute_order(self, plan: OrderPlan) -> ExecutionResult:
        """Execute a trade order."""
        pass

    @abstractmethod
    def modify_sl(self, ticket: int, new_sl: float, new_tp: float = 0.0) -> bool:
        """Modify position SL/TP."""
        pass

    @abstractmethod
    def close_position(self, ticket: int, reason: str = "") -> bool:
        """Close a specific position."""
        pass

    @abstractmethod
    def partial_close(self, ticket: int, volume: float, reason: str = "") -> bool:
        """Partially close a position."""
        pass
    
    @abstractmethod
    def get_symbol_info(self, symbol: str) -> Optional[SymbolProfile]:
         """Get symbol specification."""
         pass
         
    @abstractmethod
    def get_current_price(self, symbol: str) -> tuple[float, float]:
        """Get (bid, ask)."""
        pass
        
    @abstractmethod
    def is_market_open(self, symbol: str) -> bool:
        """Check if market is open."""
        pass

    @abstractmethod
    def calc_margin(self, action: Action, symbol: str, volume: float, price: float) -> Optional[float]:
        """Calculate required margin for an order using MT5."""
        pass


class Mt5LiveAdapter(ExecutionAdapter):
    """
    Adapter for REAL MT5 execution.
    Delegates everything to the existing MT5Client.
    """

    def __init__(self, mt5_client: MT5Client):
        self.client = mt5_client

    def is_connected(self) -> bool:
        return self.client.is_connected()

    def get_account_state(self) -> AccountState:
        return self.client.get_account_state()

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.client.get_positions(symbol)

    def execute_order(self, plan: OrderPlan) -> ExecutionResult:
        try:
            res = self.client.send_order(plan)
            return ExecutionResult(
                ticket=res["ticket"],
                price=res["price"],
                volume=res["volume"],
                retcode=res["retcode"],
                comment=res["comment"],
                mode="LIVE"
            )
        except Exception as e:
            logger.error("mt5_adapter_execute_error", extra={"error": str(e), "symbol": plan.symbol})
             # Return error result instead of crashing
            return ExecutionResult(
                ticket=0,
                price=0.0,
                volume=0.0,
                error=str(e),
                mode="LIVE"
            )

    def modify_sl(self, ticket: int, new_sl: float, new_tp: float = 0.0) -> bool:
        return self.client.modify_sl(ticket, new_sl, new_tp)

    def close_position(self, ticket: int, reason: str = "") -> bool:
        return self.client.close_position(ticket, reason)

    def partial_close(self, ticket: int, volume: float, reason: str = "") -> bool:
        return self.client.partial_close(ticket, volume, reason)
        
    def get_symbol_info(self, symbol: str) -> Optional[SymbolProfile]:
        return self.client.get_symbol_info(symbol)
        
    def get_current_price(self, symbol: str) -> tuple[float, float]:
        return self.client.get_current_price(symbol)
        
    def is_market_open(self, symbol: str) -> bool:
        return self.client.is_market_open(symbol)

    def calc_margin(self, action: Action, symbol: str, volume: float, price: float) -> Optional[float]:
        return self.client.calc_margin(action, symbol, volume, price)

