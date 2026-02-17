"""
MT5 Client — เชื่อมต่อ MetaTrader 5 จริงสำหรับเทรด.

หน้าที่:
    - เชื่อมต่อ/ตัดการเชื่อมต่อ MT5
    - ดึงข้อมูลสัญลักษณ์ (symbol info) → SymbolProfile
    - ส่งคำสั่งเทรด (order send)
    - แก้ไขออเดอร์ (modify SL/TP)
    - ปิดออเดอร์
    - ดึงสถานะบัญชี → AccountState

กฎสำคัญ:
    - ห้ามส่งออเดอร์จริงในโหมด DRY_RUN
    - ทุกออเดอร์ต้องมี SL
    - Log ทุก action ด้วย structured logger
"""

from typing import Optional

import MetaTrader5 as mt5

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.errors import ConnectionError, OrderError
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
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._connected = False
        self._initial_balance: float = 0.0  # เก็บ balance ตอนเริ่ม (สำหรับ capital floor)
        # Performance: symbol profile cache with TTL
        self._symbol_cache: dict[str, tuple[float, SymbolProfile]] = {}  # symbol -> (ts, profile)
        self._symbol_cache_ttl: float = 60.0  # cache 60 seconds

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

        # ดึง initial balance สำหรับ capital floor
        info = mt5.account_info()
        if info:
            self._initial_balance = info.balance
            logger.info("mt5_connected", extra={
                "login": info.login,
                "server": info.server,
                "balance": info.balance,
                "equity": info.equity,
                "leverage": info.leverage,
            })
        else:
            logger.warning("mt5_connected_no_account_info")

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
        ดึงสถานะบัญชีปัจจุบันจาก MT5 จริง.

        Returns:
            AccountState: ข้อมูลบัญชี (balance, equity, margin, ฯลฯ)
        """
        info = mt5.account_info()
        if info is None:
            logger.error("mt5_account_info_failed", extra={"error": str(mt5.last_error())})
            return AccountState(balance=0.0, equity=0.0)

        # คำนวณ daily PL (ใช้ balance - initial)
        positions = mt5.positions_get()
        floating_pl = sum(p.profit for p in positions) if positions else 0.0

        return AccountState(
            balance=info.balance,
            equity=info.equity,
            margin=info.margin,
            free_margin=info.margin_free,
            floating_pl=floating_pl,
            open_positions=len(positions) if positions else 0,
            daily_pl=0.0,  # TODO: track daily PL in SQLite
            initial_balance=self._initial_balance,
        )

    def get_symbol_info(self, symbol: str) -> Optional[SymbolProfile]:
        """
        ดึงข้อมูลสัญลักษณ์เทรดจาก MT5 → SymbolProfile.
        Cached 60 วินาที — profile ไม่เปลี่ยนระหว่าง session.

        Returns:
            SymbolProfile: ข้อมูลสัญลักษณ์พร้อมใช้
            None: ถ้าสัญลักษณ์ไม่มีหรือเทรดไม่ได้
        """
        import time as _time
        now = _time.monotonic()

        # Check cache
        cached = self._symbol_cache.get(symbol)
        if cached:
            cache_ts, profile = cached
            if now - cache_ts < self._symbol_cache_ttl:
                return profile

        info = mt5.symbol_info(symbol)
        if info is None:
            logger.warning("symbol_not_found", extra={"symbol": symbol})
            return None

        # ตรวจว่าสัญลักษณ์เทรดได้ (visible + trade allowed)
        if not info.visible:
            # ลองเปิดให้เห็น
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
            if info is None:
                return None

        profile = SymbolProfile(
            symbol=symbol,
            contract_size=info.trade_contract_size,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
            point=info.point,
            digits=info.digits,
            spread_avg=info.spread * info.point,  # spread ในหน่วยราคา
            spread_max_allowed=info.spread * info.point * 3,  # default max = 3x current
            is_active=info.trade_mode != 0,
        )

        # Update cache
        self._symbol_cache[symbol] = (now, profile)
        return profile

    def get_current_price(self, symbol: str) -> tuple[float, float]:
        """
        ดึงราคา bid/ask ปัจจุบัน.

        Returns:
            (bid, ask) tuple
        """
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error("tick_fetch_failed", extra={"symbol": symbol})
            return 0.0, 0.0
        return tick.bid, tick.ask

    def get_current_spread(self, symbol: str) -> float:
        """ดึง spread ปัจจุบัน (ในหน่วยราคา)."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return 999.0  # spread สูงมาก → จะถูกบล็อก
        return tick.ask - tick.bid

    def is_market_open(self, symbol: str) -> bool:
        """ตรวจว่าตลาดเปิดอยู่สำหรับ symbol นี้."""
        info = mt5.symbol_info(symbol)
        if info is None:
            return False
        # trade_mode: 0=disabled, other=enabled
        return info.trade_mode != 0

    def get_positions(self, symbol: str | None = None) -> list[dict]:
        """
        ดึง positions ที่เปิดอยู่ (จาก MT5 จริง).

        Args:
            symbol: กรองตามสัญลักษณ์ (None = ทั้งหมด)
        """
        if symbol:
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()

        if positions is None:
            return []

        return [
            {
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
                "magic": p.magic,
                "time": p.time,
            }
            for p in positions
        ]

    def count_positions(self, symbol: str) -> int:
        """นับจำนวน positions ที่เปิดอยู่สำหรับ symbol."""
        positions = mt5.positions_get(symbol=symbol)
        return len(positions) if positions else 0

    def get_batch_mt5_state(self, symbols: list[str]) -> dict[str, dict]:
        """
        ดึง MT5 state ทุก symbol ในครั้งเดียว — ลด IPC calls.

        แทนที่จะเรียก is_market_open + get_current_spread + count_positions
        ต่อ symbol (= 3×N calls) → ดึง positions ครั้งเดียว + tick ต่อ symbol.

        Returns:
            dict[symbol -> {connected, market_open, spread, positions_count, total_positions}]
        """
        connected = self.is_connected()
        result: dict[str, dict] = {}

        # ดึง ALL positions ครั้งเดียว
        all_positions = mt5.positions_get()
        total_positions = len(all_positions) if all_positions else 0

        # นับ positions ต่อ symbol
        pos_by_symbol: dict[str, int] = {}
        if all_positions:
            for p in all_positions:
                pos_by_symbol[p.symbol] = pos_by_symbol.get(p.symbol, 0) + 1

        for symbol in symbols:
            # tick → spread + market check (1 call ต่อ symbol)
            tick = mt5.symbol_info_tick(symbol)
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
                "positions_count": pos_by_symbol.get(symbol, 0),
                "total_positions": total_positions,
            }

        return result

    def send_order(self, plan: OrderPlan) -> dict:
        """
        ส่งคำสั่งเทรดจาก OrderPlan — ผ่าน MT5 จริง.

        กฎ:
            - ต้องผ่าน ExecutionPipeline เท่านั้น
            - OrderPlan ต้องมี SL เสมอ

        Raises:
            OrderError: ถ้าส่งคำสั่งไม่สำเร็จ
        """
        if plan.stop_loss <= 0:
            raise OrderError("SL is mandatory — ห้ามส่งออเดอร์ไม่มี SL", {
                "symbol": plan.symbol,
            })

        # ดึง bid/ask สำหรับกำหนดราคา
        tick = mt5.symbol_info_tick(plan.symbol)
        if tick is None:
            raise OrderError("Cannot get tick price", {"symbol": plan.symbol})

        # กำหนดราคาเข้า
        if plan.action == Action.BUY:
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY
        else:
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL

        # สร้าง request dict
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": plan.symbol,
            "volume": plan.lot_size,
            "type": order_type,
            "price": price,
            "sl": plan.stop_loss,
            "deviation": 20,  # slippage allowance
            "magic": 888888,  # magic number สำหรับระบุ bot
            "comment": plan.comment or f"AG|{plan.strategy_name}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # TP (optional)
        if plan.take_profit and plan.take_profit > 0:
            request["tp"] = plan.take_profit

        logger.info("send_order", extra={
            "symbol": plan.symbol,
            "action": plan.action.value if hasattr(plan.action, 'value') else str(plan.action),
            "lot": plan.lot_size,
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
                "symbol": plan.symbol, "error": str(err),
            })

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise OrderError(
                f"Order rejected: {result.comment} (code={result.retcode})",
                {"symbol": plan.symbol, "retcode": result.retcode, "comment": result.comment},
            )

        logger.info("order_filled", extra={
            "ticket": result.order,
            "symbol": plan.symbol,
            "lot": plan.lot_size,
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
        แก้ไข Stop Loss ของ position จริง.

        ใช้สำหรับ Break-Even และ trailing stop.
        """
        # ดึงข้อมูล position
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.error("position_not_found_for_modify", extra={"ticket": ticket})
            return False

        pos = positions[0]

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": new_sl,
            "tp": new_tp if new_tp > 0 else pos.tp,
        }

        result = mt5.order_send(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("modify_sl_failed", extra={
                "ticket": ticket,
                "new_sl": new_sl,
                "error": result.comment if result else str(mt5.last_error()),
            })
            return False

        logger.info("modify_sl_ok", extra={
            "ticket": ticket,
            "new_sl": new_sl,
            "stage": "be_move",
            "result": "ok",
        })
        return True

    def close_position(self, ticket: int, reason: str = "") -> bool:
        """
        ปิด position จริง.

        Args:
            ticket: หมายเลข position
            reason: เหตุผลที่ปิด (สำหรับ log)
        """
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            logger.warning("position_not_found_for_close", extra={"ticket": ticket})
            return False

        pos = positions[0]
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            logger.error("tick_failed_for_close", extra={"ticket": ticket})
            return False

        # Close = ส่ง counter order
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        close_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": close_price,
            "deviation": 20,
            "magic": 888888,
            "comment": f"AG_CLOSE|{reason}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("close_position_failed", extra={
                "ticket": ticket,
                "reason": reason,
                "error": result.comment if result else str(mt5.last_error()),
            })
            return False

        logger.info("position_closed", extra={
            "ticket": ticket,
            "reason": reason,
            "profit": pos.profit,
            "stage": "close",
            "result": "ok",
        })
        return True

    def get_closed_deal_info(self, position_ticket: int) -> dict | None:
        """
        ดึงข้อมูล deal ที่ปิดจาก MT5 history — ใช้สำหรับ Telegram notification.

        Args:
            position_ticket: หมายเลข position ที่ถูกปิด

        Returns:
            dict with {symbol, type, volume, profit, price_open, price_close, comment}
            None ถ้าหาไม่เจอ
        """
        from datetime import datetime, timezone, timedelta

        try:
            # ดึง deals ย้อนหลัง 24 ชม.
            now = datetime.now(timezone.utc)
            from_date = now - timedelta(days=1)

            deals = mt5.history_deals_get(from_date, now, position=position_ticket)
            if not deals:
                return None

            # หา deal out (close) — type = DEAL_TYPE_BUY/SELL ที่เป็น exit
            # Deal สุดท้ายมักเป็น close deal
            for deal in reversed(deals):
                if deal.entry == mt5.DEAL_ENTRY_OUT or deal.entry == mt5.DEAL_ENTRY_INOUT:
                    return {
                        "symbol": deal.symbol,
                        "type": "BUY" if deal.type == mt5.DEAL_TYPE_BUY else "SELL",
                        "volume": deal.volume,
                        "profit": deal.profit,
                        "price_open": deal.price,  # close price ของ deal
                        "comment": deal.comment,
                        "ticket": deal.ticket,
                        "position_id": deal.position_id,
                    }

            return None
        except Exception as e:
            logger.debug("get_closed_deal_error", extra={
                "position": position_ticket,
                "error": str(e),
            })
            return None
