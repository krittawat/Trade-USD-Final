import MetaTrader5 as mt5
import sys

def emergency_exit():
    if not mt5.initialize():
        print("MT5 Init Failed")
        return

    # Target the toxic USOILm position
    ticket = 1027555200
    positions = mt5.positions_get(ticket=ticket)
    
    if not positions:
        print(f"Position {ticket} not found or already closed.")
        mt5.shutdown()
        return

    pos = positions[0]
    symbol = pos.symbol
    lot = pos.volume
    type_dict = {mt5.ORDER_TYPE_BUY: mt5.ORDER_TYPE_SELL, mt5.ORDER_TYPE_SELL: mt5.ORDER_TYPE_BUY}
    
    tick = mt5.symbol_info_tick(symbol)
    price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": type_dict[pos.type],
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": 999999,
        "comment": "RECOVERY EXIT",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"Failed to close: {result.comment} ({result.retcode})")
    else:
        print(f"SUCCESS: Position {ticket} closed at {result.price}")

    mt5.shutdown()

if __name__ == "__main__":
    emergency_exit()
