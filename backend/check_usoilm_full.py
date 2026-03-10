import MetaTrader5 as mt5
import pandas as pd

if not mt5.initialize():
    print(f"MT5 initialize failed")
    quit()

target = "USOILm"
positions = mt5.positions_get(symbol=target)

if positions:
    print(f"--- All {len(positions)} Positions for {target} ---")
    for p in positions:
        type_str = 'BUY' if p.type == 0 else 'SELL'
        print(f"Ticket: {p.ticket} | Type: {type_str} | Lot: {p.volume} | Open: {p.price_open} | SL: {p.sl} | TP: {p.tp} | Profit: {p.profit} | Magic: {p.magic}")
else:
    print(f"No open positions for {target}")

mt5.shutdown()
