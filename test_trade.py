"""
Quick test trade: places a minimum-lot BUY on XAUUSDc with proper SL/TP.
Fix: wider SL/TP to avoid 'Invalid stops' error (10016).
"""
import sys
sys.path.insert(0, "D:/VibeCode/Trade")

import MetaTrader5 as mt5
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def main():
    if not mt5.initialize():
        logger.error("MT5 init failed")
        return

    account = mt5.account_info()
    logger.info(f"Account: {account.login} | Balance: {account.balance} | Equity: {account.equity}")

    symbol = "XAUUSDc"
    sym_info = mt5.symbol_info(symbol)
    if sym_info is None:
        logger.error(f"Symbol {symbol} not found")
        mt5.shutdown()
        return

    if not sym_info.visible:
        mt5.symbol_select(symbol, True)

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error("No tick data")
        mt5.shutdown()
        return

    price = tick.ask
    point = sym_info.point
    spread = sym_info.spread
    stops_level = sym_info.trade_stops_level  # minimum stop distance in points

    logger.info(f"Symbol: {symbol}")
    logger.info(f"Price: {price} | Point: {point} | Digits: {sym_info.digits}")
    logger.info(f"Spread: {spread} pts | Stops Level (min): {stops_level} pts")
    logger.info(f"Volume Min: {sym_info.volume_min} | Step: {sym_info.volume_step}")

    # Use at least stops_level + spread + buffer for SL/TP distance
    min_distance = max(stops_level, spread * 2, 5000)  # at least 5000 points = $5
    sl_distance = min_distance + 1000  # extra buffer
    tp_distance = min_distance + 2000

    sl = round(price - sl_distance * point, sym_info.digits)
    tp = round(price + tp_distance * point, sym_info.digits)
    lot = sym_info.volume_min

    logger.info(f"Calculated: SL dist={sl_distance} pts | TP dist={tp_distance} pts")
    logger.info(f"BUY @ {price} | SL: {sl} | TP: {tp} | Lot: {lot}")

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 999999,
        "comment": "TEST_TRADE",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    logger.info(f"Sending order request...")
    result = mt5.order_send(request)

    if result is None:
        logger.error(f"order_send returned None. Last error: {mt5.last_error()}")
    elif result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(f"Order FAILED — retcode={result.retcode} comment='{result.comment}'")
        logger.error(f"Full result: {result}")
    else:
        logger.info(f"✅ Order FILLED — ticket={result.order} price={result.price} volume={result.volume}")

    mt5.shutdown()

if __name__ == "__main__":
    main()
