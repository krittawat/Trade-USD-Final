from typing import Optional, List, Dict, Any, Tuple

import MetaTrader5 as mt5

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.errors import ConnectionError, OrderError
from app.core.currency_adapter import get_adapter, CurrencyAdapter
from app.core.mode_resolver import ModeResolver
from app.domain.enums import Action
from app.domain.models import AccountState, OrderPlan, SymbolProfile

logger = get_logger(__name__)

# MT5 timeframe mapping
TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


class MT5Client:
    """
    MetaTrader 5 client wrapper — เชื่อมต่อ MT5 จริง.

    ห่อหุ้ม MetaTrader5 Python package เพื่อ:
    - จัดการ connection lifecycle
    - Log ทุก action
    - ป้องกันการส่งออเดอร์โดยไม่ผ่าน risk gate
    - **จัดการแปลงหน่วยเงินและ Lot Size (CurrencyAdapter integration)**
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._connected = False
        self._initial_balance: float = 0.0  # เก็บ balance ตอนเริ่ม (สำหรับ capital floor)
        # Performance: symbol profile cache with TTL
        self._symbol_cache: Dict[str, Tuple[float, SymbolProfile]] = {}  # symbol -> (ts, profile)
        self._symbol_cache_ttl: float = 60.0  # cache 60 seconds
        
        # Dependency Injection for Mode
        self.mode_resolver = ModeResolver(settings)
        self.adapter = get_adapter(settings) # Singleton instance

    def connect(self) -> bool:
        """
        เชื่อมต่อ MT5 terminal จริง.

        Raises:
            ConnectionError: ถ้าเชื่อมต่อไม่ได้
        """
        logger.info("mt5_connect_attempt", extra={
            "server": self.settings.mt5_server,
            "login": self.settings.mt5_login,
        })

        # เชื่อมต่อจริง
        init_args = {}
        if self.settings.mt5_path:
            init_args["path"] = self.settings.mt5_path

        # ถ้ามี login → ส่ง credentials ให้ MT5 authenticate
        # ถ้าไม่มี login → เชื่อมกับ terminal ที่เปิดอยู่แล้ว (ไม่ต้อง re-auth)
        if self.settings.mt5_login:
            init_args["login"] = int(self.settings.mt5_login)
            if self.settings.mt5_password:
                init_args["password"] = self.settings.mt5_password
            if self.settings.mt5_server:
                init_args["server"] = self.settings.mt5_server
        if self.settings.mt5_timeout:
            init_args["timeout"] = int(self.settings.mt5_timeout)

        self._connected = mt5.initialize(**init_args)

        if not self._connected:
            err = mt5.last_error()
            raise ConnectionError(
                f"MT5 connect failed: {err}",
                context={"error_code": err[0] if err else -1, "error_msg": str(err)},
            )

        # ─── Auto-Detect Mode ───
        try:
            account_info = mt5.account_info()
            if account_info:
                # Get visible symbols for heuristic
                symbols = mt5.symbols_get()
                symbol_names = [s.name for s in symbols] if symbols else []
                
                # Resolve Mode
                mode = self.mode_resolver.resolve(account_info.currency, symbol_names)
                
                # Update Adapter
                self.adapter.update_mode(mode)
                
                logger.info("mt5_mode_detected", extra={
                    "mode": mode.__dict__,
                    "account_currency": account_info.currency
                })
            else:
                logger.warning("mt5_connected_no_account_info")
        except Exception as e:
            logger.error("mt5_mode_detection_failed", extra={"error": str(e)})

        # ดึง initial balance สำหรับ capital floor (Normalized)
        info = mt5.account_info()
        if info:
            self._initial_balance = self.adapter.normalize_money(info.balance)
            logger.info("mt5_connected", extra={
                "login": info.login,
                "server": info.server,
                "balance": info.balance,
                "equity": info.equity,
                "leverage": info.leverage,
                "balance_usd": self._initial_balance,
                "mode": self.adapter.mode.account_currency
            })
        
        return True

    def disconnect(self) -> None:
        """ตัดการเชื่อมต่อ MT5."""
        mt5.shutdown()
        self._connected = False
        logger.info("mt5_disconnected")

    def is_connected(self) -> bool:
        """ตรวจสอบว่ายังเชื่อมต่อ MT5 อยู่."""
        if not self._connected:
            return False
        # ตรวจจริงโดยดึง terminal info
        try:
            info = mt5.terminal_info()
            return info is not None and info.connected
        except Exception:
            self._connected = False
            return False

    def get_account_state(self) -> AccountState:
        """
        ดึงสถานะบัญชีปัจจุบันจาก MT5 จริง (Normalized to USD).
        รวมการคำนวณ Daily PL จากประวัติการเทรดวันนี้.
        """
        info = mt5.account_info()
        if info is None:
            logger.error("mt5_account_info_failed", extra={"error": str(mt5.last_error())})
            return AccountState(balance=0.0, equity=0.0)

        # Convert/Normalize money to USD
        balance_usd = self.adapter.normalize_money(info.balance)
        equity_usd = self.adapter.normalize_money(info.equity)

        # --- คำนวณ Daily PL จากประวัติ (Closed Profit Today) ---
        from datetime import datetime, time as dt_time
        now = datetime.now()
        start_of_day = datetime.combine(now.date(), dt_time.min)
        
        # ดึง deals ที่เกิดขึ้นตั้งแต่วันนี้
        history_deals = mt5.history_deals_get(start_of_day, now)
        closed_profit_today = 0.0
        if history_deals:
            for d in history_deals:
                # เฉพาะ deal ที่เป็น OUT (ปิดออเดอร์) หรือ INOUT
                if d.entry in [mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT]:
                    closed_profit_today += (d.profit + d.commission + d.swap)

        # รวม Floating PL
        positions = mt5.positions_get()
        floating_pl_account = sum(p.profit for p in positions) if positions else 0.0
        
        # total_daily_pl = Closed Profit Today + Current Floating PL
        daily_pl_usd = self.adapter.normalize_money(closed_profit_today + floating_pl_account)
        floating_pl_usd = self.adapter.normalize_money(floating_pl_account)

        return AccountState(
            balance=balance_usd,
            equity=equity_usd,
            margin=self.adapter.normalize_money(info.margin),
            free_margin=self.adapter.normalize_money(info.margin_free),
            floating_pl=floating_pl_usd,
            open_positions=len(positions) if positions else 0,
            daily_pl=daily_pl_usd,
            initial_balance=self._initial_balance,
            leverage=info.leverage,
            real_balance=info.balance,
            real_equity=info.equity,
            currency=info.currency,
        )

    def get_available_symbols(self, group: str = "*", forex_only: bool = True) -> List[str]:
        """
        Discover tradable symbols from MT5.
        """
        if not self.is_connected():
             return []

        symbols = mt5.symbols_get(group=group)
        if symbols is None:
            logger.warning("symbol_discovery_failed")
            return []
            
        results = []
        for s in symbols:
            # Basic validation
            if not s.visible and not s.select: 
                continue
            
            # Filter logic
            is_forex = "Forex" in s.path or "FX" in s.path or s.currency_base in ["USD", "EUR", "GBP", "JPY"]
            
            # Simple Heuristic: Trading enabled
            if s.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
                continue
                
            if forex_only and not is_forex:
                 # Check common non-forex
                 if "Stock" in s.path or "Index" in s.path or "Crypto" in s.path:
                     continue
            
            # Return INTERNAL symbols (unmapped)
            # e.g. Broker has "XAUUSDc" -> System uses "XAUUSD"
            internal_symbol = self.adapter.unmap_symbol(s.name)
            results.append(internal_symbol)
            
        logger.info("symbol_discovery_complete", extra={
            "found": len(results),
            "forex_only": forex_only
        })
        return list(set(results)) # Unique

    def get_symbol_info(self, symbol: str) -> Optional[SymbolProfile]:
        """
        ดึงข้อมูลสัญลักษณ์เทรดจาก MT5 → SymbolProfile.
        Handles symbol mapping (Internal -> Broker).
        """
        import time as _time
        now = _time.monotonic()

        # Check cache (key = INTERNAL symbol)
        cached = self._symbol_cache.get(symbol)
        if cached:
            cache_ts, profile = cached
            if now - cache_ts < self._symbol_cache_ttl:
                return profile

        # Map to broker symbol
        broker_symbol = self.adapter.map_symbol(symbol)
        
        info = mt5.symbol_info(broker_symbol)
        if info is None:
            return None

        # ตรวจว่าสัญลักษณ์เทรดได้ (visible + trade allowed)
        if not info.visible:
            # ลองเปิดให้เห็น
            if not mt5.symbol_select(broker_symbol, True):
                 return None
            info = mt5.symbol_info(broker_symbol)
            if info is None:
                return None

        profile = SymbolProfile(
            symbol=symbol, # Use INTERNAL symbol name in profile
            contract_size=info.trade_contract_size,
            volume_min=self.adapter.from_broker_lots(info.volume_min),
            volume_max=self.adapter.from_broker_lots(info.volume_max),
            volume_step=self.adapter.from_broker_lots(info.volume_step),
            point=info.point,
            digits=info.digits,
            spread_avg=info.spread * info.point,
            spread_max_allowed=info.spread * info.point * 3,
            is_active=info.trade_mode != 0,
        )

        # Update cache
        self._symbol_cache[symbol] = (now, profile)
        return profile

    def get_current_price(self, symbol: str) -> Tuple[float, float]:
        """
        ดึงราคา bid/ask ปัจจุบัน.
        """
        broker_symbol = self.adapter.map_symbol(symbol)
        tick = mt5.symbol_info_tick(broker_symbol)
        if tick is None:
            logger.error("tick_fetch_failed", extra={"symbol": broker_symbol})
            return 0.0, 0.0
        return tick.bid, tick.ask

    def get_current_spread(self, symbol: str) -> float:
        """ดึง spread ปัจจุบัน (ในหน่วยราคา)."""
        broker_symbol = self.adapter.map_symbol(symbol)
        tick = mt5.symbol_info_tick(broker_symbol)
        if tick is None:
            return 999.0
        return tick.ask - tick.bid

    def is_market_open(self, symbol: str) -> bool:
        """ตรวจว่าตลาดเปิดอยู่สำหรับ symbol นี้."""
        broker_symbol = self.adapter.map_symbol(symbol)
        info = mt5.symbol_info(broker_symbol)
        if info is None:
            return False
        return info.trade_mode != 0

    def get_positions(self, symbol: str | None = None) -> List[Dict[str, Any]]:
        """
        ดึง positions ที่เปิดอยู่ (จาก MT5 จริง).
        Returns normalized data (Standard Lots, Internal Symbols).
        """
        broker_symbol = self.adapter.map_symbol(symbol) if symbol else None
        
        if broker_symbol:
            positions = mt5.positions_get(symbol=broker_symbol)
        else:
            positions = mt5.positions_get()

        if positions is None:
            return []

        # Pre-fetch digits
        _digits_cache: Dict[str, int] = {}
        for p in positions:
            if p.symbol not in _digits_cache:
                sym_info = mt5.symbol_info(p.symbol)
                _digits_cache[p.symbol] = sym_info.digits if sym_info else 2

        mapped_positions = []
        for p in positions:
            internal_symbol = self.adapter.unmap_symbol(p.symbol)
            standard_volume = self.adapter.from_broker_lots(p.volume)
            profit_usd = self.adapter.normalize_money(p.profit)
            
            mapped_positions.append({
                "ticket": p.ticket,
                "symbol": internal_symbol,
                "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume": standard_volume, # Standard Lots
                "price_open": p.price_open,
                "price_current": p.price_current,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit, # Account Currency
                "profit_usd": profit_usd, # Normalized USD
                "comment": p.comment,
                "magic": p.magic,
                "time": p.time,
                "digits": _digits_cache.get(p.symbol, 2),
            })

        return mapped_positions


    def count_positions(self, symbol: str) -> int:
        """นับจำนวน positions ที่เปิดอยู่สำหรับ symbol."""
        broker_symbol = self.adapter.map_symbol(symbol)
        positions = mt5.positions_get(symbol=broker_symbol)
        return len(positions) if positions else 0

    def get_position_by_ticket(self, ticket: int) -> Optional[Dict[str, Any]]:
        """
        ดึง position เดียวจาก ticket (from MT5 จริง).
        Returns normalized data (Standard Lots, Internal Symbols) or None if not found.
        """
        positions = mt5.positions_get(ticket=ticket)
        if not positions or len(positions) == 0:
            return None

        p = positions[0] # Tuple
        
        # Helper to get digits cache (simplified, or re-fetch)
        sym_info = mt5.symbol_info(p.symbol)
        digits = sym_info.digits if sym_info else 2

        internal_symbol = self.adapter.unmap_symbol(p.symbol)
        standard_volume = self.adapter.from_broker_lots(p.volume)
        profit_usd = self.adapter.normalize_money(p.profit)
        
        return {
            "ticket": p.ticket,
            "symbol": internal_symbol,
            "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
            "volume": standard_volume, 
            "price_open": p.price_open,
            "price_current": p.price_current,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit, 
            "profit_usd": profit_usd,
            "comment": p.comment,
            "magic": p.magic,
            "time": p.time,
            "digits": digits,
        }

    def get_batch_mt5_state(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:

        """
        ดึง MT5 state ทุก symbol ในครั้งเดียว.
        """
        connected = self.is_connected()
        result: Dict[str, Dict[str, Any]] = {}

        # ดึง ALL positions ครั้งเดียว
        all_positions = mt5.positions_get()
        total_positions = len(all_positions) if all_positions else 0

        # นับ positions ต่อ symbol (Key = Broker Symbol)
        pos_by_broker_symbol: Dict[str, int] = {}
        if all_positions:
            for p in all_positions:
                pos_by_broker_symbol[p.symbol] = pos_by_broker_symbol.get(p.symbol, 0) + 1

        for symbol in symbols:
            broker_symbol = self.adapter.map_symbol(symbol)
            
            tick = mt5.symbol_info_tick(broker_symbol)
            if tick:
                spread = tick.ask - tick.bid
                market_open = True
            else:
                spread = 999.0
                market_open = False

            result[symbol] = {
                "connected": connected,
                "market_open": market_open,
                "spread": spread,
                "positions_count": pos_by_broker_symbol.get(broker_symbol, 0),
                "total_positions": total_positions,
            }

        return result

    def send_order(self, plan: OrderPlan) -> Dict[str, Any]:
        """
        ส่งคำสั่งเทรดจาก OrderPlan — ผ่าน MT5 จริง.
        
        Converts:
            - Internal Symbol -> Broker Symbol
            - Standard Lots -> Broker Lots (Cent/Standard)
        """
        if plan.stop_loss <= 0:
            raise OrderError("SL is mandatory — ห้ามส่งออเดอร์ไม่มี SL", {
                "symbol": plan.symbol,
            })

        broker_symbol = self.adapter.map_symbol(plan.symbol)
        
        # ดึง bid/ask สำหรับกำหนดราคา
        tick = mt5.symbol_info_tick(broker_symbol)
        if tick is None:
            raise OrderError("Cannot get tick price", {"symbol": broker_symbol})

        # กำหนดราคาเข้า
        if plan.action == Action.BUY:
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY
        else:
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL

        # ─── Convert Volume ───
        broker_volume = self.adapter.to_broker_lots(plan.lot_size, plan.symbol)
        
        # ═══════════════════════════════════════════════════════════════════
        # ABSOLUTE LAST-LINE-OF-DEFENSE LOT CAP (Defense in Depth Layer 2)
        # If ANY bug upstream produces insane lot sizes, this catches it
        # RIGHT BEFORE the order hits MT5. NEVER REMOVE THIS.
        # ═══════════════════════════════════════════════════════════════════
        HARD_BROKER_LOT_CAPS = {
            "XAUUSD": 3.0,   # Gold
            "XAGUSD": 1.0,   # Silver
            "BTCUSD": 2.0,   # BTC
        }
        HARD_DEFAULT_CAP = 10.0
        
        hard_cap = HARD_DEFAULT_CAP
        sym_upper = plan.symbol.upper()
        for key, cap_val in HARD_BROKER_LOT_CAPS.items():
            if key in sym_upper:
                hard_cap = cap_val
                break
        
        if broker_volume > hard_cap:
            logger.critical("SEND_ORDER_HARD_LOT_CAP", extra={
                "symbol": plan.symbol,
                "broker_volume_before": broker_volume,
                "hard_cap": hard_cap,
                "lot_std": plan.lot_size,
                "ALERT": "Lot exceeded hard cap at send_order — CLAMPED!",
            })
            broker_volume = hard_cap

        # สร้าง request dict
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": broker_volume,
            "type": order_type,
            "price": price,
            "deviation": 20,  # slippage allowance
            "magic": plan.magic if plan.magic is not None else 888888,
            "comment": plan.comment or f"AG|{plan.strategy_name}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # Handle Ghost Protocol (Stealth Mode)
        # If ghost_protocol is active, DO NOT send SL/TP to the broker.
        if not getattr(plan, "ghost_protocol", False):
            request["sl"] = plan.stop_loss
            # TP (optional)
            if plan.take_profit and plan.take_profit > 0:
                request["tp"] = plan.take_profit
        else:
            # We add a special marker in the comment to denote stealth mode if space permits
            if len(request["comment"]) < 26: 
                request["comment"] += "[👻]"

        logger.info("send_order", extra={
            "symbol": plan.symbol,
            "broker_symbol": broker_symbol,
            "action": plan.action.value if hasattr(plan.action, 'value') else str(plan.action),
            "lot_std": plan.lot_size,
            "lot_broker": broker_volume,
            "price": price,
            "sl": plan.stop_loss,
            "tp": plan.take_profit,
            "risk_usd": plan.risk_usd,
        })

        # ส่งออเดอร์
        result = mt5.order_send(request)

        if result is None:
            err = mt5.last_error()
            raise OrderError(f"Order send failed: {err}", {
                "symbol": broker_symbol, "error": str(err),
            })

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise OrderError(
                f"Order rejected: {result.comment} (code={result.retcode})",
                {"symbol": broker_symbol, "retcode": result.retcode, "comment": result.comment},
            )

        logger.info("order_filled", extra={
            "ticket": result.order,
            "symbol": broker_symbol,
            "lot_broker": result.volume,
            "price": result.price,
            "stage": "order",
            "result": "ok",
        })

        return {
            "ticket": result.order,
            "price": result.price,
            "volume": result.volume,
            "retcode": result.retcode,
            "comment": result.comment,
        }

    def modify_sl(self, ticket: int, new_sl: float, new_tp: float = 0.0) -> bool:
        """
        แก้ไข SL/TP ของออเดอร์ (ticket).
        """
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": new_sl,
        }
        if new_tp > 0:
            request["tp"] = new_tp

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("modify_sl_failed", extra={
                "ticket": ticket,
                "retcode": result.retcode if result else -1,
                "comment": result.comment if result else str(mt5.last_error())
            })
            return False

        logger.info("modify_sl_success", extra={"ticket": ticket, "new_sl": new_sl})
        return True

    def close_position(self, ticket: int, reason: str = "") -> bool:
        """Close specific position by ticket."""
        # 1. Get position details
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.warning("close_position_not_found", extra={"ticket": ticket})
            return False
            
        pos = positions[0]
        
        # 2. Determine close action
        # Check current price for execute
        tick_info = mt5.symbol_info_tick(pos.symbol)
        if not tick_info:
             logger.error("close_position_no_tick", extra={"ticket": ticket, "symbol": pos.symbol})
             return False

        if pos.type == mt5.ORDER_TYPE_BUY:
            action_type = mt5.ORDER_TYPE_SELL
            price = tick_info.bid
        else:
            action_type = mt5.ORDER_TYPE_BUY
            price = tick_info.ask
            
        comment = "AG|Close"
        if reason:
            comment = f"AG|{reason}"
            comment = comment[:30]

        # 3. Send close order
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": action_type,
            "price": price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        res = mt5.order_send(request)
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("close_position_failed", extra={"ticket": ticket, "retcode": res.retcode})
            return False
            
        logger.info("close_position_success", extra={"ticket": ticket, "reason": reason})
        return True

    def partial_close(self, ticket: int, volume: float, reason: str = "") -> bool:
        """
        Close specific portion of a position by ticket.
        :param volume: Volume to close in STANDARD LOTS.
        """
        # 1. Get position details from MT5 directly
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.warning("partial_close_not_found", extra={"ticket": ticket})
            return False
            
        pos = positions[0]
        
        # Convert requested standard lot back to broker lot
        internal_symbol = self.adapter.unmap_symbol(pos.symbol)
        broker_close_vol = self.adapter.to_broker_lots(volume, internal_symbol)
        
        # Ensure we don't try to close more than we have
        close_volume = min(broker_close_vol, pos.volume)
        if close_volume <= 0:
            return False
            
        # 2. Determine close action
        tick_info = mt5.symbol_info_tick(pos.symbol)
        if not tick_info:
             logger.error("partial_close_no_tick", extra={"ticket": ticket, "symbol": pos.symbol})
             return False

        if pos.type == mt5.ORDER_TYPE_BUY:
            action_type = mt5.ORDER_TYPE_SELL
            price = tick_info.bid
        else:
            action_type = mt5.ORDER_TYPE_BUY
            price = tick_info.ask
            
        comment = "AG|PClose"
        if reason:
            comment = f"AG|PC|{reason}"
            comment = comment[:30]

        # 3. Send close order
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": pos.symbol,
            "volume": close_volume,
            "type": action_type,
            "price": price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        res = mt5.order_send(request)
        if res.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("partial_close_failed", extra={"ticket": ticket, "retcode": res.retcode, "comment": res.comment})
            return False
            
        logger.info("partial_close_success", extra={"ticket": ticket, "volume_closed": close_volume, "reason": reason})
        return True

    def calc_margin(self, action: Action, symbol: str, volume: float, price: float) -> Optional[float]:
        """
        Calculate required margin for an order using MT5 API.
        This provides accurate margin even for cryptos or exotic pairs.
        """
        if not self.is_connected():
            return None

        broker_symbol = self.adapter.map_symbol(symbol)
        order_type = mt5.ORDER_TYPE_BUY if action == Action.BUY else mt5.ORDER_TYPE_SELL
        
        margin = mt5.order_calc_margin(order_type, broker_symbol, volume, price)
        if margin is None:
            logger.debug("calc_margin_failed", extra={
                "symbol": broker_symbol,
                "volume": volume,
                "price": price,
                "error": str(mt5.last_error())
            })
            return None
        return margin


    def get_historical_candles(
        self, symbol: str, timeframe: str, n_candles: int
    ) -> Optional[Any]:
        """
        ดึงแท่งเทียนย้อนหลัง.
        """
        mt5_tf = TIMEFRAME_MAP.get(timeframe, mt5.TIMEFRAME_M5)
        broker_symbol = self.adapter.map_symbol(symbol)
        
        rates = mt5.copy_rates_from_pos(broker_symbol, mt5_tf, 0, n_candles)
        if rates is None or len(rates) == 0:
            return None
        return rates

    def get_closed_deal_info(self, ticket: int) -> Optional[Dict[str, Any]]:
        """
        Get info about a CLOSED deal/position from history.
        Used to detect why a position was closed (SL/TP/Manual).
        """
        # Search history for deals related to this ticket
        # We look back 7 days just in case
        from datetime import datetime, timedelta
        to_date = datetime.now()
        from_date = to_date - timedelta(days=7)
        
        # history_deals_get can filter by ticket if position ID is consistent
        # Usually deal ticket != position ticket, but position_id field in deal links them.
        # Try fetching deals by position ticket first
        deals = mt5.history_deals_get(position=ticket)
        
        if not deals:
            # Fallback: fetch all deals and filter? Too slow.
            # Maybe the ticket passed IS the position ticket.
            return None

        # Find the exit deal (entry=0, exit=1)
        # Entry deal is usually first, Exit deal is last
        # We want the exit deal info
        exit_deal = None
        for d in deals:
            if d.entry == mt5.DEAL_ENTRY_OUT:
                exit_deal = d
                break
        
        if not exit_deal:
             # Maybe it was a partial close? or reversal?
             # Return the last deal found
             exit_deal = deals[-1]

        return {
            "ticket": exit_deal.ticket,
            "position_ticket": exit_deal.position_id,
            "symbol": exit_deal.symbol,
            "type": "BUY" if exit_deal.type == mt5.ORDER_TYPE_BUY else "SELL", # Actually deal type is Buy/Sell
            "volume": exit_deal.volume,
            "price_open": 0.0, # Not relevant for deal OUT, but we can try to find entry deal if needed
            "price_close": exit_deal.price,
            "profit": exit_deal.profit,
            "commission": exit_deal.commission,
            "swap": exit_deal.swap,
            "comment": exit_deal.comment,
            "reason": exit_deal.reason, # DEAL_REASON_SL, DEAL_REASON_TP, etc.
            "time": exit_deal.time,
        }
