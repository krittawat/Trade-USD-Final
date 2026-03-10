from typing import Optional, List, Dict, Any, cast

from app.execution.adapter import ExecutionAdapter
from app.execution.paper.broker import PaperBroker
from app.mt5.client import MT5Client
from app.domain.models import OrderPlan, AccountState, ExecutionResult, SymbolProfile, Action
from app.core.logging import get_logger

logger = get_logger(__name__)

class DryRunAdapter(ExecutionAdapter):
    """
    Adapter for DRY_RUN (Paper) execution.
    Uses PaperBroker for trades, but still needs MT5Client for Market Data.
    """

    def __init__(self, paper_broker: PaperBroker, mt5_client: MT5Client):
        self.broker = paper_broker
        self.mt5 = mt5_client # Source of truth for Market Data

    def is_connected(self) -> bool:
        # For Dry Run, we need MT5 for data, but if it fails, we might still want to
        # allow pure simulation if we had other data sources. 
        # But strictly, strategy needs data.
        return self.mt5.is_connected()

    def get_account_state(self) -> AccountState:
        # Return Virtual Account State
        # Trigger PnL update before returning
        self._update_valuations()
        return self.broker.get_account_state()

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        self._update_valuations() # Ensure fresh prices
        all_pos = [p.to_dict() for p in self.broker.positions.values()]
        if symbol:
            return [p for p in all_pos if p["symbol"] == symbol]
        return all_pos

    def execute_order(self, plan: OrderPlan) -> ExecutionResult:
        # 1. Get current Price from MT5
        bid, ask = self.mt5.get_current_price(plan.symbol)
        if bid <= 0:
            return ExecutionResult(ticket=0, price=0, volume=0, error="No Market Price", mode="DRY_RUN")

        info = self.mt5.get_symbol_info(plan.symbol)
        point = info.point if info else 0.01

        # 2. Delegate execution to PaperBroker
        return self.broker.execute_order(plan, (bid, ask), point)

    def modify_sl(self, ticket: int, new_sl: float, new_tp: float = 0.0) -> bool:
        return self.broker.modify_sl(ticket, new_sl, new_tp)

    def close_position(self, ticket: int, reason: str = "") -> bool:
        # Need symbol to get price
        pos = self.broker.positions.get(ticket)
        if not pos: 
            return False
            
        bid, ask = self.mt5.get_current_price(pos.symbol)
        info = self.mt5.get_symbol_info(pos.symbol)
        contract = info.contract_size if info else 100.0
        
        return self.broker.close_position(ticket, (bid, ask), reason, contract_size=contract)

    def partial_close(self, ticket: int, volume: float, reason: str = "") -> bool:
        pos = self.broker.positions.get(ticket)
        if not pos:
            return False
            
        bid, ask = self.mt5.get_current_price(pos.symbol)
        info = self.mt5.get_symbol_info(pos.symbol)
        contract = info.contract_size if info else 100.0
        
        return self.broker.partial_close(ticket, volume, (bid, ask), contract_size=contract, reason=reason)
        
    def get_symbol_info(self, symbol: str) -> Optional[SymbolProfile]:
        return self.mt5.get_symbol_info(symbol)
        
    def get_current_price(self, symbol: str) -> tuple[float, float]:
        return self.mt5.get_current_price(symbol)
        
    def is_market_open(self, symbol: str) -> bool:
        return self.mt5.is_market_open(symbol)

    def calc_margin(self, action: Action, symbol: str, volume: float, price: float) -> Optional[float]:
        return self.mt5.calc_margin(action, symbol, volume, price)

    def _update_valuations(self):
        """Helper to push latest MT5 prices to PaperBroker."""
        market_data = {}
        # Get unique symbols from paper positions
        symbols = set(p.symbol for p in self.broker.positions.values())
        for s in symbols:
            bid, ask = self.mt5.get_current_price(s)
            info = self.mt5.get_symbol_info(s)
            contract = info.contract_size if info else 100.0 # Default fallback
            if bid > 0:
                market_data[s] = (bid, ask, contract)
        
        self.broker.update_valuations(market_data)
