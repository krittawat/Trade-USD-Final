"""
Partial TP — Close 30-50% of the position at +1.5R to +2R.
"""

from app.core.logging import get_logger
from app.domain.models import OrderPlan

logger = get_logger(__name__)

def check_partial_tp(position, current_price: float, contract_size: float) -> float:
    """
    Check if a partial profit taking should be executed.
    
    Args:
        position: The open position object (from MT5 or simulator)
        current_price: Current market price
        contract_size: Symbol contract size
        
    Returns:
        float: The volume to close (0.0 if no partial TP)
    """
    try:
        # 1. Identify trade parameters
        entry_price = position.price_open
        sl_price = position.sl
        
        if sl_price == 0:
            return 0.0
            
        is_buy = position.type == 0 # MT5: 0=BUY, 1=SELL
        
        # 2. Calculate R-Multiple
        sl_dist = abs(entry_price - sl_price)
        if sl_dist == 0:
            return 0.0
            
        current_profit_points = (current_price - entry_price) if is_buy else (entry_price - current_price)
        current_r = current_profit_points / sl_dist
        
        # 3. Decision Logic: Partial Close at +1.5R or +2.0R
        # Rule: Only partial close once per position.
        # We check comment for "PARTIAL_OK" to avoid loops.
        comment = position.comment or ""
        if "P_CLOSED" in comment:
            return 0.0
            
        if current_r >= 2.0:
            # High confidence target: close 50%
            return round(position.volume * 0.5, 2)
        elif current_r >= 1.5:
            # Moderate target: close 30%
            return round(position.volume * 0.3, 2)
            
        return 0.0
    except Exception as e:
        logger.error(f"Error checking partial TP: {e}")
        return 0.0
