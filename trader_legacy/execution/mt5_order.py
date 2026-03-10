import MetaTrader5 as mt5
import logging
from trader.data.mapper import mapper
from trader.storage.sqlite_db import db

logger = logging.getLogger("opus_logger")

class Executor:
    def __init__(self, mode="dry_run"):
        """Modes: live, dry_run, backtest"""
        self.mode = mode.lower()
        self.virtual_orders = [] # For dry_run

    def place_order(self, signal: dict, lot_size: float = 0.01) -> dict:
        """
        Executes order via MT5 or simulates it based on mode.
        """
        symbol = signal['symbol']
        broker_symbol = mapper.to_broker(symbol)
        side = signal['side']
        order_type = mt5.ORDER_TYPE_BUY if side == 'BUY' else mt5.ORDER_TYPE_SELL
        
        logger.info(f"Attempting {self.mode.upper()} order: {side} {symbol} @ {signal['entry_price']} SL={signal['sl']}")

        if self.mode == "live":
            # Real MT5 Execution
            if not mt5.initialize():
                return {"status": "error", "reason": "MT5 Not initialized"}
                
            point = mt5.symbol_info(broker_symbol).point
            price = mt5.symbol_info_tick(broker_symbol).ask if side == 'BUY' else mt5.symbol_info_tick(broker_symbol).bid
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": broker_symbol,
                "volume": lot_size,
                "type": order_type,
                "price": price,
                "sl": signal['sl'],
                "tp": signal['tp1'],
                "deviation": 20,
                "magic": 777000,
                "comment": f"OPUS_{signal['model']}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error(f"Order failed: {result.comment}, code: {result.retcode}")
                db.log_incident(symbol, f"Order failed: {result.retcode}", "ERROR")
                return {"status": "error", "reason": result.comment}
                
            trade_record = {
                "ticket": result.order,
                "symbol": symbol,
                "mode": self.mode,
                "side": side,
                "entry_price": result.price,
                "sl": signal['sl'],
                "tp1": signal['tp1'],
                "tp2": signal['tp2'],
                "tp3": signal['tp3'],
                "lot": lot_size
            }
            db.record_trade(trade_record)
            logger.info("LIVE Order filled successfully", extra={"metrics": trade_record})
            return {"status": "ok", "trade": trade_record}

        elif self.mode in ["dry_run", "backtest"]:
            # Simulated Execution
            trade_record = {
                "ticket": len(self.virtual_orders) + 1000,
                "symbol": symbol,
                "mode": self.mode,
                "side": side,
                "entry_price": signal['entry_price'],
                "sl": signal['sl'],
                "tp1": signal['tp1'],
                "tp2": signal['tp2'],
                "tp3": signal['tp3'],
                "lot": lot_size
            }
            self.virtual_orders.append(trade_record)
            db.record_trade(trade_record)
            logger.info(f"{self.mode.upper()} Virtual Order generated", extra={"metrics": trade_record})
            return {"status": "ok", "trade": trade_record}

        return {"status": "error", "reason": "Unknown mode"}

