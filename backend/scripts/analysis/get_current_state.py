import MetaTrader5 as mt5
import json

def get_positions():
    if not mt5.initialize():
        print(json.dumps({"error": "Init failed"}))
        return

    positions = mt5.positions_get()
    if positions is None:
        print(json.dumps({"error": "Fetch failed"}))
        mt5.shutdown()
        return

    pos_list = []
    for p in positions:
        pos_list.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
            "volume": p.volume,
            "price_open": p.price_open,
            "price_current": p.price_current,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            "comment": p.comment,
            "swap": p.swap,
            "magic": p.magic
        })
    
    print(json.dumps(pos_list, indent=2))
    mt5.shutdown()

if __name__ == "__main__":
    get_positions()
