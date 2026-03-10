import MetaTrader5 as mt5
import sys

mt5.initialize()

# Try to add SL to naked positions 2944296400 and 2944329765
tickets = [2944296400, 2944329765]

for t in tickets:
    p = mt5.positions_get(ticket=t)
    if not p:
        print(f"Ticket {t} not found")
        continue
    
    pos = p[0]
    if pos.sl > 0:
        print(f"Ticket {t} already has SL: {pos.sl}")
        continue
        
    print(f"Fixing #{t} {pos.symbol} BUY {pos.volume} @ {pos.price_open}")
    
    # Check symbol specs to get correct stop level distance
    sym_info = mt5.symbol_info(pos.symbol)
    if not sym_info:
        continue
        
    stoplevel = sym_info.trade_stops_level * sym_info.point
    
    # Calculate safe SL below current price
    current_price = mt5.symbol_info_tick(pos.symbol).bid
    
    # Place SL 0.5% below current price or at least stoplevel
    sl_dist = max(current_price * 0.005, stoplevel + sym_info.point * 10)
    new_sl = round(current_price - sl_dist, sym_info.digits)
    
    print(f"  Current: {current_price}, StopLevel: {stoplevel}")
    print(f"  Setting SL: {new_sl}")
    
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": pos.symbol,
        "position": t,
        "sl": new_sl,
        "tp": pos.tp if pos.tp > 0 else 0.0
    }
    
    result = mt5.order_send(request)
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"  SUCCESS! SL set to {new_sl}")
    else:
        print(f"  FAILED: {result.retcode} {result.comment}")

mt5.shutdown()
