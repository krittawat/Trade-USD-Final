import MetaTrader5 as mt5
import logging
import pandas as pd
from backend.trader.data.mapper import mapper
from backend.trader.storage.sqlite_db import db
from backend.trader.observability.logger import C

logger = logging.getLogger("opus_logger")

def manage_pending_orders(symbol: str, df: pd.DataFrame, timeframe_str: str) -> int:
    """
    Scans pending orders for the given symbol and deletes them if the market 
    structure on the given timeframe invalidates the trade direction.
    - BUY LIMIT/STOP: Invalidated if structure prints LL (Lower Low)
    - SELL LIMIT/STOP: Invalidated if structure prints HH (Higher High)
    """
    if 'structure' not in df.columns:
        return 0
        
    # Get the latest confirmed structure that is not 'NONE'
    structures = df[df['structure'] != 'NONE']['structure']
    if len(structures) == 0:
        return 0
        
    latest_struct = structures.iloc[-1]
    
    broker_symbol = mapper.to_broker(symbol)
    orders = mt5.orders_get(symbol=broker_symbol)
    if not orders:
        return 0
        
    cancelled_count = 0
    for order in orders:
        ticket = order.ticket
        order_type = order.type
        
        # BUY orders (LIMIT = 2, STOP = 4, STOP_LIMIT = 6)
        is_buy_order = order_type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP, mt5.ORDER_TYPE_BUY_STOP_LIMIT)
        # SELL orders (LIMIT = 3, STOP = 5, STOP_LIMIT = 7)
        is_sell_order = order_type in (mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP, mt5.ORDER_TYPE_SELL_STOP_LIMIT)
        
        cancel_reason = None
        
        if is_buy_order and latest_struct == 'LL':
            cancel_reason = f"Structure changed to Bearish ({latest_struct}) on {timeframe_str}"
            
        elif is_sell_order and latest_struct == 'HH':
            cancel_reason = f"Structure changed to Bullish ({latest_struct}) on {timeframe_str}"
            
        if cancel_reason:
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": ticket,
            }
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                side_str = "BUY" if is_buy_order else "SELL"
                logger.info(
                    f"  🗑️ {C.YELLOW}CANCEL PENDING #{ticket} {side_str} {symbol}{C.RST} | "
                    f"Reason: {cancel_reason}"
                )
                db.log_incident(symbol, f"Cancelled pending {side_str} order #{ticket}: {cancel_reason}", "INFO")
                cancelled_count += 1
            else:
                err = result.comment if result else "None"
                logger.warning(f"  ⚠️ Cancel pending failed #{ticket}: {err}")
                
    return cancelled_count
