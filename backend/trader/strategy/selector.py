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
from backend.trader.config.paths import SETTINGS_PATH
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
from .rapid_pullback import signal_rapid_pullback
from .btc_mean_rev import signal_btc_mean_rev
from .btc_stop_hunt_v2 import signal_btc_stop_hunt_v2
from .btc_elite_v2 import signal_btc_elite_v2
from .correlation_sniper import signal_correlation_sniper
from .btc_oracle import signal_btc_oracle
from .aether_flow_live import signal_aether_flow
from .indices_ultimate import signal_indices_ultimate
from .indicator_confluence import signal_indicator_confluence
from .tick_volume_gate import evaluate_tick_volume
from .alpha_v7_ict_live import signal_alpha_v7_ict


from backend.trader.features.pattern_recognition import analyze_patterns
from backend.trader.brain.ml_scoring import calculate_trade_probability
from backend.trader.brain.feedback_loop import feedback_loop
from backend.trader.brain.brain_bridge import brain_bridge
from backend.trader.brain.symbol_tuner import symbol_tuner
from backend.trader.brain.professional_guard import apply_professional_guard


logger = logging.getLogger("opus_logger")

with open(SETTINGS_PATH, encoding="utf-8") as _f:
    _STRATEGY_CFG = json.load(_f).get("strategy", {})

COOLDOWN_BARS = _STRATEGY_CFG.get("trade_cooldown_bars", 10)
MIN_CONFIDENCE = _STRATEGY_CFG.get("min_confidence_trade", 0.6)

ENABLE_TREND_KILLER = _STRATEGY_CFG.get("enable_trend_killer", True)
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
ENABLE_RAPID_PULLBACK = _STRATEGY_CFG.get("enable_rapid_pullback", False)
ENABLE_BTC_MEAN_REV = _STRATEGY_CFG.get("enable_btc_mean_rev", False)
ENABLE_BTC_STOP_HUNT = _STRATEGY_CFG.get("enable_btc_stop_hunt", False)
ENABLE_BTC_ELITE = _STRATEGY_CFG.get("enable_btc_elite", False)
ENABLE_BTC_ORACLE = _STRATEGY_CFG.get("enable_btc_oracle", True)
ENABLE_CORRELATION_SNIPER = _STRATEGY_CFG.get("enable_correlation_sniper", True)
ENABLE_AETHER_FLOW = _STRATEGY_CFG.get("enable_aether_flow", True)
ENABLE_ALPHA_V7 = _STRATEGY_CFG.get("enable_alpha_v7", True)
ENABLE_INDICATOR_CONFLUENCE = _STRATEGY_CFG.get("enable_indicator_confluence", True)
MOMENTUM_MODELS = {"MOMENTUM_RIDER", "MOMENTUM_SCALPER_V2", "USOIL_MOMENTUM"}


_last_trade_bar = {}


def reset_cooldown():
    _last_trade_bar.clear()


def _resolve_asset_profile(symbol: str) -> dict:
    return symbol_tuner.get_profile(symbol)


def _calc_rr(signal: dict) -> float:
    try:
        entry = float(signal.get("entry_price", 0))
        sl = float(signal.get("sl", 0))
        tp = float(signal.get("tp1", 0))
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk <= 0:
            return 0.0
        return reward / risk
    except Exception:
        return 0.0


def _filter_candidate_models(candidates: list, context: dict) -> list:
    whitelist = context.get("strategy_whitelist") or context.get("model_whitelist")
    if not whitelist:
        return candidates

    allowed = {str(name).upper() for name in whitelist}
    filtered = []
    for sig in candidates:
        if not isinstance(sig, dict):
            continue
        if str(sig.get("model", "")).upper() in allowed:
            filtered.append(sig)
    return filtered


def _resolve_forced_models(context: dict) -> set:
    forced = context.get("force_enabled_models") or context.get("force_models") or []
    if isinstance(forced, str):
        forced = [forced]
    return {str(name).strip().upper() for name in forced if str(name).strip()}


def _resolve_whitelisted_models(context: dict) -> set:
    whitelist = context.get("strategy_whitelist") or context.get("model_whitelist") or []
    if isinstance(whitelist, str):
        whitelist = [whitelist]
    return {str(name).strip().upper() for name in whitelist if str(name).strip()}


def _is_alpha_v7_focus_mode(context: dict) -> bool:
    profile = str(context.get("strategy_profile") or context.get("live_strategy_profile") or "").strip().lower()
    forced = _resolve_forced_models(context)
    whitelist = _resolve_whitelisted_models(context)
    return (
        profile == "alpha_v7_ict"
        or "ALPHA_V7_ICT" in forced
        or whitelist == {"ALPHA_V7_ICT"}
    )


def _is_model_enabled(context: dict, model_name: str, enabled_by_config: bool) -> bool:
    return bool(enabled_by_config) or (str(model_name).upper() in _resolve_forced_models(context))


def _resolve_reference_utc(context: dict, df: pd.DataFrame):
    ref_time = context.get("current_time")
    if ref_time is None and not df.empty:
        ref_time = df.iloc[-1].get("time")
    if ref_time is None:
        return None
    try:
        ts = pd.Timestamp(ref_time)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.to_pydatetime()
    except Exception:
        return None


def _apply_momentum_discipline(candidates: list, context: dict, df: pd.DataFrame, events: list) -> list:
    if not candidates or df is None or df.empty:
        return candidates

    latest = df.iloc[-1]
    symbol = str(context.get("symbol", "")).upper()
    session = str(context.get("session") or context.get("current_session") or "").upper()
    regime_name = str(context.get("regime_result", {}).get("regime", "")).upper()
    is_crypto = "BTC" in symbol
    adx = float(latest.get("adx", 0.0) or 0.0)
    body_ratio = float(latest.get("body_ratio", 0.0) or 0.0)
    vol_ratio = float(latest.get("vol_ratio", 0.0) or 0.0)
    plus_di = float(latest.get("plus_di", 0.0) or 0.0)
    minus_di = float(latest.get("minus_di", 0.0) or 0.0)
    has_sweep = any(e.get("type") == "SWEEP" for e in (events or []))
    is_high_beta = any(key in symbol for key in ["XAU", "BTC", "USOIL"])

    momentum_candidates = [s for s in candidates if str(s.get("model", "")).upper() in MOMENTUM_MODELS]
    if len(momentum_candidates) >= 2 and len({str(s.get("side", "")).upper() for s in momentum_candidates}) > 1:
        logger.info(f"🚫 [MOMENTUM] Conflicting momentum directions for {symbol} - dropping momentum entries")
        candidates = [s for s in candidates if str(s.get("model", "")).upper() not in MOMENTUM_MODELS]

    disciplined = []
    for sig in candidates:
        model = str(sig.get("model", "")).upper()
        if model not in MOMENTUM_MODELS:
            disciplined.append(sig)
            continue

        side = str(sig.get("side", "")).upper()
        di_gap = (plus_di - minus_di) if side == "BUY" else (minus_di - plus_di)
        min_adx = 22.0 if is_high_beta else 20.0
        min_di_gap = 3.0 if is_high_beta else 2.0
        min_body_ratio = 0.30 if is_high_beta else 0.25
        min_vol_ratio = 1.0 if not is_crypto else 0.9

        if "TREND" not in regime_name and "VOLATILITY" not in regime_name:
            logger.info(f"🚫 [MOMENTUM] {model} blocked on {symbol}: regime {regime_name or 'UNKNOWN'} not directional")
            continue
        if not is_crypto and session not in {"LONDON", "NY", "LONDON/NY"}:
            logger.info(f"🚫 [MOMENTUM] {model} blocked on {symbol}: session {session or 'UNKNOWN'} outside London/NY")
            continue
        if adx < min_adx:
            logger.info(f"🚫 [MOMENTUM] {model} blocked on {symbol}: ADX {adx:.1f} < {min_adx:.1f}")
            continue
        if di_gap < min_di_gap:
            logger.info(f"🚫 [MOMENTUM] {model} blocked on {symbol}: DI gap {di_gap:.1f} < {min_di_gap:.1f}")
            continue
        if body_ratio < min_body_ratio:
            logger.info(f"🚫 [MOMENTUM] {model} blocked on {symbol}: body_ratio {body_ratio:.2f} < {min_body_ratio:.2f}")
            continue
        if vol_ratio < min_vol_ratio:
            logger.info(f"🚫 [MOMENTUM] {model} blocked on {symbol}: vol_ratio {vol_ratio:.2f} < {min_vol_ratio:.2f}")
            continue
        if is_high_beta and not has_sweep and float(sig.get("confidence", 0.0) or 0.0) < 0.78:
            logger.info(f"🚫 [MOMENTUM] {model} blocked on {symbol}: high-beta setup lacks sweep/elite confidence")
            continue

        disciplined.append(sig)

    return disciplined


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
    asset_profile = _resolve_asset_profile(_sym_debug)

    # Cooldown
    _is_btc = "BTC" in context.get('symbol', '')
    required_cooldown = max(1, int(asset_profile.get("cooldown_bars", COOLDOWN_BARS)))
    
    if current_bar >= 0 and (current_bar - last_trade_bar) < required_cooldown:
        if _is_btc:
            logger.info(f"⏳ [DEBUG] BTC COOLDOWN: bar={current_bar} last={last_trade_bar} wait={required_cooldown}")
        else:
            logger.debug(f"COOLDOWN: key={cooldown_key} bar={current_bar} last={last_trade_bar} need={required_cooldown}")
        return None

    # AntiChop pre-filter
    alpha_v7_focus_mode = _is_alpha_v7_focus_mode(context)
    if ENABLE_ANTICHOP and not alpha_v7_focus_mode:
        is_choppy, chop_reason = is_market_choppy(df)
        if is_choppy:
            if "BTC" in _sym_debug:
                logger.info(f"🚫 [DEBUG] BTC CHOP BLOCKED: {chop_reason}")
            else:
                logger.debug(f"CHOP: {chop_reason}")
            return None
    elif ENABLE_ANTICHOP and alpha_v7_focus_mode:
        logger.debug(f"CHOP BYPASS: {_sym_debug} running ALPHA_V7_ICT focus mode")

    # ─── PHASE 4: TIME-GATING (NY CLOSE SPREAD GUARD) ──────────
    # Exness spreads widen and liquidity drops during NY Close (Hour 20:00-21:00 Server Time)
    # Block new entries during this window to protect capital.
    backtest_mode = bool(context.get("backtest_mode", False))
    if backtest_mode:
        ref_utc = _resolve_reference_utc(context, df)
        server_hour = ((ref_utc.hour + 3) % 24) if ref_utc is not None else None
    else:
        server_hour = (datetime.now(timezone.utc).hour + 3) % 24  # Approximation for MT5 Server Time (GMT+3)
    if server_hour is not None and 20 <= server_hour <= 21:
        logger.info(f"🛡️ [NY-CLOSE] Time Block Active (Hour {server_hour}). Blocking new entries for {_sym_debug}")
        return None

    # ─── PHASE 5: PARAMETER EVOLUTION & ASSET TUNING ──────────
    evolved_ctx = context.copy()
    strategy_eval_mode = bool(evolved_ctx.get("strategy_eval_mode", False))
    symbol_upper = _sym_debug.upper()
    
    # Identify Asset Class
    is_metal = any(m in symbol_upper for m in ["XAU", "XAG", "GOLD", "SILVER"])
    is_crypto = "BTC" in symbol_upper
    is_index = any(i in symbol_upper for i in ["30", "TEC", "NAS", "500", "HK", "JP", "AUS"])
    is_oil = "OIL" in symbol_upper
    
    incoming_brain_params = context.get("brain_params") if isinstance(context.get("brain_params"), dict) else {}
    evolved_ctx['brain_params'] = {
        str(name): dict(values)
        for name, values in incoming_brain_params.items()
        if isinstance(values, dict)
    }
    
    # Pre-fetch for common strategies
    for strat in ['gold_elite', 'gold_scalp_pro', 'ranging_sniper', 'alpha_v6', 'alpha_v7_ict', 'btc_elite_v2', 'usoil_elite', 'indices_ultimate', 'rapid_pullback']:
        params = brain_bridge.get_evolved_params(symbol, regime_name, strat)
        if params:
            slot = evolved_ctx['brain_params'].setdefault(strat, {})
            for key, value in params.items():
                slot.setdefault(key, value)

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
    if _is_model_enabled(evolved_ctx, "TREND", ENABLE_TREND_KILLER):
        candidates.append(signal_trend_killer(df, evolved_ctx))
    if _is_model_enabled(evolved_ctx, "ALPHA_V6_INSTITUTIONAL", ENABLE_ALPHA_V6):
        candidates.append(signal_alpha_v6_smc(df, evolved_ctx))
    if _is_model_enabled(evolved_ctx, "INDICATOR_CONFLUENCE", ENABLE_INDICATOR_CONFLUENCE):
        candidates.append(signal_indicator_confluence(df, evolved_ctx))
    if _is_model_enabled(evolved_ctx, "MOMENTUM_RIDER", ENABLE_MOMENTUM_RIDER):
        candidates.append(signal_momentum_rider(df, evolved_ctx))
    if _is_model_enabled(evolved_ctx, "MOMENTUM_SCALPER_V2", ENABLE_MOMENTUM_SCALPER):
        candidates.append(signal_momentum_scalper_v2(df, evolved_ctx))
    
    # 2. Metals Focused
    if is_metal:
        if _is_model_enabled(evolved_ctx, "SMC_METALS", ENABLE_SMC_METALS): candidates.append(signal_smc_metals(df, evolved_ctx))
        if _is_model_enabled(evolved_ctx, "GOLD_ELITE", ENABLE_GOLD_ELITE): candidates.append(signal_gold_elite(df, evolved_ctx))
        if _is_model_enabled(evolved_ctx, "CORRELATION_SNIPER", ENABLE_CORRELATION_SNIPER):
            from .correlation_sniper import signal_correlation_sniper
            candidates.append(signal_correlation_sniper(df, evolved_ctx))

    # 3. Crypto Focused
    if is_crypto:
        if _is_model_enabled(evolved_ctx, "BTC_WHALE", ENABLE_BTC_WHALE): candidates.append(signal_btc_whale(df, evolved_ctx))
        # Add other BTC specific models here
    
    # 4. Indices Focused
    if is_index:
        from .indices_ultimate import signal_indices_ultimate
        if _is_model_enabled(evolved_ctx, "INDICES_ULTIMATE", ENABLE_INDICES_ULTIMATE):
            candidates.append(signal_indices_ultimate(df, evolved_ctx))

    # 5. Energy Focused (USOIL)
    if is_oil:
        from .usoil_elite import signal_usoil_elite
        from .usoil_momentum import signal_usoil_momentum
        if _is_model_enabled(evolved_ctx, "USOIL_ELITE", ENABLE_USOIL_ELITE):
            candidates.append(signal_usoil_elite(df, evolved_ctx))
        if _is_model_enabled(evolved_ctx, "USOIL_MOMENTUM", ENABLE_USOIL_MOMENTUM):
            candidates.append(signal_usoil_momentum(df, evolved_ctx))

    # 6. Forex & Utility
    if not (is_metal or is_crypto or is_index or is_oil):
        if _is_model_enabled(evolved_ctx, "SNIPER_PRO", ENABLE_SNIPER): candidates.append(signal_sniper_pro(df, evolved_ctx))
        if _is_model_enabled(evolved_ctx, "PREDICTA_V4", ENABLE_PREDICTA): candidates.append(signal_predicta_v4(df, evolved_ctx))

    # 6. Global Overrides (Only if explicitly enabled)
    if _is_model_enabled(evolved_ctx, "COUNTER_TREND", ENABLE_COUNTER): candidates.append(signal_counter_trend(df, evolved_ctx, current_bar))
    if _is_model_enabled(evolved_ctx, "FVG_LOGIC", ENABLE_FVG_LOGIC): candidates.append(signal_fvg_logic(df, evolved_ctx))
    if _is_model_enabled(evolved_ctx, "AI_BRAIN", ENABLE_AI_BRAIN): candidates.append(signal_ai_brain(df, evolved_ctx))

    if _is_model_enabled(evolved_ctx, "RAPID_PULLBACK", ENABLE_RAPID_PULLBACK) or evolved_ctx.get("enable_rapid_pullback", False):
        candidates.append(signal_rapid_pullback(df, evolved_ctx))

    if _is_model_enabled(evolved_ctx, "BTC_MEAN_REV", ENABLE_BTC_MEAN_REV) and "BTC" in _sym_debug:
        candidates.append(signal_btc_mean_rev(df, evolved_ctx))
        
    if _is_model_enabled(evolved_ctx, "BTC_STOP_HUNT_V2", ENABLE_BTC_STOP_HUNT) and "BTC" in _sym_debug:
        candidates.append(signal_btc_stop_hunt_v2(df, evolved_ctx))
        
    if _is_model_enabled(evolved_ctx, "BTC_ELITE_V2", ENABLE_BTC_ELITE) and "BTC" in _sym_debug:
        candidates.append(signal_btc_elite_v2(df, evolved_ctx))
        
    if _is_model_enabled(evolved_ctx, "BTC_ORACLE", ENABLE_BTC_ORACLE) and "BTC" in _sym_debug:
        candidates.append(signal_btc_oracle(df, evolved_ctx))

    if _is_model_enabled(evolved_ctx, "AETHER_FLOW", ENABLE_AETHER_FLOW):
        candidates.append(signal_aether_flow(df, evolved_ctx))

    if _is_model_enabled(evolved_ctx, "INDICES_ULTIMATE", ENABLE_INDICES_ULTIMATE) and ("30" in _sym_debug or "TEC" in _sym_debug or "NAS" in _sym_debug):
        candidates.append(signal_indices_ultimate(df, evolved_ctx))

    if _is_model_enabled(evolved_ctx, "ALPHA_V7_ICT", ENABLE_ALPHA_V7):
        candidates.append(signal_alpha_v7_ict(df, evolved_ctx))

    if events:
        candidates.append(signal_liquidity_hunter(df, events, evolved_ctx))

    from backend.trader.brain.quality_filter import quality_filter
    
    candidates = _filter_candidate_models(candidates, evolved_ctx)
    valid_candidates = [s for s in candidates if s is not None]
    
    # ─── 2. Filter by Historical Performance (Quality Guard) ─────
    # ใช้ข้อมูลจากการ Replay 180 วัน เพื่อกรองสัญญาณที่ไม่มีคุณภาพย้อนหลัง
    quality_candidates = []
    for sig in valid_candidates:
        if strategy_eval_mode or quality_filter.is_quality_signal(sig, evolved_ctx):
            quality_candidates.append(sig)
        else:
            logger.info(f"🚫 [SELECTOR] Quality check failed for {sig.get('model')} - Signal Dropped")
            
    valid_candidates = quality_candidates
    valid_candidates = _apply_momentum_discipline(valid_candidates, evolved_ctx, df, events)

    # ─── 2.5. Institutional Major Trend Gate (HTF EMA 200) ─────
    # [ANTIGRAVITY] Forbidden to trade against major trend (H1 EMA 200)
    # UNLESS allow_counter_trend_critical is enabled for XAU, XAG, USOIL, BTC
    allow_counter_critical = _STRATEGY_CFG.get("allow_counter_trend_critical", False)
    htf_align = context.get('htf_ema_align', 'UNCERTAIN')
    latest_row = df.iloc[-1] if not df.empty else {}
    latest_structure = str(latest_row.get("structure", "NONE")).upper()
    disp_up = bool(latest_row.get("displacement_up", False))
    disp_down = bool(latest_row.get("displacement_down", False))
    sweep_dir = next((e.get("direction") for e in (events or []) if e.get("type") == "SWEEP"), None)

    def _allow_counter_by_fvg_structure(sig: dict) -> bool:
        side = str(sig.get("side", "")).upper()
        model = str(sig.get("model", "")).upper()
        reasons_txt = " ".join(str(r) for r in sig.get("rationale", [])).upper()
        has_fvg = ("FVG" in model) or ("FVG" in reasons_txt)
        if not has_fvg:
            return False

        if side == "BUY":
            structure_ok = latest_structure in {"HH", "HL"} or disp_up
            sweep_ok = (sweep_dir is None) or (sweep_dir == "LONG_SIGNAL")
            return structure_ok and sweep_ok
        if side == "SELL":
            structure_ok = latest_structure in {"LH", "LL"} or disp_down
            sweep_ok = (sweep_dir is None) or (sweep_dir == "SHORT_SIGNAL")
            return structure_ok and sweep_ok
        return False
    
    if htf_align in ['BULLISH', 'BEARISH']:
        trend_candidates = []
        for sig in valid_candidates:
            is_counter = (sig['side'] == 'BUY' and htf_align == 'BEARISH') or \
                         (sig['side'] == 'SELL' and htf_align == 'BULLISH')
            
            # Critical Assets requested by user: XAU, XAG, USOIL, BTC
            critical_assets = ["XAU", "GOLD", "XAG", "SILVER", "OIL", "BTC"]
            is_critical = any(ca in _sym_debug.upper() for ca in critical_assets)
            
            if is_counter:
                if _allow_counter_by_fvg_structure(sig):
                    logger.info(
                        f"⚖️ [SELECTOR] {sig.get('model')} ALLOWED: Counter-Trend via FVG+Structure "
                        f"({_sym_debug}, Struct={latest_structure}, Sweep={sweep_dir or 'NONE'}, HTF={htf_align})"
                    )
                elif is_critical and allow_counter_critical:
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
        elif strategy_eval_mode:
            blended = 0.2 * (ml_score / 100.0) + 0.8 * raw_confidence
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

    if not scored_signals:
        return None

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

    # ─── PHASE 3.55: TICK VOLUME ENTRY GATE ───────────────────
    # Only keep entries when tick volume is large enough and aligned with pressure.
    tv_filtered = []
    for sig in scored_signals:
        tv_gate = evaluate_tick_volume(df, sig, context=evolved_ctx)
        sig['tick_volume_gate'] = tv_gate.state
        sig['tick_volume_ratio'] = tv_gate.metrics.get('vol_ratio', vol_ratio)
        sig['tick_volume_side'] = tv_gate.metrics.get('directional_side', 'NEUTRAL')
        sig['tick_volume_boost'] = tv_gate.confidence_delta

        if not tv_gate.allowed:
            logger.info(
                f"🚫 [TICK VOL] {sig.get('model')} blocked on {_sym_debug}: "
                f"{tv_gate.reason}"
            )
            continue

        if tv_gate.confidence_delta:
            sig['confidence'] = float(min(0.98, max(0.0, sig['confidence'] + tv_gate.confidence_delta)))
            sig['ml_score'] = sig['confidence'] * 100
        if tv_gate.reason:
            sig['rationale'].append(tv_gate.reason)
        tv_filtered.append(sig)

    scored_signals = tv_filtered
    if not scored_signals:
        return None

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

    # ─── PHASE 4.2: ASSET PROFILE HARD GATES ──────────────────
    min_rr = float(asset_profile.get("min_rr", 1.10))
    rr_filtered = []
    for sig in scored_signals:
        rr = _calc_rr(sig)
        sig["rr"] = rr
        if rr < min_rr:
            logger.info(
                f"🚫 [SELECTOR] {sig.get('model')} RR {rr:.2f} < {min_rr:.2f} "
                f"for {_sym_debug} - dropped"
            )
            continue
        rr_filtered.append(sig)
    if not rr_filtered:
        return None

    rr_filtered = apply_professional_guard(rr_filtered, evolved_ctx)
    if not rr_filtered:
        return None

    # ─── PHASE 4.3: CONFLUENCE-AWARE COMPOSITE SCORING ────────
    buy_count = sum(1 for s in rr_filtered if s.get("side") == "BUY")
    sell_count = sum(1 for s in rr_filtered if s.get("side") == "SELL")
    htf_align = context.get('htf_ema_align', 'UNCERTAIN')
    sweep_dir = next((e.get('direction') for e in (events or []) if e.get('type') == 'SWEEP'), None)
    scored_rank = []

    for sig in rr_filtered:
        bonus = 0.0
        side = sig.get("side", "")
        rr = float(sig.get("rr", 0.0))
        confidence = float(sig.get("confidence", 0.0))

        side_support = buy_count if side == "BUY" else sell_count
        if side_support >= 2:
            bonus += 0.03
            sig['rationale'].append(f"🤝 Side consensus ({side_support} models)")

        if (htf_align == "BULLISH" and side == "BUY") or (htf_align == "BEARISH" and side == "SELL"):
            bonus += 0.02
            sig['rationale'].append("🧭 HTF trend aligned")

        if (obv_bullish and side == "BUY") or ((not obv_bullish) and side == "SELL"):
            bonus += 0.01
            sig['rationale'].append("📈 OBV aligned")

        if (sweep_dir == "LONG_SIGNAL" and side == "BUY") or (sweep_dir == "SHORT_SIGNAL" and side == "SELL"):
            bonus += 0.02
            sig['rationale'].append("🌊 Sweep aligned")

        rr_component = min(rr / (min_rr * 1.8), 1.0)
        composite = (confidence * 0.72) + (rr_component * 0.23) + (bonus * 0.05)
        sig["confluence_bonus"] = round(bonus, 4)
        sig["composite_score"] = composite
        scored_rank.append(sig)

    # Pick best signal by composite score (confidence + RR + confluence)
    best_sig = max(scored_rank, key=lambda s: s.get('composite_score', s.get('confidence', 0)))

    if "BTC" in _sym_debug:
        logger.info(
            f"🧬 [DEBUG] BTC Scored Signals: "
            f"{[{'m': s['model'], 'c': round(s['confidence'], 3), 'rr': round(s.get('rr', 0.0), 2), 'cmp': round(s.get('composite_score', 0.0), 3)} for s in scored_rank]}"
        )

    # Phase 3 Hard Gate: Probability Scoring Filter (AI Feedback Loop Enhanced)
    # Dynamic confidence threshold per strategy from shadow trade performance
    strategy_name = best_sig.get('model', 'UNKNOWN')
    req_confidence = feedback_loop.get_adjusted_confidence(strategy_name)
    # Fallback to global MIN_CONFIDENCE if feedback loop has no data
    if req_confidence == 0:
        req_confidence = MIN_CONFIDENCE
    req_confidence = max(req_confidence, float(asset_profile.get("min_confidence", MIN_CONFIDENCE)))
    if strategy_eval_mode:
        req_confidence = min(req_confidence, float(evolved_ctx.get("strategy_eval_min_confidence", 0.62)))
    
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

    logger.info(
        f"🧠 AI ENGINE APPROVED: {best_sig.get('model', '?')} | ML Probability: {best_sig.get('ml_score', 0):.1f}% "
        f"| RR={best_sig.get('rr', 0):.2f} | Score={best_sig.get('composite_score', best_sig.get('confidence', 0)):.3f} "
        f"| VolR={vol_ratio:.1f} OBV={'▲' if obv_bullish else '▼'}"
    )
    return best_sig
