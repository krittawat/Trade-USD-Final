"""
Execution Pipeline — ไปป์ไลน์เดียวสำหรับทุกโหมด (FULL PERSISTENCE).

flow: signal → gate → risk → order_plan → execute → postfill → persist → notify

กฎสำคัญ:
    - ไปป์ไลน์เดียวกัน ใช้กับทุกโหมด (LIVE/DRY/REPLAY/BACKTEST)
    - ทุกขั้นตอน log + persist ลง SQLite
    - Strategy ไม่สามารถข้ามด่าน risk gate ได้
"""

import asyncio

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.mode import TradingMode
from app.db.sqlite import SQLiteStore
from app.domain.enums import Action, BlockReason, TradeStage
from app.domain.models import AccountState, Decision, GateResult, OrderPlan, SymbolProfile
from app.risk.gate import PreTradeGate
from app.risk.sizing import calculate_lot_size
from app.mt5.client import MT5Client
from app.services.session import get_current_session
from app.services.news_filter import NewsFilter
from app.services.telegram import TelegramNotifier

logger = get_logger(__name__)


class ExecutionPipeline:
    """
    Unified Execution Pipeline — หัวใจของระบบเทรด.
    ทุก trade decision ต้องผ่านไปป์ไลน์นี้.
    ใช้โค้ดเดียวกันสำหรับ LIVE, DRY_RUN, REPLAY, BACKTEST.
    """

    def __init__(
        self,
        settings: Settings,
        mt5_client: MT5Client | None = None,
        gate: PreTradeGate | None = None,
        db: SQLiteStore | None = None,
        news_filter: NewsFilter | None = None,
        telegram: TelegramNotifier | None = None,
    ) -> None:
        self.settings = settings
        self.mode = TradingMode(settings.trading_mode)
        self.mt5 = mt5_client
        self.gate = gate or PreTradeGate(settings)
        self.db = db
        self.news_filter = news_filter or NewsFilter(settings.news_block_minutes)
        self.telegram = telegram
        self._cycle = 0
        # ─── Cache PostFillGuard — ไม่สร้างใหม่ทุก execute() ───
        self._postfill_guard = None

    def set_cycle(self, cycle: int) -> None:
        self._cycle = cycle

    async def execute(
        self,
        decision: Decision,
        profile: SymbolProfile,
        account: AccountState,
        regime: str = "",
        session: str = "",
        mt5_state: dict | None = None,
    ) -> dict:
        """
        รันไปป์ไลน์ทั้งหมด: signal → gate → risk → execute → postfill → persist.

        Performance:
            - รับ session จากภายนอก (ไม่เรียก get_current_session() ซ้ำ)
            - รับ mt5_state จากภายนอก (ลด MT5 calls)
            - PostFillGuard ถูก cache เป็น instance variable

        Args:
            mt5_state: dict with keys: connected, market_open, spread, positions_count
                       ถ้าไม่ส่ง → จะเรียก MT5 เอง (fallback)
        """
        result = {
            "stage": TradeStage.SIGNAL.value,
            "result": "ok",
            "decision": decision,
            "order_plan": None,
            "gate_result": None,
            "reason": "",
            "ticket": None,
        }

        symbol = decision.symbol
        # ─── ใช้ session ที่ส่งมา ไม่เรียกซ้ำ ───
        if not session:
            session = get_current_session().value

        # --- 1: SIGNAL ---
        logger.info("pipeline_signal", extra={
            "symbol": symbol,
            "action": decision.action.value,
            "confidence": decision.confidence,
            "strategy": decision.strategy_name,
            "stage": "signal",
            "result": "ok",
            "mode": self.mode.value,
        })

        # HOLD → persist + return
        if decision.action == Action.HOLD:
            result["reason"] = decision.reason
            self._persist_trace(symbol, "signal", "hold",
                                decision=decision, regime=regime, session=session)
            return result

        # --- 2: GATE ---
        result["stage"] = TradeStage.GATE.value

        # ─── ใช้ mt5_state ที่ส่งมา ถ้ามี (ลด 4 MT5 calls → 0) ───
        if mt5_state:
            mt5_connected = mt5_state.get("connected", False)
            market_open = mt5_state.get("market_open", True)
            current_spread = mt5_state.get("spread", 0.0)
            open_positions = mt5_state.get("positions_count", 0)
        else:
            # Fallback: เรียก MT5 ตรง
            mt5_connected = self.mt5.is_connected() if self.mt5 else False
            market_open = self.mt5.is_market_open(symbol) if self.mt5 else True
            current_spread = self.mt5.get_current_spread(symbol) if self.mt5 else 0.0
            open_positions = self.mt5.count_positions(symbol) if self.mt5 else 0

        # Total positions across ALL symbols
        total_positions = mt5_state.get("total_positions", 0) if mt5_state else (
            len(self.mt5.get_all_positions()) if self.mt5 and hasattr(self.mt5, 'get_all_positions') else open_positions
        )

        # News filter
        news_safe = self.news_filter.is_safe(symbol) if self.news_filter else True

        gate_result = self.gate.check(
            decision=decision,
            profile=profile,
            account=account,
            mt5_connected=mt5_connected,
            market_open=market_open,
            current_spread=current_spread,
            current_session=session,
            news_safe=news_safe,
            open_positions_count=open_positions,
            total_positions_count=total_positions,
        )
        result["gate_result"] = gate_result

        if not gate_result.passed:
            result["result"] = "blocked"
            result["reason"] = ", ".join([r.value for r in gate_result.reasons])
            logger.warning("pipeline_blocked", extra={
                "symbol": symbol,
                "reasons": result["reason"],
                "details": gate_result.details,
                "stage": "gate",
                "result": "blocked",
            })
            self._persist_trace(symbol, "gate", "blocked",
                                decision=decision, regime=regime, session=session,
                                details=gate_result.details)
            return result

        # --- 3: RISK ---
        result["stage"] = TradeStage.RISK.value

        entry_price = None
        if self.mt5 and self.mt5.is_connected():
            bid, ask = self.mt5.get_current_price(symbol)
            entry_price = ask if decision.action == Action.BUY else bid

        sizing_result = calculate_lot_size(
            decision=decision,
            profile=profile,
            account=account,
            settings=self.settings,
            entry_price=entry_price,
        )

        if isinstance(sizing_result, BlockReason):
            result["result"] = "blocked"
            result["reason"] = sizing_result.value
            logger.warning("pipeline_sizing_blocked", extra={
                "symbol": symbol,
                "reason": sizing_result.value,
                "stage": "risk",
                "result": "blocked",
            })
            self._persist_trace(symbol, "risk", "blocked",
                                decision=decision, regime=regime, session=session)
            return result

        order_plan: OrderPlan = sizing_result

        # ─── Correlation Guard: ลด lot ถ้ามี correlated positions ───
        try:
            from app.risk.correlation_guard import adjust_lot_for_correlation
            positions_list = []
            if self.mt5 and self.mt5.is_connected():
                positions_list = self.mt5.get_positions() or []
            adjusted_lot, corr_reason = adjust_lot_for_correlation(
                symbol, order_plan.lot_size, positions_list,
            )
            if adjusted_lot != order_plan.lot_size:
                logger.info("lot_correlation_adjusted", extra={
                    "symbol": symbol,
                    "original": order_plan.lot_size,
                    "adjusted": adjusted_lot,
                    "reason": corr_reason,
                })
                order_plan.lot_size = adjusted_lot
        except Exception as e:
            logger.debug("correlation_guard_skip", extra={"error": str(e)})

        result["order_plan"] = order_plan
        result["stage"] = TradeStage.ORDER.value

        # --- 4: EXECUTE ---
        if self.mode.can_send_orders:
            # LIVE → send real order
            try:
                if self.mt5:
                    order_result = self.mt5.send_order(order_plan)
                    result["ticket"] = order_result.get("ticket")
                    logger.info("pipeline_order_sent", extra={
                        "symbol": symbol,
                        "ticket": result["ticket"],
                        "lot": order_plan.lot_size,
                        "price": order_result.get("price"),
                        "stage": "order",
                        "mode": "LIVE",
                        "result": "ok",
                    })
                else:
                    logger.error("pipeline_no_mt5_client", extra={"symbol": symbol})
                    result["result"] = "error"
                    result["reason"] = "MT5 client not available for LIVE mode"
                    return result
            except Exception as e:
                result["result"] = "error"
                result["reason"] = str(e)
                logger.error("pipeline_order_failed", extra={
                    "symbol": symbol,
                    "error": str(e),
                    "stage": "order",
                    "result": "error",
                }, exc_info=True)
                self._persist_trace(symbol, "order", "error",
                                    decision=decision, regime=regime, session=session)
                return result
        else:
            # DRY_RUN / BACKTEST → virtual trade
            logger.info("pipeline_virtual_trade", extra={
                "symbol": symbol,
                "action": order_plan.action.value,
                "lot": order_plan.lot_size,
                "sl": order_plan.stop_loss,
                "tp": order_plan.take_profit,
                "risk_usd": order_plan.risk_usd,
                "risk_pct": order_plan.risk_pct,
                "stage": "order",
                "mode": self.mode.value,
                "result": "ok",
            })

        # --- 5: POSTFILL (LIVE only) ---
        result["stage"] = TradeStage.POSTFILL.value

        if self.mode.can_send_orders and result["ticket"] and self.mt5:
            # ─── Cache PostFillGuard — ไม่สร้างใหม่ทุก trade ───
            if self._postfill_guard is None:
                from app.risk.postfill import PostFillGuard
                self._postfill_guard = PostFillGuard(self.mt5)
            await self._postfill_guard.verify_single(result["ticket"], order_plan.stop_loss)

        # --- 6: PERSIST ---
        self._persist_trace(symbol, "order", "ok",
                            decision=decision, regime=regime, session=session,
                            details={"lot": order_plan.lot_size, "risk_pct": order_plan.risk_pct})

        # Save trade to journal
        if self.db:
            self.db.save_trade(
                symbol=symbol,
                action=order_plan.action.value,
                lot_size=order_plan.lot_size,
                entry_price=entry_price or 0.0,
                stop_loss=order_plan.stop_loss,
                take_profit=order_plan.take_profit or 0.0,
                risk_usd=order_plan.risk_usd,
                risk_pct=order_plan.risk_pct,
                strategy_name=decision.strategy_name,
                regime=regime,
                session=session.value,
                ticket=result.get("ticket") or 0,
                mode=self.mode.value,
                tags=decision.tags,
            )

        # --- 7: TELEGRAM NOTIFY (fire-and-forget) ---
        if self.telegram:
            asyncio.create_task(self.telegram.notify_trade_open(
                symbol=symbol,
                action=order_plan.action.value,
                lot_size=order_plan.lot_size,
                entry_price=entry_price or 0.0,
                stop_loss=order_plan.stop_loss,
                take_profit=order_plan.take_profit or 0.0,
                risk_usd=order_plan.risk_usd,
                risk_pct=order_plan.risk_pct,
                strategy_name=decision.strategy_name,
                ticket=result.get("ticket"),
                mode=self.mode.value,
            ))

        result["result"] = "ok"
        result["reason"] = f"Order {'sent' if self.mode.can_send_orders else 'simulated'}: {order_plan.lot_size} lot @ {order_plan.risk_pct}% risk"
        return result

    def _persist_trace(
        self,
        symbol: str,
        stage: str,
        result: str,
        decision: Decision | None = None,
        regime: str = "",
        session: str = "",
        details: dict | None = None,
    ) -> None:
        """บันทึก decision trace ลง SQLite."""
        if not self.db:
            return
        try:
            self.db.save_decision_trace(
                symbol=symbol,
                stage=stage,
                result=result,
                action=decision.action.value if decision else "",
                confidence=decision.confidence if decision else 0.0,
                strategy_name=decision.strategy_name if decision else "",
                reason=decision.reason if decision else "",
                details=details,
                regime=regime,
                session=session,
                cycle=self._cycle,
                mode=self.mode.value,
            )
        except Exception as e:
            logger.error("trace_persist_error", extra={"error": str(e)})
