"""
Symbol Processor — แยกจาก MasterLoop._process_symbol().

จัดการ per-symbol analysis + execution pipeline:
    1. Profile lookup
    2. Multi-timeframe candle fetch
    3. Candle dedup
    4. Regime analysis + intelligence
    5. Pattern detection + tick volume analysis
    6. Strategy selection + confidence boosting
    7. Pipeline execution
    8. Throttle tracking + outcome recording
    9. Shadow runner
"""

import asyncio
import inspect
import time
import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import RegimeContext, Decision

logger = get_logger(__name__)


class SymbolProcessor:
    """
    Processes one symbol per cycle: analyze market → select strategy → execute.

    Extracted from MasterLoop._process_symbol() for maintainability.
    All dependencies injected via set_dependencies().
    """

    def __init__(self):
        # Dependencies (set from MasterLoop after construction)
        self.mt5 = None
        self.factory = None
        self.db = None
        self.pipeline = None
        self.settings = None

        # Intelligence modules
        self.brain_memory = None
        self.trainer = None
        self.recommender = None
        self.ml_pattern_learner = None
        self.sentiment_aggregator = None
        self.personality_engine = None
        self._regime_engine = None

        # Smart Bot V2 — โมดูลเพิ่มเติม
        self.trade_coach = None
        self.mtf_gate = None
        self._pattern_detector = None
        self._pattern_scorer = None
        self._tick_volume_analyzer = None
        self._order_flow_analyzer = None
        self._mtf_engine = None
        self._entry_optimizer = None
        self._outcome_analyzer = None
        self.backtest_router = None
        self.shadow_runner = None
        self.auto_coach = None
        self.online_learner = None

        # State (shared with MasterLoop)
        self._last_candle_hash: dict[str, str] = {}
        self._trade_throttle: dict = {
            "hourly_count": 0,
            "last_hour_reset": time.monotonic(),
            "symbol_last_trade": {},
        }
        self._regime_intel: dict[str, object] = {}
        self._pattern_signals: dict[str, list] = {}
        self._tick_volume_signals: dict[str, object] = {}
        self._vp_signals: dict[str, dict] = {}
        self._shadow_candle_cache: dict = {}
        self._last_analyzed_candle_time: dict[str, str] = {}
        self._h1_candle_cache: dict[str, pd.DataFrame] = {}
        self._last_h1_fetch_time: dict[str, float] = {}
        self._needed_tfs: set[str] = {"M5", "H1", "H4"}  # M5 + H1 + H4 สำหรับ MTF trend filter

    def set_dependencies(self, **deps):
        """Set dependencies from MasterLoop."""
        for key, val in deps.items():
            setattr(self, key, val)

    def reset_hourly_throttle(self):
        """Reset hourly trade counter (called from cycle loop)."""
        if time.monotonic() - self._trade_throttle["last_hour_reset"] > 3600:
            self._trade_throttle["hourly_count"] = 0
            self._trade_throttle["last_hour_reset"] = time.monotonic()

    async def process(
        self,
        symbol: str,
        account,
        session_value: str,
        mt5_state: dict | None,
        cycle_count: int,
        candle_map: dict,
        regime_contexts: dict,
        last_decisions: dict,
        tracked_positions: dict,
        latest_coach_report: dict | None,
        mode,
        is_shadow_only: bool = False,
        macro_bias_direction: str = "ANY",
    ) -> None:
        """
        Run full analysis + execution for 1 symbol.

        This is the extracted MasterLoop._process_symbol().
        """
        from app.mt5.market_data import fetch_candles
        from app.services.session import get_current_session
        from app.strategy.invoker import analyze_with_fallback, normalize_strategy_params

        # ─── 1. Profile ───
        profile = None
        if self.mt5 and self.mt5.is_connected():
            profile = await asyncio.to_thread(self.mt5.get_symbol_info, symbol)
        if profile is None:
            from app.domain.models import SymbolProfile
            profile = SymbolProfile(symbol=symbol)

        # ─── 1.5 Tick Ingestion ───
        if self.db and self.mt5 and self.mt5.is_connected():
            await self._ingest_market_data(symbol, cycle_count)

        # ─── 2. Multi-timeframe candles ───
        
        primary_tf = "M5"
        if "XAU" in symbol:
            primary_tf = "H1"
        elif "XAG" in symbol:
            primary_tf = "M3"
            
        needed_tfs = self._needed_tfs.copy()
        needed_tfs.add(primary_tf)
        
        def _fetch_all_candles():
            broker_symbol = symbol
            if self.mt5 and hasattr(self.mt5, 'adapter'):
                broker_symbol = self.mt5.adapter.map_symbol(symbol)
            result = {}
            for tf in needed_tfs:
                if tf == "H1":
                    # I/O Optimizer: Fetch H1 only every 5 minutes (300s) to save MT5 stress
                    if time.monotonic() - self._last_h1_fetch_time.get(symbol, 0) < 300:
                        cached_h1 = self._h1_candle_cache.get(symbol)
                        if cached_h1 is not None and len(cached_h1) >= 30:
                            result[tf] = cached_h1
                            continue
                            
                tf_key, cnt = candle_map.get(tf, ("M5", 250))
                c = fetch_candles(broker_symbol, timeframe=tf_key, count=cnt, cycle=cycle_count)
                if c is not None and len(c) >= 30:
                    result[tf] = c
                    if tf == "H1":
                        self._h1_candle_cache[symbol] = c
                        self._last_h1_fetch_time[symbol] = time.monotonic()
            return result

        candles_by_tf = await asyncio.to_thread(_fetch_all_candles)
        m5_candles = candles_by_tf.get("M5")
        primary_candles = candles_by_tf.get(primary_tf, m5_candles)

        if primary_candles is None or len(primary_candles) < 30:
            logger.warning("symbol_skipped_no_candles", extra={
                "symbol": symbol, "reason": "insufficient_candles",
                "candle_count": len(primary_candles) if primary_candles is not None else 0,
                "timeframe": primary_tf,
                "cycle": cycle_count,
            })
            last_decisions[symbol] = {"result": "blocked", "reason": f"insufficient_{primary_tf}_candles"}
            return

        self._shadow_candle_cache[symbol] = primary_candles

        # ─── 3. Candle dedup ───
        last_row = primary_candles.iloc[-1]
        candle_hash = f"{last_row.name}:{last_row['close']}"
        if candle_hash == self._last_candle_hash.get(symbol):
            if cycle_count % 20 == 0:
                logger.debug("candle_dedup_skip", extra={"symbol": symbol, "cycle": cycle_count})
            return
        self._last_candle_hash[symbol] = candle_hash

        # ─── 3.5 New Candle Detection ───
        is_new_candle = False
        candle_time_str = str(last_row.name)
        if candle_time_str != self._last_analyzed_candle_time.get(symbol):
            is_new_candle = True
            self._last_analyzed_candle_time[symbol] = candle_time_str

        # ─── 4. Regime ───
        regime_recommended = None
        regime_lot_multiplier = 1.0
        regime_param_overrides: dict = {}

        if self._regime_engine:
            if is_new_candle or symbol not in self._regime_intel:
                regime_intel = self._regime_engine.analyze(
                    symbol=symbol, candles=primary_candles,
                    session=session_value or "CLOSED",
                    profile=getattr(self, '_personality_cache', {}).get(symbol),
                )
                self._regime_intel[symbol] = regime_intel
            else:
                regime_intel = self._regime_intel[symbol]

            regime_ctx = self._regime_engine.get_regime_context(regime_intel)
            regime_recommended = getattr(regime_intel, "recommended_strategy", "") or None
            rp = getattr(regime_intel, "risk_profile", {}) or {}
            try:
                regime_lot_multiplier = float(rp.get("lot_multiplier", 1.0))
            except (TypeError, ValueError):
                regime_lot_multiplier = 1.0
            regime_param_overrides = normalize_strategy_params({
                "sl_atr": rp.get("sl_atr"), "tp_rr": rp.get("tp_rr"),
            })
            self._regime_intel[symbol] = regime_intel
        else:
            from app.brain.regime import classify_regime
            regime_ctx = classify_regime(primary_candles)

        regime_contexts[symbol] = regime_ctx
        regime = regime_ctx.regime

        # ─── 4.5 Pattern Detection (Multi-TF: Primary + H1) ───
        try:
            if is_new_candle or symbol not in self._pattern_signals:
                pattern_signals = self._pattern_detector.detect_latest(primary_candles, lookback=3)
                # Tag primary patterns
                for sig in pattern_signals:
                    sig.timeframe = primary_tf

                # H1 Pattern Detection — higher TF patterns carry more weight
                if primary_tf != "H1":
                    h1_candles = candles_by_tf.get("H1")
                    if h1_candles is not None and len(h1_candles) >= 30:
                        h1_patterns = self._pattern_detector.detect_latest(h1_candles, lookback=2)
                        for sig in h1_patterns:
                            sig.timeframe = "H1"
                            sig.strength = min(0.95, sig.strength * 1.5)  # H1 = 1.5x stronger
                            sig.name = f"h1_{sig.name}"  # prefix for clarity
                        pattern_signals.extend(h1_patterns)

                self._pattern_signals[symbol] = pattern_signals
            else:
                pattern_signals = self._pattern_signals[symbol]
        except Exception:
            pattern_signals = []
            self._pattern_signals[symbol] = []

        # ─── 4.6 Tick Volume ───
        tick_vol_signal = None
        try:
            if is_new_candle or symbol not in self._tick_volume_signals:
                tick_vol_signal = self._tick_volume_analyzer.analyze(primary_candles)
                self._tick_volume_signals[symbol] = tick_vol_signal
            else:
                tick_vol_signal = self._tick_volume_signals[symbol]
        except Exception:
            pass

        # ─── 4.7 Order Flow & Volume Profile ───
        vp_data = None
        try:
            if self._order_flow_analyzer:
                if is_new_candle or symbol not in self._vp_signals:
                    vp_data = self._order_flow_analyzer.calculate_volume_profile(primary_candles)
                    self._vp_signals[symbol] = vp_data
                else:
                    vp_data = self._vp_signals[symbol]
        except Exception:
            pass

        # ─── 5. Session ───
        if not session_value:
            session_value = get_current_session().value

        # ─── 6. Throttle Check ───
        skip_analysis = False
        throttle_reason = ""

        if self._trade_throttle["hourly_count"] >= 6:
            skip_analysis = True
            throttle_reason = "hourly_limit_reached"

        last_trade_time = self._trade_throttle["symbol_last_trade"].get(symbol, 0)
        if time.monotonic() - last_trade_time < 1800:
            skip_analysis = True
            throttle_reason = "symbol_cooldown"

        # ─── 7. Strategy Selection & Analysis ───
        best_decision = None
        candidates = []

        # Shadow-only symbols skip live analysis and pipeline
        if is_shadow_only:
             skip_analysis = True
             throttle_reason = "shadow_only_mode"

        # CPU Turbocharger: Only run heavy analysis on new candles
        if not skip_analysis and is_new_candle:
            personality = self.personality_engine.analyze_symbol(symbol, primary_candles)

            # Brain + Router recommendations
            brain_rec = None
            evolved_params_cache: dict = {}
            if self.brain_memory:
                brain_rec = await asyncio.to_thread(
                    self.brain_memory.get_best_strategy,
                    symbol=symbol, regime=regime.value, session=session_value,
                )

            router_pick = None
            if self.backtest_router:
                router_pick = self.backtest_router.get_best_strategy(symbol, regime.value)

            # Build candidate list
            added = set()
            gated_out = []

            async def _add_candidate(name: str):
                if not name or name in added:
                    return
                strategy_obj = self.factory._strategies.get(name)
                if strategy_obj is None:
                    return
                # ── Performance Gate ──
                if self.brain_memory:
                    perf = await asyncio.to_thread(
                        self.brain_memory.get_strategy_performance,
                        strategy_name=name, symbol=symbol, regime=regime.value,
                    )
                    if perf and perf.get("total_trades", 0) >= 10:
                        wr = perf.get("win_rate", 0)
                        pf = perf.get("profit_factor", 0)
                        if wr < 0.50 or pf < 1.3:
                            gated_out.append(f"{name}(WR={wr:.2f},PF={pf:.1f})")
                            return
                candidates.append((name, strategy_obj))
                added.add(name)

            selected = self.factory.select_strategy(
                symbol=symbol, regime=regime, session=session_value,
                brain_recommendation=brain_rec,
            )

            if regime_recommended and regime_recommended in self.factory._strategies:
                await _add_candidate(regime_recommended)
            if router_pick and router_pick in self.factory._strategies:
                await _add_candidate(router_pick)
            if selected:
                await _add_candidate(selected.name)

            for strat_name, strategy in self.factory._strategies.items():
                if strat_name in added: continue
                suitable = getattr(strategy, 'suitable_regimes', None) or getattr(strategy, 'regimes', None)
                if suitable and regime in suitable:
                    await _add_candidate(strat_name)

            if not candidates:
                for name in list(self.factory._strategies.keys())[:3]:
                    await _add_candidate(name)

            if len(candidates) > 10:
                candidates = candidates[:10]

            # ─── PARALLEL: Pre-fetch evolved params for all candidates ───
            async def _fetch_ep(s_name):
                if not self.brain_memory: return (s_name, {})
                for r in [regime.value, "ALL"]:
                    ep = await asyncio.to_thread(self.brain_memory.get_evolved_params, strategy_name=s_name, symbol=symbol, regime=r)
                    if ep: return (s_name, ep)
                return (s_name, {})

            ep_results = await asyncio.gather(*[_fetch_ep(n) for n, _ in candidates])
            evolved_params_cache = dict(ep_results)

            # ML & Sentiment
            ml_win_prob = 0.5
            dl_win_prob = 0.5
            sentiment_score = 0.0

            if getattr(self, 'deep_learner', None):
                try: dl_win_prob = self.deep_learner.predict_from_candles(primary_candles)
                except Exception: pass

            if self.ml_pattern_learner:
                try: ml_win_prob = self.ml_pattern_learner.predict(primary_candles, regime=regime.value, session=session_value)
                except Exception: pass

            if self.sentiment_aggregator:
                try: sentiment_score = self.sentiment_aggregator.get_sentiment(symbol).composite_score
                except Exception: pass

            confidence_boost = 0.0
            if self.recommender:
                try:
                    intel = await asyncio.to_thread(self.recommender.recommend_with_intelligence, symbol=symbol, regime=regime.value, session=session_value, ml_win_prob=ml_win_prob, sentiment_score=sentiment_score)
                    confidence_boost = intel.get("confidence_boost", 0.0)
                    pers_rec = await asyncio.to_thread(self.recommender.recommend_with_personality, symbol=symbol, regime=regime.value, session=session_value, personality=personality)
                    confidence_boost += pers_rec.get("personality_boost", 0.0)
                except Exception: pass

            # ─── PARALLEL ANALYSIS ───
            h1_candles = candles_by_tf.get("H1")
            
            def _analyze_strategy_sync(strat_name, strategy, evolved_params):
                """Synchronous logic for 1 strategy to be offloaded to a thread."""
                tf = getattr(strategy, 'timeframe', primary_tf)
                candles = candles_by_tf.get(tf, primary_candles)
                
                try:
                    sig = inspect.signature(strategy.analyze)
                    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                except Exception: accepts_kwargs = False
                
                strategy_kwargs = dict(regime_param_overrides) if accepts_kwargs else {}
                if evolved_params and accepts_kwargs:
                    strategy_kwargs.update(normalize_strategy_params(evolved_params))
                
                analysis_kwargs = {
                    "session": session_value, "h1_candles": h1_candles,
                    "h4_candles": candles_by_tf.get("H4"), "m15_candles": candles_by_tf.get("M15"),
                    "regime_context": regime_ctx,
                }
                if tick_vol_signal and tick_vol_signal.is_valid:
                    analysis_kwargs["pressure"] = {
                        "buying_pressure": tick_vol_signal.buying_pressure, "selling_pressure": tick_vol_signal.selling_pressure,
                        "score": tick_vol_signal.score, "is_climax": tick_vol_signal.is_climax, "is_dryup": tick_vol_signal.is_dryup,
                        "ad_line_trend": tick_vol_signal.ad_line_trend, "volume_trend": tick_vol_signal.volume_trend,
                        "body_conviction": tick_vol_signal.body_conviction, "has_divergence": tick_vol_signal.has_divergence,
                        "bull_power": tick_vol_signal.bull_power, "bear_power": tick_vol_signal.bear_power,
                        "power_score": tick_vol_signal.power_score, "power_verdict": tick_vol_signal.power_verdict,
                    }
                if strategy_kwargs: analysis_kwargs.update(strategy_kwargs)
                
                decision = analyze_with_fallback(strategy=strategy, candles=candles, profile=profile, regime=regime, extra_kwargs=analysis_kwargs)
                
                # Boosts and Guards
                if decision.action == Action.HOLD: return decision
                
                # Trade Coach
                if self.trade_coach:
                    should_trade, coach_adj, _ = self.trade_coach.get_advice(symbol=symbol, action=decision.action.value, session=session_value, regime=regime.value)
                    if not should_trade: 
                        decision.action = Action.HOLD
                        return decision
                else: coach_adj = 0.0

                # MTF Gate
                mtf_gate_adj = 0.0
                if self.mtf_gate and h1_candles is not None:
                    try:
                        mtf_res = self.mtf_gate.check(decision.action, h1_candles)
                        if not mtf_res.aligned:
                            decision.action = Action.HOLD
                            return decision
                        mtf_gate_adj = mtf_res.confidence_adj
                    except Exception: pass

                # Pattern Scorers
                p_score = self._pattern_scorer.score(signals=pattern_signals, strategy_direction=decision.action.value, symbol=symbol, regime=regime.value)
                if p_score.should_skip:
                    decision.action = Action.HOLD
                    return decision
                
                # Tick Volume
                tv_b = 0.0
                if tick_vol_signal and tick_vol_signal.is_valid:
                    if tick_vol_signal.is_climax or tick_vol_signal.is_dryup:
                        decision.action = Action.HOLD
                        return decision
                    pv = tick_vol_signal.power_verdict
                    if decision.action == Action.BUY:
                        if pv == "STRONG_BEAR": decision.action = Action.HOLD; return decision
                        elif pv in ["BULL", "STRONG_BULL"]: tv_b += 0.05
                        elif pv == "BEAR": tv_b -= 0.03
                    elif decision.action == Action.SELL:
                        if pv == "STRONG_BULL": decision.action = Action.HOLD; return decision
                        elif pv in ["BEAR", "STRONG_BEAR"]: tv_b += 0.05
                        elif pv == "BULL": tv_b -= 0.03
                
                # Order Flow
                of_b = 0.0
                if vp_data and self._order_flow_analyzer:
                    of_eval = self._order_flow_analyzer.analyze_order_flow(symbol, primary_candles, decision.action.value)
                    if not of_eval.get("is_favorable", True): decision.action = Action.HOLD; return decision
                    if "breakout" in of_eval.get("reason", ""): of_b = 0.05

                # MTF Engine
                mtf_e_b = 0.0
                mtf_e_score = 0.0
                if self._mtf_engine:
                    try:
                        is_rev = any("reversal" in str(t).lower() for t in (decision.tags or {}))
                        mtf_e_res = self._mtf_engine.score(candles_by_tf=candles_by_tf, direction=decision.action.value, is_reversal=is_rev)
                        if mtf_e_res.should_block: decision.action = Action.HOLD; return decision
                        mtf_e_b = mtf_e_res.confidence_boost
                        mtf_e_score = mtf_e_res.total
                    except Exception: pass

                # Entry Optimizer
                entry_b = 0.0
                e_grade = "B"
                if self._entry_optimizer:
                    try:
                        eq = self._entry_optimizer.evaluate(candles=candles, direction=decision.action.value)
                        entry_b, e_grade = eq.confidence_boost, eq.grade
                    except Exception: pass

                # Final calculation
                total_b = confidence_boost + p_score.confidence_boost + tv_b + of_b + mtf_gate_adj + mtf_e_b + entry_b + coach_adj
                decision.confidence = max(0.0, min(1.0, decision.confidence + total_b))
                
                # Attach tags
                if not hasattr(decision, 'tags') or decision.tags is None: decision.tags = {}
                decision.tags.update({
                    "pattern_boost": p_score.confidence_boost, "mtf_score": mtf_e_score,
                    "entry_grade": e_grade, "entry_boost": entry_b, "coach_adj": coach_adj,
                })
                return decision

            # Run all analyses in parallel threads
            analysis_tasks = [
                asyncio.to_thread(_analyze_strategy_sync, n, s, evolved_params_cache.get(n, {}))
                for n, s in candidates
            ]
            analysis_results = await asyncio.gather(*analysis_tasks)
            
            best_confidence = -1.0
            best_decision = None
            best_hold_decision = None
            
            for decision in analysis_results:
                if decision.action == Action.HOLD:
                    if best_hold_decision is None: best_hold_decision = decision
                    continue
                
                if decision.confidence > best_confidence:
                    best_confidence = decision.confidence
                    best_decision = decision

        # Fallback decision
        if best_decision is None:
            if skip_analysis or not is_new_candle:
                delay_reason = throttle_reason if skip_analysis else "waiting_for_candle_close"
                best_decision = Decision(
                    action=Action.HOLD, symbol=symbol,
                    strategy_name="CPU_Saver", reason=delay_reason,
                    confidence=0.0,
                )
            elif best_hold_decision is not None:
                best_decision = best_hold_decision
            else:
                best_decision = self.factory.get_decision(
                    candles=candles_by_tf.get("M5", primary_candles), profile=profile, regime=regime, session=session_value,
                    brain_recommendation=brain_rec if 'brain_rec' in locals() else None,
                    personality=personality if 'personality' in locals() else None,
                )

        # ─── 8. Pipeline Execution ───
        if account is None:
            from app.domain.models import AccountState
            account = AccountState(balance=100.0, equity=100.0)

        performance_metrics = {}
        if latest_coach_report:
            performance_metrics = dict(latest_coach_report.get("performance_summary") or {})
        performance_metrics["regime_lot_multiplier"] = regime_lot_multiplier
        if best_decision and hasattr(best_decision, 'tags') and isinstance(best_decision.tags, dict):
            performance_metrics["mtf_score"] = best_decision.tags.get("mtf_score", 50)
            performance_metrics["strategy_edge"] = best_decision.tags.get("edge_boost", 0)

        pipeline_result = await self.pipeline.execute(
            decision=best_decision, profile=profile, account=account,
            regime=regime.value, session=session_value, mt5_state=mt5_state,
            candles=candles_by_tf.get("M5", primary_candles),
            personality=personality if 'personality' in locals() else None,
            performance_metrics=performance_metrics, regime_context=regime_ctx,
            macro_bias_direction=macro_bias_direction,
        )

        # ─── 9. Throttle & Track ───
        if pipeline_result["result"] == "ok" and pipeline_result.get("ticket"):
            ticket = pipeline_result["ticket"]
            entry_price = pipeline_result.get("entry_price", 0.0)

            self._trade_throttle["hourly_count"] += 1
            self._trade_throttle["symbol_last_trade"][symbol] = time.monotonic()

            tracked_positions[ticket] = {
                "symbol": symbol, "type": best_decision.action.value,
                "volume": best_decision.lot_size if hasattr(best_decision, 'lot_size') else 0.01,
                "price_open": entry_price, "profit": 0.0,
                "sl": best_decision.stop_loss,
                "strategy": best_decision.strategy_name,
                "regime": regime.value, "session": session_value,
                "patterns": [s.name for s in pattern_signals] if pattern_signals else [],
            }

            # Outcome recording
            try:
                from app.brain.outcome_analyzer import TradeContext
                tags = best_decision.tags or {}
                self._outcome_analyzer.record_trade_open(TradeContext(
                    ticket=ticket, symbol=symbol,
                    strategy_name=best_decision.strategy_name,
                    regime=regime.value, session=session_value,
                    confidence=best_decision.confidence,
                    mtf_score=tags.get("mtf_score", 0),
                    entry_quality_grade=tags.get("entry_grade", ""),
                    entry_quality_score=tags.get("entry_boost", 0),
                    ml_win_prob=ml_win_prob if 'ml_win_prob' in locals() else 0.5,
                    sentiment_score=sentiment_score if 'sentiment_score' in locals() else 0.0,
                    patterns_aligned=tags.get("patterns_aligned", []),
                    patterns_conflicting=tags.get("patterns_conflicting", []),
                    direction=best_decision.action.value,
                    entry_price=entry_price,
                    stop_loss=best_decision.stop_loss or 0,
                    take_profit=best_decision.take_profit or 0,
                ))
            except Exception:
                pass

        # ─── 10. Brain Learning ───
        if self.trainer:
            try:
                m5_c = candles_by_tf.get("M5", primary_candles)
                await self.trainer.update_regime_stats(
                    symbol=symbol, regime=regime.value,
                    session=session_value,
                    atr=float(m5_c['close'].pct_change().std() * 100) if len(m5_c) > 20 else 0,
                )
            except Exception:
                pass

        last_decisions[symbol] = {
            "stage": pipeline_result["stage"],
            "result": pipeline_result["result"],
            "reason": pipeline_result["reason"],
            "action": best_decision.action.value,
            "confidence": best_decision.confidence,
            "strategy": best_decision.strategy_name,
            "regime": regime.value, "session": session_value,
            "cycle": cycle_count,
            "sl": best_decision.stop_loss, "tp": best_decision.take_profit,
            "strategies_tried": [c[0] for c in candidates],
            "candidates": len(candidates),
        }

        # ─── Shadow Runner ───
        if self.shadow_runner:
            try:
                equity = account.equity if account else 10000.0
                self.shadow_runner.run_shadow(
                    symbol=symbol, candles_by_tf=candles_by_tf, profile=profile,
                    regime=regime, session=session_value,
                    live_strategy=best_decision.strategy_name,
                    cycle=cycle_count, account_equity=equity,
                )
                m5 = candles_by_tf.get("M5")
                if m5 is not None and len(m5) > 0:
                    self._shadow_candle_cache[symbol] = m5
            except Exception:
                pass

    # ────────────────────────────────────────────────────────────────
    # _ingest_market_data
    # ────────────────────────────────────────────────────────────────

    _last_tick_time: dict[str, int] = {}

    async def _ingest_market_data(self, symbol: str, cycle_count: int) -> None:
        """Fetch and ingest new ticks to SQLite."""
        try:
            last_time = self._last_tick_time.get(symbol)
            if last_time is None:
                self._last_tick_time[symbol] = int(time.time() * 1000) - 10000
                last_time = self._last_tick_time[symbol]

            from app.mt5.market_data import fetch_ticks_since

            broker_symbol = symbol
            if self.mt5 and hasattr(self.mt5, 'adapter'):
                broker_symbol = self.mt5.adapter.map_symbol(symbol)

            ticks = await asyncio.to_thread(fetch_ticks_since, broker_symbol, last_time)
            if not ticks:
                return

            if self.db:
                await asyncio.to_thread(self.db.ingest_ticks, symbol, ticks)

            max_ts = max(t['time'] for t in ticks)
            max_ts_ms = int(max_ts * 1000)
            if max_ts_ms > last_time:
                self._last_tick_time[symbol] = max_ts_ms

        except Exception as e:
            logger.debug("tick_ingest_error", extra={"symbol": symbol, "error": str(e)})
