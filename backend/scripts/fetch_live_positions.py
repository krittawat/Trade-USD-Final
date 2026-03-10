import MetaTrader5 as mt5
import sys

if not mt5.initialize():
    print("MT5 initialize failed")
    sys.exit(1)

positions = mt5.positions_get()
if positions is None:
    print("No positions found or MT5 error.")
elif len(positions) == 0:
    print("Zero open positions on account.")
else:
    print(f"Total Open Positions: {len(positions)}")
    for pos in positions:
        print(f"Ticket: {pos.ticket} | Symbol: {pos.symbol} | Type: {pos.type} | Volume: {pos.volume} | Price: {pos.price_open} | Profit: {pos.profit}")

mt5.shutdown()
