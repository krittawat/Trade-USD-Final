# -*- coding: utf-8 -*-
"""
Strategy Selector V4 — Best Signal Wins (All Strategies Compete)
Changes from V3:
- ALL enabled strategies run on EVERY cycle (no regime-exclusive routing)
- AntiChop filter still blocks in chop
- Best confidence wins regardless of model
- Compression/extreme expansion still blocked
"""
import json
import logging
import pandas as pd
from datetime import datetime, timezone
from .trend_killer import signal_trend_killer
from .liquidity_hunter import signal_liquidity_hunter
from .sniper_pro import signal_sniper_pro
from .predicta_v4 import signal_predicta_v4
from .counter_trend import signal_counter_trend
from .easy_trend import signal_easy_trend
from .antichop_filter import is_market_choppy
from .smc_metals import signal_smc_metals
from .btc_whale import signal_btc_whale
from .fvg_logic import signal_fvg_logic
from .momentum_rider import signal_momentum_rider
from .antigravity_alpha import signal_antigravity_alpha
from .antigravity_alpha_v6 import signal_antigravity_alpha_v6
from .alpha_v6_smc_live import signal_alpha_v6_smc
from .micro_scalper import signal_micro_scalper
from .gold_elite import signal_gold_elite
from .usoil_elite import signal_usoil_elite
from .usoil_momentum import signal_usoil_momentum
from .ai_brain_strategy import signal_ai_brain
from .momentum_scalper_v2 import signal_momentum_scalper_v2
from .btc_mean_rev import signal_btc_mean_rev
from .btc_stop_hunt_v2 import signal_btc_stop_hunt_v2
from .btc_elite_v2 import signal_btc_elite_v2
from .correlation_sniper import signal_correlation_sniper
from .btc_oracle import signal_btc_oracle
from .aether_flow_live import signal_aether_flow
from .indices_ultimate import signal_indices_ultimate


from backend.trader.features.pattern_recognition import analyze_patterns
from backend.trader.brain.ml_scoring import calculate_trade_probability
from backend.trader.brain.feedback_loop import feedback_loop
from backend.trader.brain.brain_bridge import brain_bridge


logger = logging.getLogger("opus_logger")

with open("d:/VibeCode/Trade/backend/trader/config/settings.json") as _f:
    _STRATEGY_CFG = json.load(_f).get("strategy", {})

COOLDOWN_BARS = _STRATEGY_CFG.get("trade_cooldown_bars", 10)
MIN_CONFIDENCE = _STRATEGY_CFG.get("min_confidence_trade", 0.6)

ENABLE_SNIPER = _STRATEGY_CFG.get("enable_sniper_pro", True)
ENABLE_PREDICTA = _STRATEGY_CFG.get("enable_predicta_v4", True)
ENABLE_COUNTER = _STRATEGY_CFG.get("enable_counter_trend", True)
ENABLE_EASY_TREND = _STRATEGY_CFG.get("enable_easy_trend", True)
ENABLE_ANTICHOP = _STRATEGY_CFG.get("enable_antichop_filter", True)
ENABLE_SMC_METALS = _STRATEGY_CFG.get("enable_smc_metals", True)
ENABLE_INDICES_ULTIMATE = _STRATEGY_CFG.get("enable_indices_ultimate", True)
ENABLE_BTC_WHALE = _STRATEGY_CFG.get("enable_btc_whale", True)
ENABLE_FVG_LOGIC = _STRATEGY_CFG.get("enable_fvg_logic", True)
ENABLE_MOMENTUM_RIDER = _STRATEGY_CFG.get("enable_momentum_rider", True)
ENABLE_ANTIGRAVITY_ALPHA = _STRATEGY_CFG.get("enable_antigravity_alpha", True)
ENABLE_ALPHA_V6 = _STRATEGY_CFG.get("enable_alpha_v6", True)
ENABLE_MICRO_SCALPER = _STRATEGY_CFG.get("enable_micro_scalper", True)
ENABLE_GOLD_ELITE = _STRATEGY_CFG.get("enable_gold_elite", True)
ENABLE_USOIL_ELITE = _STRATEGY_CFG.get("enable_usoil_elite", True)
ENABLE_USOIL_MOMENTUM = _STRATEGY_CFG.get("enable_usoil_momentum", True)
ENABLE_AI_BRAIN = _STRATEGY_CFG.get("enable_ai_brain", True)
ENABLE_MOMENTUM_SCALPER = _STRATEGY_CFG.get("enable_momentum_scalper", True)
ENABLE_BTC_MEAN_REV = _STRATEGY_CFG.get("enable_btc_mean_rev", False)
ENABLE_BTC_STOP_HUNT = _STRATEGY_CFG.get("enable_btc_stop_hunt", False)
ENABLE_BTC_ELITE = _STRATEGY_CFG.get("enable_btc_elite", False)
ENABLE_BTC_ORACLE = _STRATEGY_CFG.get("enable_btc_oracle", True)
ENABLE_CORRELATION_SNIPER = _STRATEGY_CFG.get("enable_correlation_sniper", True)
ENABLE_AETHER_FLOW = _STRATEGY_CFG.get("enable_aether_flow", True)


_last_trade_bar = {}


def reset_cooldown():
    _last_trade_bar.clear()


def select_and_generate_signal(df: pd.DataFrame, context: dict, events: list,
                                current_bar: int = -1) -> dict:
    """
    V4: ALL strategies compete. Best confidence wins.
    """
    _sym_debug = context.get('symbol', 'UNKNOWN')
    symbol = _sym_debug # Definitive fix for potential scope issues
    if "BTC" in _sym_debug:
        logger.info(f"🔍 [DEBUG] BTC Selector start: Bar={current_bar}, Context={context}")
        
    regime = context.get('regime_result', {})
    regime_name = regime.get('regime', 'UNKNOWN')
    cooldown_key = f"{context.get('symbol', 'UNKNOWN')}:{context.get('timeframe', 'NA')}"
    last_trade_bar = _last_trade_bar.get(cooldown_key, -999)

    # Cooldown
    _is_btc = "BTC" in context.get('symbol', '')
    required_cooldown = 3 if _is_btc else COOLDOWN_BARS
    
    if current_bar >= 0 and (current_bar - last_trade_bar) < required_cooldown:
        if _is_btc:
            logger.info(f"⏳ [DEBUG] BTC COOLDOWN: bar={current_bar} last={last_trade_bar} wait={required_cooldown}")
        else:
            logger.debug(f"COOLDOWN: key={cooldown_key} bar={current_bar} last={last_trade_bar} need={required_cooldown}")
        return None

    # AntiChop pre-filter
    if ENABLE_ANTICHOP:
        is_choppy, chop_reason = is_market_choppy(df)
        if is_choppy:
            if "BTC" in _sym_debug:
                logger.info(f"🚫 [DEBUG] BTC CHOP BLOCKED: {chop_reason}")
            else:
                logger.debug(f"CHOP: {chop_reason}")
            return None

    # ─── PHASE 4: TIME-GATING (NY CLOSE SPREAD GUARD) ──────────
    # Exness spreads widen and liquidity drops during NY Close (Hour 20:00-21:00 Server Time)
    # Block new entries during this window to protect capital.
    server_hour = datetime.now(timezone.utc).hour + 3 # Approximation for MT5 Server Time (GMT+3)
    if 20 <= (server_hour % 24) <= 21:
        logger.info(f"🛡️ [NY-CLOSE] Time Block Active (Hour {server_hour % 24}). Blocking new entries for {_sym_debug}")
        return None

    # ─── PHASE 5: PARAMETER EVOLUTION & ASSET TUNING ──────────
    evolved_ctx = context.copy()
    symbol_upper = _sym_debug.upper()
    
    # Identify Asset Class
    is_metal = any(m in symbol_upper for m in ["XAU", "XAG", "GOLD", "SILVER"])
    is_crypto = "BTC" in symbol_upper
    is_index = any(i in symbol_upper for i in ["30", "TEC", "NAS", "500", "HK", "JP", "AUS"])
    is_oil = "OIL" in symbol_upper
    
    evolved_ctx['brain_params'] = {}
    
    # Pre-fetch for common strategies
    for strat in ['gold_elite', 'gold_scalp_pro', 'ranging_sniper', 'alpha_v6', 'btc_elite_v2', 'usoil_elite', 'indices_ultimate']:
        params = brain_bridge.get_evolved_params(symbol, regime_name, strat)
        if params:
            evolved_ctx['brain_params'][strat] = params

    # Apply Institutional Tuning overrides
    if 'alpha_v6' not in evolved_ctx['brain_params']:
        evolved_ctx['brain_params']['alpha_v6'] = {}
        
    if is_crypto:
        evolved_ctx['brain_params']['alpha_v6']['sl_mult'] = 2.5 # survive crypto volatility
        evolved_ctx['sl_atr_multiplier'] = 2.5 
    elif is_metal:
        evolved_ctx['min_confidence'] = 0.75 # avoid gold noise
        evolved_ctx['brain_params']['alpha_v6']['sl_mult'] = 1.0 # tighten gold
    elif is_index:
        evolved_ctx['vwap_gate'] = True # Force VWAP for Indices
        evolved_ctx['brain_params']['alpha_v6']['sl_mult'] = 1.8 # index buffer
    elif is_oil:
        evolved_ctx['min_confidence'] = 0.70 
        evolved_ctx['brain_params']['alpha_v6']['sl_mult'] = 1.8 # oil buffer
        evolved_ctx['sl_atr_multiplier'] = 1.8


    # ─── PHASE 6: SIGNAL GENERATION (STRATEGY MATRIX) ──────────
    candidates = []
    
    # 1. Trend & Global Core (Universal)
    candidates.append(signal_trend_killer(df, evolved_ctx))
    if ENABLE_ALPHA_V6:
        candidates.append(signal_alpha_v6_smc(df, evolved_ctx))
    
    # 2. Metals Focused
    if is_metal:
        if ENABLE_SMC_METALS: candidates.append(signal_smc_metals(df, evolved_ctx))
        if ENABLE_GOLD_ELITE: candidates.append(signal_gold_elite(df, evolved_ctx))
        from .correlation_sniper import signal_correlation_sniper
        candidates.append(signal_correlation_sniper(df, evolved_ctx))

    # 3. Crypto Focused
    if is_crypto:
        if ENABLE_BTC_WHALE: candidates.append(signal_btc_whale(df, evolved_ctx))
        # Add other BTC specific models here
    
    # 4. Indices Focused
    if is_index:
        from .indices_ultimate import signal_indices_ultimate
        candidates.append(signal_indices_ultimate(df, evolved_ctx))
        if ENABLE_MOMENTUM_RIDER: candidates.append(signal_momentum_rider(df, evolved_ctx))

    # 5. Energy Focused (USOIL)
    if is_oil:
        from .usoil_elite import signal_usoil_elite
        from .usoil_momentum import signal_usoil_momentum
        candidates.append(signal_usoil_elite(df, evolved_ctx))
        candidates.append(signal_usoil_momentum(df, evolved_ctx))

    # 6. Forex & Utility
    if not (is_metal or is_crypto or is_index or is_oil):
        if ENABLE_SNIPER: candidates.append(signal_sniper_pro(df, evolved_ctx))
        if ENABLE_PREDICTA: candidates.append(signal_predicta_v4(df, evolved_ctx))

    # 6. Global Overrides (Only if explicitly enabled)
    if ENABLE_COUNTER: candidates.append(signal_counter_trend(df, evolved_ctx, current_bar))
    if ENABLE_FVG_LOGIC: candidates.append(signal_fvg_logic(df, evolved_ctx))
    if ENABLE_AI_BRAIN: candidates.append(signal_ai_brain(df, evolved_ctx))

    if ENABLE_MOMENTUM_SCALPER:
        candidates.append(signal_momentum_scalper_v2(df, evolved_ctx))

    if ENABLE_BTC_MEAN_REV and "BTC" in _sym_debug:
        candidates.append(signal_btc_mean_rev(df, evolved_ctx))
        
    if ENABLE_BTC_STOP_HUNT and "BTC" in _sym_debug:
        candidates.append(signal_btc_stop_hunt_v2(df, evolved_ctx))
        
    if ENABLE_BTC_ELITE and "BTC" in _sym_debug:
        candidates.append(signal_btc_elite_v2(df, evolved_ctx))
        
    if ENABLE_BTC_ORACLE and "BTC" in _sym_debug:
        candidates.append(signal_btc_oracle(df, evolved_ctx))

    if ENABLE_AETHER_FLOW:
        candidates.append(signal_aether_flow(df, evolved_ctx))

    if ENABLE_INDICES_ULTIMATE and ("30" in _sym_debug or "TEC" in _sym_debug or "NAS" in _sym_debug):
        candidates.append(signal_indices_ultimate(df, evolved_ctx))

    if events:
        candidates.append(signal_liquidity_hunter(df, events, evolved_ctx))

    if ENABLE_CORRELATION_SNIPER and "XAU" in _sym_debug.upper():
        candidates.append(signal_correlation_sniper(df, evolved_ctx))



    from backend.trader.brain.quality_filter import quality_filter
    
    valid_candidates = [s for s in candidates if s is not None]
    
    # ─── 2. Filter by Historical Performance (Quality Guard) ─────
    # ใช้ข้อมูลจากการ Replay 180 วัน เพื่อกรองสัญญาณที่ไม่มีคุณภาพย้อนหลัง
    quality_candidates = []
    for sig in valid_candidates:
        if quality_filter.is_quality_signal(sig, evolved_ctx):
            quality_candidates.append(sig)
        else:
            logger.info(f"🚫 [SELECTOR] Quality check failed for {sig.get('model')} - Signal Dropped")
            
    valid_candidates = quality_candidates

    # ─── 2.5. Institutional Major Trend Gate (HTF EMA 200) ─────
    # [ANTIGRAVITY] Forbidden to trade against major trend (H1 EMA 200)
    # UNLESS allow_counter_trend_critical is enabled for XAU, XAG, USOIL, BTC
    allow_counter_critical = _STRATEGY_CFG.get("allow_counter_trend_critical", False)
    htf_align = context.get('htf_ema_align', 'UNCERTAIN')
    
    if htf_align in ['BULLISH', 'BEARISH']:
        trend_candidates = []
        for sig in valid_candidates:
            is_counter = (sig['side'] == 'BUY' and htf_align == 'BEARISH') or \
                         (sig['side'] == 'SELL' and htf_align == 'BULLISH')
            
            # Critical Assets requested by user: XAU, XAG, USOIL, BTC
            critical_assets = ["XAU", "GOLD", "XAG", "SILVER", "OIL", "BTC"]
            is_critical = any(ca in _sym_debug.upper() for ca in critical_assets)
            
            if is_counter:
                if is_critical and allow_counter_critical:
                    logger.info(f"⚠️ [SELECTOR] {sig.get('model')} ALLOWED: Counter-Trend on Critical Asset {_sym_debug} (HTF: {htf_align}) via Override")
                elif is_critical:
                    logger.info(f"🚫 [SELECTOR] {sig.get('model')} BLOCKED: Counter-Trend on Critical Asset {_sym_debug} (HTF: {htf_align})")
                    continue
                else:
                    # For other assets, we still allow but with heavy penalty or just block if user says "all"
                    # Based on "ห้าม สวนเทรดใหญ่" (Forbidden), let's block for all to be safe.
                    logger.info(f"🚫 [SELECTOR] {sig.get('model')} BLOCKED: Counter-Trend (HTF: {htf_align})")
                    continue
            trend_candidates.append(sig)
        valid_candidates = trend_candidates

    # ─── 3. Post-Process & Tag Signals ─────────────
    # Ensure every signal has a 'model' tag for tracking in brain.db
    for sig in valid_candidates:
        if isinstance(sig, dict) and (not sig.get('model') or sig.get('model') == 'unknown'):
             sig['model'] = sig.get('strategy', 'UNKNOWN')

    if not valid_candidates:
        return None

    # PHASE 3: ML PROBABILITY SCORING (The Antigravity Engine)
    # Re-evaluate the signals using AI pattern mappings and the ML weights
    pattern_state = analyze_patterns(df)
    
    scored_signals = []
    for sig in valid_candidates:
        ml_score = calculate_trade_probability(df, sig, pattern_state)
        raw_confidence = sig.get('confidence', 0.5)
        
        # Elite/Ultimate strategies get high raw confidence weight (90%)
        elite_models = (
            'ANTIGRAVITY_ALPHA', 
            'ALPHA_V6_INSTITUTIONAL', 
            'USOIL_ELITE', 
            'BTC_ELITE_V2', 
            'INDICES_ULTIMATE'
        )
        if sig.get('model') in elite_models:
            blended = 0.1 * (ml_score / 100.0) + 0.9 * raw_confidence
        else:
            blended = 0.4 * (ml_score / 100.0) + 0.6 * raw_confidence
            
        sig['confidence'] = blended
        sig['ml_score'] = ml_score
        sig['raw_confidence'] = raw_confidence
        
        # Add visual metadata to reasons if pattern identified
        if "BUY" in sig['side'] and pattern_state['bullish_qml']:
            sig['rationale'].append("AI: Bullish QML Detected")
        if "SELL" in sig['side'] and pattern_state['bearish_qml']:
            sig['rationale'].append("AI: Bearish QML Detected")
            
        scored_signals.append(sig)

    # ─── PHASE 3.5: VOLUME CONFLUENCE GATE ────────────────────
    # Penalize signals during volume dry-up (except counter-trend exhaustion plays)
    latest = df.iloc[-1]
    vol_dryup = bool(latest.get('vol_dryup', False))
    vol_ratio = float(latest.get('vol_ratio', 0.0))
    obv_bullish = bool(latest.get('obv_bullish', False))
    
    if vol_dryup:
        for sig in scored_signals:
            if sig.get('model') != 'COUNTER_TREND':
                sig['confidence'] *= 0.90  # 10% penalty for low volume
                sig['ml_score'] = sig['confidence'] * 100
                sig['rationale'].append("⚠️ Vol Dryup Penalty -10%")
                logger.debug(f"VOL_GATE: {sig.get('model')} penalized for dryup (vol_ratio={vol_ratio:.2f})")

    # ─── PHASE 3.6: BTC HIGH-SPREAD PENALTY ────────────────────
    # BTC spread 500-800 pts eats ~40% of tight setups → require stronger signal
    # Dynamic penalty: If ATR is high, spread impact is lower. If ATR is low, spread impact is higher.
    for sig in scored_signals:
        _sym = sig.get('symbol', '').upper()
        if 'BTC' in _sym:
            _atr = float(latest.get('atr', 1000))
            # If ATR > 4000 (high vol), penalty is 2%. If ATR < 1500 (low vol), penalty is 8%.
            if _atr > 4000:
                penalty_factor = 0.98
                reason = "📊 BTC High-Vol Spread Penalty -2%"
            elif _atr < 1500:
                penalty_factor = 0.92
                reason = "📊 BTC Low-Vol Spread Penalty -8%"
            else:
                penalty_factor = 0.95
                reason = "📊 BTC Standard Spread Penalty -5%"
                
            sig['confidence'] *= penalty_factor
            sig['ml_score'] = sig['confidence'] * 100
            sig['rationale'].append(reason)

    # ─── PHASE 4.1: SMC LIQUIDITY CONFLUENCE ────────────────────
    # High-Beta assets (BTC, XAU) REQUIRE a liquidity sweep for institutional entries.
    has_sweep = any(e['type'] == 'SWEEP' for e in (events or []))
    is_high_beta = any(hb in _sym_debug.upper() for hb in ["BTC", "XAU", "GOLD"])
    
    for sig in scored_signals:
        if is_high_beta and not has_sweep:
            # Penalize signals that don't have a recent sweep confluence
            sig['confidence'] *= 0.85 
            sig['rationale'].append("🚨 No Liquidity Sweep Confluence (-15%)")
            logger.debug(f"SMC_GATE: {sig.get('model')} penalized for no sweep ({_sym_debug})")
        elif has_sweep:
            # Boost if sweep aligns with direction
            sweep_dir = next((e['direction'] for e in events if e['type'] == 'SWEEP'), None)
            if (sweep_dir == "LONG_SIGNAL" and sig['side'] == "BUY") or \
               (sweep_dir == "SHORT_SIGNAL" and sig['side'] == "SELL"):
                sig['confidence'] *= 1.10
                sig['rationale'].append("🎯 Sweep Confirmation (+10%)")

    # Pick best signal by new ML confidence
    best_sig = max(scored_signals, key=lambda s: s.get('confidence', 0))

    if "BTC" in _sym_debug:
        logger.info(f"🧬 [DEBUG] BTC Scored Signals: {[{'m': s['model'], 'c': round(s['confidence'], 3), 'r': s['raw_confidence']} for s in scored_signals]}")

    # Phase 3 Hard Gate: Probability Scoring Filter (AI Feedback Loop Enhanced)
    # Dynamic confidence threshold per strategy from shadow trade performance
    strategy_name = best_sig.get('model', 'UNKNOWN')
    req_confidence = feedback_loop.get_adjusted_confidence(strategy_name)
    # Fallback to global MIN_CONFIDENCE if feedback loop has no data
    if req_confidence == 0:
        req_confidence = MIN_CONFIDENCE
    
    if best_sig.get('confidence', 0) < req_confidence:
        if "BTC" in _sym_debug:
            logger.info(f"🧠 [DEBUG] BTC REJECTED: {best_sig.get('model')} confidence {best_sig.get('confidence'):.3f} < {req_confidence:.3f} (AI Feedback)")
        return None

    # (Phase 4 Counter-Trend Adjustment removed - now handled by hard block in Phase 2.5)

    # ─── PHASE 4.5: SL FLOOR ENFORCEMENT (Anti Stop-Hunt) ────────
    # Prevents any strategy from producing SL too tight for the asset.
    # BTC needs wider SL due to high spread (500+ pts) and volatile noise.
    _SL_FLOOR_ATR = {"BTC": 2.0, "XAU": 1.0, "XAG": 1.0, "USOIL": 1.2}
    _sym_upper = best_sig.get('symbol', '').upper()
    _floor_key = next((k for k in _SL_FLOOR_ATR if k in _sym_upper), None)
    if _floor_key:
        _atr = float(latest.get('atr', 0))
        if _atr > 0:
            _min_sl_dist = _atr * _SL_FLOOR_ATR[_floor_key]
            _entry = best_sig['entry_price']
            _sl = best_sig['sl']
            _actual_sl_dist = abs(_entry - _sl)
            
            if _actual_sl_dist < _min_sl_dist:
                # Calculate original RR to preserve after widening
                _orig_tp1_dist = abs(best_sig.get('tp1', _entry) - _entry)
                _orig_rr = _orig_tp1_dist / _actual_sl_dist if _actual_sl_dist > 0 else 1.5
                _orig_rr = max(1.2, _orig_rr)  # At least 1.2 RR
                
                # Widen SL to floor
                if best_sig['side'] == 'BUY':
                    best_sig['sl'] = round(_entry - _min_sl_dist, 5)
                    best_sig['tp1'] = round(_entry + _min_sl_dist * _orig_rr, 5)
                    if 'tp2' in best_sig:
                        best_sig['tp2'] = round(_entry + _min_sl_dist * _orig_rr * 1.5, 5)
                    if 'tp3' in best_sig:
                        best_sig['tp3'] = round(_entry + _min_sl_dist * _orig_rr * 2.5, 5)
                else:
                    best_sig['sl'] = round(_entry + _min_sl_dist, 5)
                    best_sig['tp1'] = round(_entry - _min_sl_dist * _orig_rr, 5)
                    if 'tp2' in best_sig:
                        best_sig['tp2'] = round(_entry - _min_sl_dist * _orig_rr * 1.5, 5)
                    if 'tp3' in best_sig:
                        best_sig['tp3'] = round(_entry - _min_sl_dist * _orig_rr * 2.5, 5)
                
                best_sig['rationale'].append(
                    f"🛡️ SL Floor: {_actual_sl_dist:.2f} → {_min_sl_dist:.2f} ({_SL_FLOOR_ATR[_floor_key]}x ATR)"
                )
                logger.info(
                    f"  🛡️ SL FLOOR: {best_sig['symbol']} SL widened "
                    f"{_actual_sl_dist:.2f} → {_min_sl_dist:.2f} ({_floor_key} min {_SL_FLOOR_ATR[_floor_key]}x ATR)"
                )

    # Update cooldown
    if current_bar >= 0:
        _last_trade_bar[cooldown_key] = current_bar

    logger.info(f"🧠 AI ENGINE APPROVED: {best_sig.get('model', '?')} | ML Probability: {best_sig.get('ml_score', 0):.1f}% | VolR={vol_ratio:.1f} OBV={'▲' if obv_bullish else '▼'}")
    return best_sig
