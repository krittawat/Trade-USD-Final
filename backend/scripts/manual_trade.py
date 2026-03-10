import sys
from pathlib import Path
import argparse
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5

def place_manual_trade(symbol: str, action: str, lot: float, sl_points: int, tp_points: int):
    print("=" * 60)
    print(f"🎯 FORCED MANUAL TRADE: {action} {lot} on {symbol}")
    print("=" * 60)

    if not mt5.initialize():
        print("❌ MT5 Initialization failed")
        return

    si = mt5.symbol_info(symbol)
    if not si:
        print(f"❌ Symbol {symbol} not found")
        mt5.shutdown()
        return

    if not si.visible:
        mt5.symbol_select(symbol, True)

    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        print("❌ No tick data")
        mt5.shutdown()
        return

    price = tick.ask if action == "BUY" else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

    sl = price - (sl_points * si.point) if action == "BUY" else price + (sl_points * si.point)
    tp = price + (tp_points * si.point) if action == "BUY" else price - (tp_points * si.point)

    sl = round(sl, si.digits)
    tp = round(tp, si.digits)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 999999,  # Bot magic number
        "comment": "MANUAL_FORCE",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    print(f"📡 Sending {action} @ {price} | SL: {sl} | TP: {tp}")
    result = mt5.order_send(request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"✅ Success! Ticket: {result.order}")
        print("🤖 Position Guardian will now auto-manage this trade (Trailing Stop, Break-Even).")
    else:
        print(f"❌ Failed! Retcode: {result.retcode if result else 'None'}, Error: {mt5.last_error()}")

    mt5.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Force a manual trade on MT5.")
    parser.add_argument("--symbol", type=str, default="XAUUSDc", help="Symbol to trade")
    parser.add_argument("--action", type=str, choices=["BUY", "SELL"], default="BUY", help="BUY or SELL")
    parser.add_argument("--lot", type=float, default=0.01, help="Lot size (e.g., 0.01)")
    parser.add_argument("--sl", type=int, default=1500, help="Stop Loss in points (1500 = 150 pips)")
    parser.add_argument("--tp", type=int, default=3000, help="Take Profit in points (3000 = 300 pips)")
    args = parser.parse_args()

    place_manual_trade(args.symbol, args.action, args.lot, args.sl, args.tp)
