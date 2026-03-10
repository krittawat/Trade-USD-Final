"""
OPUS Smoke Test — ส่งออเดอร์จริง 0.01 lot XAUUSD เพื่อเช็คว่า execution pipeline ทำงาน
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import MetaTrader5 as mt5
from trader.data.mapper import mapper

def main():
    if not mt5.initialize():
        print(f"❌ MT5 init failed: {mt5.last_error()}")
        return

    broker_sym = mapper.to_broker("XAUUSD")
    info = mt5.symbol_info(broker_sym)
    tick = mt5.symbol_info_tick(broker_sym)
    account = mt5.account_info()

    if not info or not tick or not account:
        print("❌ Cannot get symbol/tick/account info")
        mt5.shutdown()
        return

    print(f"📊 Symbol: {broker_sym}")
    print(f"💰 Account: {account.login} | Equity: {account.equity} | Balance: {account.balance}")
    print(f"📈 Bid: {tick.bid} | Ask: {tick.ask} | Spread: {info.spread}")

    # BUY 0.01 lot at market with SL 300 points below
    price = tick.ask
    sl = round(price - 3.0, 2)  # ~300 cents below for cent account
    tp = round(price + 3.0, 2)  # ~300 cents above

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": broker_sym,
        "volume": 0.01,
        "type": mt5.ORDER_TYPE_BUY,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 777000,
        "comment": "OPUS_SMOKE_TEST",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    print(f"\n🚀 Sending BUY 0.01 @ {price} SL={sl} TP={tp}...")
    result = mt5.order_send(request)

    if result is None:
        print(f"❌ order_send returned None: {mt5.last_error()}")
    elif result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"✅ ORDER FILLED! Ticket: {result.order}")
        print(f"   Price: {result.price} | Volume: {result.volume}")
    else:
        print(f"❌ Order failed: retcode={result.retcode} | {result.comment}")

    mt5.shutdown()

if __name__ == "__main__":
    main()
