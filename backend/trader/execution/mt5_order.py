import MetaTrader5 as mt5
import logging
from backend.trader.data.mapper import mapper
from backend.trader.storage.sqlite_db import db

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
        
        logger.info(f"Attempting {self.mode.upper()} order: {side} {symbol} @ {signal['entry_price']} SL={signal['sl']} Lot={lot_size}")

        if self.mode == "live":
            # Real MT5 Execution
            if not mt5.initialize():
                return {"status": "error", "reason": "MT5 Not initialized"}
                
            # Correct rounding & alignment
            info = mt5.symbol_info(broker_symbol)
            if info is None: return {"status": "error", "reason": f"No symbol info for {broker_symbol}"}
            
            def norm(p):
                # MetaTrader 5 Attribute: trade_tick_size (not tick_size)
                ts = getattr(info, 'trade_tick_size', getattr(info, 'tick_size', 0))
                if ts > 0:
                    return round(round(p / ts) * ts, info.digits)
                return round(p, info.digits)

            action = mt5.TRADE_ACTION_DEAL
            if signal.get('entry_type') == 'LIMIT':
                action = mt5.TRADE_ACTION_PENDING
                order_type = mt5.ORDER_TYPE_BUY_LIMIT if side == 'BUY' else mt5.ORDER_TYPE_SELL_LIMIT
                price = norm(signal['entry_price'])
                comment = f"OPUS_LM_{signal['model']}"
            else:
                action = mt5.TRADE_ACTION_DEAL
                order_type = mt5.ORDER_TYPE_BUY if side == 'BUY' else mt5.ORDER_TYPE_SELL
                tick = mt5.symbol_info_tick(broker_symbol)
                price = tick.ask if side == 'BUY' else tick.bid
                price = norm(price)
                comment = f"OPUS_{signal['model']}"
            
            sl = norm(signal['sl'])
            tp = norm(signal['tp1'])
            
            # Final check against StopsLevel
            stops_level = info.trade_stops_level * info.point
            if abs(price - sl) < stops_level:
                # Widen SL to minimum stops level
                if side == 'BUY': sl = norm(price - stops_level - (info.point * 10))
                else: sl = norm(price + stops_level + (info.point * 10))
                logger.warning(f"⚠️ StopsLevel Breach: Widening SL to {sl}")

            request = {
                "action": action,
                "symbol": broker_symbol,
                "volume": lot_size,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": 777000,
                "comment": comment,
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
                "tp2": signal.get('tp2', signal['tp1']),
                "tp3": signal.get('tp3', signal['tp1']),
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
                "tp2": signal.get('tp2', signal['tp1']),
                "tp3": signal.get('tp3', signal['tp1']),
                "lot": lot_size
            }
            self.virtual_orders.append(trade_record)
            db.record_trade(trade_record)
            logger.info(f"{self.mode.upper()} Virtual Order generated", extra={"metrics": trade_record})
            return {"status": "ok", "trade": trade_record}

        return {"status": "error", "reason": "Unknown mode"}

    def close_symbol_positions(self, symbol: str) -> bool:
        """Closes all open positions for a specific symbol."""
        broker_symbol = mapper.to_broker(symbol)
        positions = mt5.positions_get(symbol=broker_symbol)
        if not positions:
            logger.info(f"No open positions to close for {symbol}")
            return True
            
        success = True
        for pos in positions:
            tick = mt5.symbol_info_tick(broker_symbol)
            if tick is None:
                success = False
                continue
                
            side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
            price = tick.bid if side == "BUY" else tick.ask
            order_type = mt5.ORDER_TYPE_SELL if side == "BUY" else mt5.ORDER_TYPE_BUY
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": broker_symbol,
                "volume": pos.volume,
                "type": order_type,
                "position": pos.ticket,
                "price": price,
                "deviation": 20,
                "magic": pos.magic,
                "comment": "OPUS_MAINT_CLOSE",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✅ Maintenance: Closed #{pos.ticket} {symbol}")
            else:
                logger.error(f"❌ Failed to close #{pos.ticket}: {result.comment if result else 'Unknown error'}")
                success = False
        return success

