
import MetaTrader5 as mt5
import json

def get_open_positions():
    if not mt5.initialize():
        print(json.dumps({"error": "Failed to initialize MT5"}))
        return

    positions = mt5.positions_get()
    if positions is None:
        print(json.dumps({"error": "No positions found or MT5 error"}))
    else:
        results = []
        for p in positions:
            results.append({
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
                "magic": p.magic
            })
        print(json.dumps(results, indent=2))
    
    mt5.shutdown()

if __name__ == "__main__":
    get_open_positions()
