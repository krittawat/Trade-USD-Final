"""
Execution Pipeline — ไปป์ไลน์เดียวสำหรับทุกโหมด (FULL PERSISTENCE).

flow: signal → gate → risk → order_plan → execute → postfill → persist → notify

กฎสำคัญ:
    - ไปป์ไลน์เดียวกัน ใช้กับทุกโหมด (LIVE/DRY/REPLAY/BACKTEST)
    - ทุกขั้นตอน log + persist ลง SQLite
    - Strategy ไม่สามารถข้ามด่าน risk gate ได้
"""

import asyncio
from typing import Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from app.brain.personality import PersonalityProfile

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.mode import TradingMode
from app.db.sqlite import SQLiteStore
from app.domain.enums import Action, BlockReason, TradeStage
from app.domain.models import AccountState, Decision, GateResult, OrderPlan, SymbolProfile, ExecutionResult, RegimeContext
from app.risk.gate import PreTradeGate
from app.risk.sizing import calculate_lot_size
from app.execution.adapter import ExecutionAdapter
from app.mt5.client import MT5Client
from app.services.session import get_current_session
from app.services.news_filter import NewsFilter
from app.brain.macro_bias import MacroBiasEngine
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
        adapter: ExecutionAdapter,  # Must provide an adapter (Live or Dry)
        mt5_client: MT5Client | None = None, # Legacy/Fallback
        gate: PreTradeGate | None = None,
        db: SQLiteStore | None = None,
        news_filter: NewsFilter | None = None,
        telegram: TelegramNotifier | None = None,
        risk_dampener=None,
        session_guard=None,
    ) -> None:
        self.settings = settings
        self.mode = TradingMode(settings.trading_mode)
        self.adapter = adapter
        self.mt5 = mt5_client # Kept for backward compat or direct data access if needed
        self.gate = gate or PreTradeGate(settings)
        self.db = db
        self.news_filter = news_filter or NewsFilter(settings.news_block_minutes)
        self.macro_bias_engine = MacroBiasEngine(impact_hours_validity=6)
        self.telegram = telegram
        self.risk_dampener = risk_dampener
        self.session_guard = session_guard
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
        candles=None,
        personality: Optional["PersonalityProfile"] = None,
        performance_metrics: dict | None = None,
        regime_context: RegimeContext | None = None,
        macro_bias_direction: str = "ANY",
    ) -> dict:
        """
        รันไปป์ไลน์ทั้งหมด: signal → gate → risk → execute → postfill → persist.

        Args:
            mt5_state: dict with keys: connected, market_open, spread, positions_count
                       ถ้าไม่ส่ง → จะเรียก Adapter เอง (fallback)
            personality: ข้อมูล personality (Optional) — ใช้ปรับความเสี่ยง
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
        signal_log_extra = {
            "symbol": symbol,
            "action": decision.action.value,
            "confidence": decision.confidence,
            "strategy": decision.strategy_name,
            "reason": decision.reason or "",
            "stage": "signal",
            "result": "ok",
            "mode": self.mode.value,
        }
        if decision.tags:
            signal_log_extra["tags"] = str(decision.tags)

        if decision.strategy_name == "CPU_Saver":
            logger.debug("pipeline_signal", extra=signal_log_extra)
        else:
            logger.info("pipeline_signal", extra=signal_log_extra)

        # HOLD → persist + return
        if decision.action == Action.HOLD:
            result["reason"] = decision.reason
            if decision.strategy_name != "CPU_Saver" or self._cycle % 60 == 0:
                await self._persist_trace(symbol, "signal", "hold",
                                    decision=decision, regime=regime, session=session)
            return result

        # --- 1.5: SNIPER ENTRY FILTER (Precision Check) ---
        if candles is not None and len(candles) >= 30:
            try:
                from app.risk.sniper_entry import SniperEntryFilter
                sniper = SniperEntryFilter(min_score=25)  # Lowered from 40 for more trade opportunities
                pv = "NEUTRAL"
                ps = 0
                if decision.tags:
                    pv = decision.tags.get("tv_power_verdict", "NEUTRAL")
                    ps = decision.tags.get("tv_power_score", 0)
                precision = sniper.check_precision(candles, decision.action, None, pv, ps)
                if not precision.passed:
                    result["reason"] = f"Sniper filter: score={precision.score}/100 < 25 ({', '.join(precision.reasons[:3])})"
                    logger.info("sniper_entry_blocked", extra={
                        "symbol": symbol,
                        "score": precision.score,
                        "reasons": precision.reasons[:5],
                        "strategy": decision.strategy_name,
                    })
                    await self._persist_trace(symbol, "sniper", "blocked",
                                        decision=decision, regime=regime, session=session,
                                        details=precision.details)
                    return result
                else:
                    # Log precision score for monitoring
                    logger.debug("sniper_entry_passed", extra={
                        "symbol": symbol,
                        "score": precision.score,
                        "details": precision.details,
                    })
            except Exception as e:
                logger.debug("sniper_entry_skip", extra={"error": str(e)})

        # --- 2: GATE ---
        result["stage"] = TradeStage.GATE.value

        # ─── ใช้ mt5_state ที่ส่งมา ถ้ามี (ลด calls) ───
        if mt5_state:
            mt5_connected = mt5_state.get("connected", False)
            market_open = mt5_state.get("market_open", True)
            current_spread = mt5_state.get("spread", 0.0)
            open_positions = mt5_state.get("positions_count", 0)
        else:
            # Fallback: เรียก Adapter
            mt5_connected = await asyncio.to_thread(self.adapter.is_connected)
            market_open = await asyncio.to_thread(self.adapter.is_market_open, symbol)
            # Spread might be tricky if adapter doesn't support get_spread directly, assume price diff
            bid, ask = await asyncio.to_thread(self.adapter.get_current_price, symbol)
            current_spread = 999.0
            if bid > 0 and ask > 0:
                current_spread = ask - bid
            
            # Count positions via adapter
            pos_list = await asyncio.to_thread(self.adapter.get_positions, symbol)
            open_positions = len(pos_list)

        # Total positions across ALL symbols
            total_positions = mt5_state.get("total_positions", 0) if mt5_state else (
                len(await asyncio.to_thread(self.adapter.get_positions))
            )

        # News filter
        news_safe = self.news_filter.is_safe(symbol) if self.news_filter else True
        
        # Use passed macro_bias_direction if provided, otherwise compute from news
        if macro_bias_direction == "ANY" and self.news_filter and hasattr(self.news_filter, "_news_events"):
            macro_bias_direction = self.macro_bias_engine.determine_bias(
                symbol, self.news_filter._news_events
            )

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
            candles=candles,
            performance_metrics=performance_metrics,
            regime_context=regime_context,
            macro_bias_direction=macro_bias_direction,
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
            await self._persist_trace(symbol, "gate", "blocked",
                                decision=decision, regime=regime, session=session,
                                details=gate_result.details)
            return result

        # --- 3: RISK ---
        result["stage"] = TradeStage.RISK.value

        bid, ask = await asyncio.to_thread(self.adapter.get_current_price, symbol)
        
        # ─── New Order Plan Builder (ATR-based SL/TP) ───
        from app.risk.order_plan import OrderPlanBuilder
        builder = OrderPlanBuilder(self.settings)
        
        plan_or_error = builder.build(
            decision=decision,
            profile=profile,
            account=account,
            candles=candles,
            mt5_price=(bid, ask) if bid > 0 else None,

            personality=personality,
            performance_metrics=performance_metrics,
        )

        if isinstance(plan_or_error, BlockReason):
            result["result"] = "blocked"
            result["reason"] = plan_or_error.value
            logger.warning("pipeline_risk_blocked", extra={
                "symbol": symbol,
                "reason": plan_or_error.value,
                "stage": "risk",
                "result": "blocked",
            })
            await self._persist_trace(symbol, "risk", "blocked",
                                decision=decision, regime=regime, session=session)
            return result
        
        order_plan: OrderPlan = plan_or_error

        # ═══════════════════════════════════════════════════════════
        # TP DIRECTION GUARD + R:R Guard — Critical Safety Check
        # ═══════════════════════════════════════════════════════════
        MIN_RR = 1.2
        ref_price = order_plan.entry_price or (bid if decision.action == Action.BUY else ask)
        is_buy = decision.action == Action.BUY

        if order_plan.stop_loss and order_plan.take_profit and ref_price:
            # ─── TP Direction Validation (BLOCK wrong-side TP) ───
            tp_wrong_side = False
            if is_buy and order_plan.take_profit <= ref_price:
                tp_wrong_side = True
            elif not is_buy and order_plan.take_profit >= ref_price:
                tp_wrong_side = True

            if tp_wrong_side:
                logger.critical("TP_WRONG_SIDE_BLOCKED", extra={
                    "symbol": symbol,
                    "action": decision.action.value,
                    "entry": ref_price,
                    "tp": order_plan.take_profit,
                    "sl": order_plan.stop_loss,
                    "strategy": decision.strategy_name,
                    "stage": "risk", "result": "blocked",
                    "ALERT": "TP on WRONG side of entry — trade blocked!",
                })
                result["result"] = "blocked"
                result["reason"] = f"TP wrong side: {decision.action.value} entry={ref_price:.2f} tp={order_plan.take_profit:.2f}"
                await self._persist_trace(symbol, "risk", "blocked",
                                    decision=decision, regime=regime, session=session)
                return result

            # ─── R:R Guard — Minimum 1.2 required ───
            sl_dist = abs(ref_price - order_plan.stop_loss)
            tp_dist = abs(order_plan.take_profit - ref_price)
            rr = tp_dist / sl_dist if sl_dist > 0 else 0
            if rr < MIN_RR:
                logger.warning("rr_too_low_blocked", extra={
                    "symbol": symbol, "rr": round(rr, 2),
                    "min_rr": MIN_RR, "sl_dist": round(sl_dist, 5),
                    "tp_dist": round(tp_dist, 5),
                    "stage": "risk", "result": "blocked",
                })
                result["result"] = "blocked"
                result["reason"] = f"R:R {rr:.2f} < {MIN_RR} minimum"
                await self._persist_trace(symbol, "risk", "blocked",
                                    decision=decision, regime=regime, session=session)
                return result

        # ─── Daily Loss Killswitch ───
        DAILY_MAX_LOSS_USC = 2000.0
        try:
            import MetaTrader5 as mt5
            from datetime import datetime, timezone
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            deals = await asyncio.to_thread(mt5.history_deals_get, today_start, datetime.now(timezone.utc))
            if deals:
                daily_pnl = sum(d.profit for d in deals if d.entry == 1)
                if daily_pnl < -DAILY_MAX_LOSS_USC:
                    logger.warning("daily_loss_killswitch", extra={
                        "symbol": symbol, "daily_pnl": round(daily_pnl, 2),
                        "threshold": -DAILY_MAX_LOSS_USC,
                        "stage": "risk", "result": "blocked",
                    })
                    result["result"] = "blocked"
                    result["reason"] = f"Daily loss killswitch: {daily_pnl:.0f} USC < -{DAILY_MAX_LOSS_USC:.0f}"
                    await self._persist_trace(symbol, "risk", "blocked",
                                        decision=decision, regime=regime, session=session)
                    return result
        except Exception as e:
            logger.error("daily_loss_check_error", extra={"error": str(e)})

        # ─── Margin Guard (Accurate MT5 API Margin) ───
        try:
            # We must use broker_lot for calc, not std_lot!
            from app.core.currency_adapter import get_adapter
            cur_adapter = get_adapter(self.settings)
            broker_lot_for_margin = cur_adapter.to_broker_lots(order_plan.lot_size, order_plan.symbol)
            
            # Fetch margin required from MT5
            margin_price = order_plan.entry_price or (bid if decision.action == Action.BUY else ask)
            req_margin = await asyncio.to_thread(
                self.adapter.calc_margin,
                action=order_plan.action, 
                symbol=order_plan.symbol, 
                volume=broker_lot_for_margin, 
                price=margin_price
            )
            
            if req_margin is not None and account.free_margin > 0:
                # Convert MT5 margin (Account Currency) to normalized USD if needed?
                # Actually, MT5 calc_margin returns the margin in Account Currency!
                # We need to map it using adapter.normalize_money() to compare with normalized free_margin
                norm_margin_req = cur_adapter.normalize_money(req_margin)
                
                max_margin_usage_pct = 25.0  # Max 25% of free margin
                if norm_margin_req > (account.free_margin * max_margin_usage_pct / 100):
                    logger.warning("margin_usage_exceeded", extra={
                        "symbol": symbol,
                        "margin_required": round(norm_margin_req, 2),
                        "free_margin": round(account.free_margin, 2),
                        "usage_pct": round(norm_margin_req / account.free_margin * 100, 2),
                        "max_usage_pct": max_margin_usage_pct,
                        "stage": "risk",
                        "result": "blocked",
                    })
                    result["result"] = "blocked"
                    result["reason"] = BlockReason.LOT_SIZE_INVALID.value
                    await self._persist_trace(symbol, "risk", "blocked",
                                        decision=decision, regime=regime, session=session)
                    return result
        except Exception as e:
            logger.error("margin_guard_error", extra={"error": str(e), "symbol": symbol})

        # ─── Correlation Guard ───
        try:
            from app.risk.correlation_guard import adjust_lot_for_correlation
            from app.core.currency_adapter import get_adapter
            
            positions_list = await asyncio.to_thread(self.adapter.get_positions) # Use Adapter!
            cur_adapter = get_adapter(self.settings)
            min_lot = cur_adapter.get_min_lot()
            
            adjusted_lot, corr_reason = adjust_lot_for_correlation(
                symbol, order_plan.lot_size, positions_list, min_lot=min_lot
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

        # ─── Risk Dampener ───
        if self.risk_dampener and self.risk_dampener.enabled:
            mult = self.risk_dampener.get_multiplier(symbol)
            if mult < 1.0:
                original_lot = order_plan.lot_size
                from app.mt5.broker_specs import round_lot
                order_plan.lot_size = round_lot(
                    order_plan.lot_size * mult,
                    profile.volume_step,
                    profile.volume_min,
                    profile.volume_max,
                )
                logger.info("pipeline_lot_dampened", extra={
                    "symbol": symbol,
                    "original_lot": original_lot,
                    "dampened_lot": order_plan.lot_size,
                    "multiplier": mult,
                    "stage": "risk",
                })

        result["order_plan"] = order_plan
        result["stage"] = TradeStage.ORDER.value

        # --- 4: EXECUTE ---
        # Execute via Adapter regardless of mode (Adapter handles implementation)
        
        try:
            exec_result: ExecutionResult = await asyncio.to_thread(self.adapter.execute_order, order_plan)
            
            if exec_result.error:
                 raise Exception(exec_result.error)
                 
            result["ticket"] = exec_result.ticket
            result["entry_price"] = exec_result.price  # Return entry price for tracking
            result["broker_volume"] = exec_result.volume # Store the raw broker volume
            
            logger.info("pipeline_order_executed", extra={
                "symbol": symbol,
                "ticket": exec_result.ticket,
                "lot": order_plan.lot_size,
                "price": exec_result.price,
                "stage": "order",
                "mode": self.mode.value,
                "result": "ok",
            })
            
            
        except Exception as e:
            result["result"] = "error"
            result["reason"] = str(e)
            logger.error("pipeline_order_failed", extra={
                "symbol": symbol,
                "error": str(e),
                "stage": "order",
                "result": "error",
            }, exc_info=True)
            await self._persist_trace(symbol, "order", "error",
                                decision=decision, regime=regime, session=session,
                                details={"error": str(e), "strategy_reason": decision.reason})
            return result

        # --- 5: POSTFILL (LIVE only validation) ---
        result["stage"] = TradeStage.POSTFILL.value

        if self.mode == TradingMode.LIVE and result["ticket"] and self.mt5:
            # ─── Cache PostFillGuard — ไม่สร้างใหม่ทุก trade ───
            if self._postfill_guard is None:
                from app.risk.postfill import PostFillGuard
                self._postfill_guard = PostFillGuard(self.mt5)
            # Use fire-and-forget or await? Original was await
            # Safe to await as it should be fast
            try:
                await self._postfill_guard.verify_single(result["ticket"], order_plan.stop_loss)
            except Exception as e:
                 logger.error("postfill_guard_error", extra={"error": str(e)})

        # --- 6: PERSIST ---
        # Use actual fill price from execution result for journal + notifications.
        entry_price = float(result.get("entry_price", 0.0) or 0.0)
        
        await self._persist_trace(symbol, "order", "ok",
                            decision=decision, regime=regime, session=session,
                            details={"lot": order_plan.lot_size, "risk_pct": order_plan.risk_pct})

        # Save trade to journal
        if self.db:
            await asyncio.to_thread(
                self.db.save_trade,
                symbol=symbol,
                action=order_plan.action.value,
                lot_size=order_plan.lot_size,
                entry_price=entry_price,
                stop_loss=order_plan.stop_loss,
                take_profit=order_plan.take_profit or 0.0,
                risk_usd=order_plan.risk_usd,
                risk_pct=order_plan.risk_pct,
                strategy_name=decision.strategy_name,
                regime=regime,
                session=session if isinstance(session, str) else session.value,
                ticket=result.get("ticket") or 0,
                mode=self.mode.value,
                tags=decision.tags,
            )

        # --- 7: TELEGRAM NOTIFY (fire-and-forget) ---
        if self.telegram:
            asyncio.create_task(self.telegram.notify_trade_open(
                symbol=symbol,
                action=order_plan.action.value,
                lot_size=result.get("broker_volume", order_plan.lot_size), # Use broker volume
                entry_price=entry_price,
                stop_loss=order_plan.stop_loss,
                take_profit=order_plan.take_profit or 0.0,
                risk_usd=order_plan.risk_usd,
                risk_pct=order_plan.risk_pct,
                strategy_name=decision.strategy_name,
                ticket=result.get("ticket"),
                mode=self.mode.value,
            ))

        result["result"] = "ok"
        result["reason"] = f"Order executed: {order_plan.lot_size} lot @ {order_plan.risk_pct}% risk"

        # ─── Session Guard: บันทึกเทรดที่เปิดสำเร็จ ───
        if self.session_guard:
            self.session_guard.record_trade(symbol, session)

        return result

    async def _persist_trace(
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
            await asyncio.to_thread(
                self.db.save_decision_trace,
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
