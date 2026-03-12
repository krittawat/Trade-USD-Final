"""
Risk Sizing — คำนวณ lot size จากความเสี่ยง.

สูตร:
    risk_usd = equity × max_risk_pct / 100
    sl_distance = |entry_price - stop_loss|
    lot = risk_usd / (sl_distance × contract_size)
    lot = round_to_step(lot, volume_step)

กฎเหล็ก:
    - ความเสี่ยงจริงต้อง ≤ 2% ของ equity
    - Lot size ต้องอยู่ในช่วง volume_min — volume_max
    - ปัดเศษลง (floor) เสมอ — ไม่ปัดขึ้น
"""

from typing import Optional, TYPE_CHECKING, cast

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.enums import BlockReason
from app.domain.models import AccountState, Decision, OrderPlan, SymbolProfile
from app.mt5.broker_specs import round_lot, is_lot_valid
from app.core.currency_adapter import get_adapter
from app.services.session import get_current_session
from trader.config.paths import SETTINGS_PATH

if TYPE_CHECKING:
    from app.brain.personality import PersonalityProfile

logger = get_logger(__name__)

        # ═══════════════════════════════════════════════════════════════════════════════
# HARD SAFETY CONSTANTS — DO NOT CHANGE WITHOUT RISK REVIEW
# These are absolute circuit breakers that fallback if dynamic tiers fail.
# ═══════════════════════════════════════════════════════════════════════════════
HARD_MAX_RISK_PCT = 5.0       # Increased to 5% to allow for aggressive scaled profits
HARD_MAX_LOT_BROKER = {       # Absolute max broker lots per symbol (final safety net)
    "XAUUSD": 0.02,           # Gold: max 0.02 Lots
    "XAGUSD": 0.01,           # Silver: max 0.01 Lots
    "BTCUSD": 0.01,           # BTC: max 0.01 Lots
}
HARD_MAX_LOT_DEFAULT = 0.02    # Fallback for unlisted symbols

import json
try:
    with open(SETTINGS_PATH, "r", encoding="utf-8") as _f:
        _TIER_CFG = json.load(_f).get("tiered_scaling", {})
except Exception as e:
    logger.error(f"Failed to load tiered_scaling config: {e}")
    _TIER_CFG = {"enabled": False, "tiers": []}


def get_risk_parity_multiplier(current_atr: float, baseline_atr: float) -> float:
    """
    Risk Parity Sizing: คำนวณตัวคูณเพื่อลด Lot เมื่อตลาดผันผวนผิดปกติ (Volatility Spike)
    ถ้า Current ATR > Baseline ATR แสดงว่ากราฟสวิงแรง → ลด Size เพื่อคุมให้ความเสี่ยง (Risk Amount) เท่าเดิม

    Returns:
        float: ตัวคูณในช่วง [0.2, 1.0] (ลดเต็มที่ 80%, ไม่เคยเพิ่ม Size เกิน 1.0)
    """
    if current_atr <= 0 or baseline_atr <= 0:
        return 1.0
        
    multiplier = baseline_atr / current_atr
    
    # Clip limits: Never increase size > 1.0, max cut to 0.2 (80% reduction)
    return max(0.2, min(1.0, multiplier))

def get_dynamic_atr_multiplier(current_atr: float, baseline_atr: float, base_multiplier: float = 1.5) -> float:
    """
    Phase 3: Adaptive Learning for Dynamic SL Multipliers.
    Dynamic ATR Multiplier widens SL when market is highly volatile (current_atr > baseline_atr),
    and tightens SL when market is quiet. 
    
    Args:
        current_atr: Current ATR(14) reading
        baseline_atr: SMA of ATR(14) over recent history (e.g., 50 bars)
        base_multiplier: The nominal multiplier (e.g., 1.5 for basic, 2.0 for crypto)
    """
    if current_atr <= 0 or baseline_atr <= 0:
        return base_multiplier
        
    # Example: If current_atr is 50% larger than baseline, we get a ratio of 1.5
    volatility_ratio = current_atr / baseline_atr
    
    # We want to scale our base_multiplier proportionally but capped.
    # Widen SL slightly if highly volatile (up to 30% wider), tighten if dead (down to 20% tighter)
    adaptive_ratio = max(0.8, min(1.3, volatility_ratio))
    
    return float(round(base_multiplier * adaptive_ratio, 2))


def calculate_lot_size(
    decision: Decision,
    profile: SymbolProfile,
    account: AccountState,
    settings: Settings,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    personality: Optional["PersonalityProfile"] = None,
    performance_metrics: dict | None = None,
    current_atr: float | None = None,
    baseline_atr: float | None = None,
) -> OrderPlan | BlockReason:
    """
    คำนวณ lot size จากความเสี่ยงที่กำหนด.
    
    Args:
        decision: ผลลัพธ์จาก strategy
        profile: ข้อมูลสัญลักษณ์
        account: สถานะบัญชี
        settings: การตั้งค่าระบบ
        entry_price: ราคาเข้า (ถ้าไม่ระบุ จะใช้ราคาปัจจุบัน)
        stop_loss: ราคา SL (ถ้าไม่ระบุ จะใช้จาก decision.stop_loss)
        personality: นิสัยตลาด (Optional) — ปรับความเสี่ยงตาม Volatility/Fakeout
        performance_metrics: สถิติการเทรดปัจจุบัน (Optional)
        current_atr: ATR ปัจจุบัน (สำหรับ Risk Parity)
        baseline_atr: ATR เฉลี่ยย้อนหลัง (สำหรับ Risk Parity)
    
    Returns:
        OrderPlan: ถ้าคำนวณสำเร็จ
        BlockReason: ถ้าไม่สามารถคำนวณ lot ที่ valid ได้
    """
    # --- ใช้ SL จาก argument หรือ decision ---
    actual_sl = stop_loss if stop_loss is not None else decision.stop_loss

    # --- ตรวจสอบ SL ---
    if actual_sl is None or actual_sl <= 0:
        return BlockReason.NO_STOP_LOSS

    # --- 1. Base Risk ---
    # Use strategy's suggested risk if provided, otherwise use global max
    if getattr(decision, "risk_pct", 0) and decision.risk_pct > 0:
        base_risk_pct = min(decision.risk_pct, settings.max_risk_per_trade_pct)
    else:
        base_risk_pct = settings.max_risk_per_trade_pct
        
    # --- 1.1 Hard Risk Caps ---
    # ABSOLUTE circuit breaker — cannot be overridden by .env or strategy
    if base_risk_pct > HARD_MAX_RISK_PCT:
        logger.warning("risk_hard_cap_enforced", extra={
            "symbol": decision.symbol,
            "original_risk_pct": base_risk_pct,
            "capped_risk_pct": HARD_MAX_RISK_PCT
        })
        base_risk_pct = HARD_MAX_RISK_PCT
    
    # ─── 1.5 Asset Class Scaling (Sniper Mode Safety) ───
    is_gold = "XAU" in decision.symbol.upper() or "GOLD" in decision.symbol.upper()
    risk_reason = []

    # Check for Cent Account Mode
    is_cent_mode = settings.account_currency == "USC" or settings.lot_mode == "CENT"

    if is_gold:
        # Gold: Hard Cap at 1.0% only for Standard accounts
        # For Cent accounts (USC), we allow up to HARD_MAX_RISK_PCT (already capped above)
        if not is_cent_mode and base_risk_pct > 1.0:
            base_risk_pct = 1.0
            risk_reason.append("Gold Risk Cap (1.0%)")
    else:
        # Forex: Hard Cap at 2.0% for Sniper Mode (Standard)
        if not is_cent_mode and base_risk_pct > 2.0:
            base_risk_pct = 2.0
            risk_reason.append("FX Risk Cap (2.0%)")
            
    # ─── 1.8 Session-Specific Volatility Targeting (Phase 4) ───
    current_session = get_current_session().value
    # ASIA session normally sideways -> cut lot by 50%
    if current_session == "ASIA":
        base_risk_pct *= 0.5
        risk_reason.append("ASIA Session Filter (-50%)")
    
    # --- 2. Dynamic Risk Adjustment (Personality) ---
    risk_modifier = 1.0
    # NOTE: DO NOT reset risk_reason here — keep asset class cap reasons
    
    if personality:
        # 2.1 Volatility Control
        if personality.volatility_score > 80:
            risk_modifier *= 0.5  # ลดความเสี่ยง 50% ถ้าผันผวนจัด
            risk_reason.append("High Volatility")
        elif personality.volatility_score > 60:
            risk_modifier *= 0.8
            
        # 2.2 Fakeout Protection
        if personality.fakeout_probability > 0.6:
            risk_modifier *= 0.7  # ลด 30% ถ้าชอบสับขาหลอก
            risk_reason.append("High Fakeout Prob")
            
        # 2.3 Trend Persistence
        # ถ้า trend แข็งแกร่ง และ strategy เป็น trend follower -> ให้เต็ม max (ไม่ลด)
        if personality.trend_persistence > 0.65 and "trend" in decision.strategy_name.lower():
            risk_modifier = min(1.0, risk_modifier * 1.2) # คืนความเสี่ยงให้ (แต่ไม่เกิน 100% ของ base)

    # ─── 3. Dynamic Performance Scaling (Auto Coach) ───
    if performance_metrics:
        # 3.1 Drawdown Scaling (Survival Mode)
        current_dd = performance_metrics.get("max_drawdown_pct", 0.0)
        
        if current_dd > 10.0:
            risk_modifier *= 0.5  # Heavy cut
            risk_reason.append(f"Deep DD {current_dd:.1f}%")
        elif current_dd > 5.0:
            risk_modifier *= 0.75 # Moderate cut
            risk_reason.append(f"Mod DD {current_dd:.1f}%")

        # 3.2 Losing Streak Scaling (Granular Protection)
        loss_streak = performance_metrics.get("current_loss_streak", 0)
        if loss_streak >= 4:
            risk_modifier *= 0.25 # Cold Streak Protection
            risk_reason.append(f"Cold Streak {loss_streak}")
        elif loss_streak == 3:
            risk_modifier *= 0.5
            risk_reason.append(f"Loss Streak 3")
        elif loss_streak == 2:
            risk_modifier *= 0.8
            risk_reason.append(f"Loss Streak 2")
            
        # 3.3 Win Streak Scaling (Confidence Boost)
        win_streak = performance_metrics.get("current_win_streak", 0)
        if win_streak >= 3:
            # Boost risk slightly to capitalize on flow, capped at 1.25x (or 1.25% absolute risk)
            risk_modifier = min(1.25, risk_modifier * 1.1)
            risk_reason.append(f"Hot Streak {win_streak}")

        # 3.4 Martingale/Gambler Protection
        if performance_metrics.get("martingale_detected", False):
            risk_modifier *= 0.1
            risk_reason.append("Gambler Mode Detected")

    # ─── 4. Regime-Aware Risk Scaling (Intelligence Core) ───
    # ใช้ lot_multiplier จาก regime risk profile
    # ส่งผ่าน decision.tags.get("regime_lot_multiplier")
    regime_lot_mult = 1.0
    tags = getattr(decision, "tags", None)
    if isinstance(tags, dict):
        regime_lot_mult = tags.get("regime_lot_multiplier", 1.0)
    elif performance_metrics:
        regime_lot_mult = performance_metrics.get("regime_lot_multiplier", 1.0)

    try:
        regime_lot_mult = float(regime_lot_mult)
    except (TypeError, ValueError):
        regime_lot_mult = 1.0

    if regime_lot_mult <= 0:
        logger.info("regime_blocks_trade", extra={
            "symbol": decision.symbol,
            "regime_lot_mult": regime_lot_mult,
            "reason": "regime_lot_multiplier=0 → NO_TRADE",
        })
        return BlockReason.LOT_SIZE_INVALID

    if regime_lot_mult < 1.0:
        risk_modifier *= regime_lot_mult
        risk_reason.append(f"Regime Scale {regime_lot_mult:.1f}x")

    # ─── 4.5 MTF Confluence Scaling (Super-Human) ───
    if performance_metrics:
        mtf_score = performance_metrics.get("mtf_score", 50)
        try:
            mtf_score = float(mtf_score)
        except (TypeError, ValueError):
            mtf_score = 50
        if mtf_score > 75:
            risk_modifier *= 1.1  # High confluence → slightly more risk
            risk_reason.append(f"MTF High ({mtf_score:.0f})")
        elif mtf_score < 30:
            risk_modifier *= 0.5  # Low confluence → half risk
            risk_reason.append(f"MTF Low ({mtf_score:.0f})")

    # ─── 4.6 Edge-Weighted Sizing (Outcome Learning) ───
    if performance_metrics:
        edge_val = performance_metrics.get("strategy_edge", 0)
        try:
            edge_val = float(edge_val)
        except (TypeError, ValueError):
            edge_val = 0
        if edge_val > 0.05:
            risk_modifier *= 1.1  # Proven edge → slightly more risk
            risk_reason.append(f"Edge Boost ({edge_val:.2f})")
        elif edge_val < -0.05:
            risk_modifier *= 0.8  # Negative edge → reduce risk
            risk_reason.append(f"Edge Penalty ({edge_val:.2f})")

    # ─── 4.6.5 Fractional Kelly Criterion Sizing (Half-Kelly) ───
    if performance_metrics and getattr(settings, 'use_kelly_sizing', True):
        # Extract win probability (W) and average reward-to-risk ratio (R)
        win_rate = float(performance_metrics.get("win_rate", 0.0))
        avg_rr = float(performance_metrics.get("avg_rr", 0.0))
        
        # Only apply Kelly if there is sufficient historical data to form a non-zero W & R
        if win_rate > 0.0 and avg_rr > 0.0:
            # Kelly % = W - [(1 - W) / R]
            kelly_pct = win_rate - ((1.0 - win_rate) / avg_rr)
            
            if kelly_pct > 0:
                # Use Half-Kelly for stricter capital preservation
                half_kelly = kelly_pct / 2.0
                
                # Scale modifier based on expected Kelly risk relative to base risk %
                base_pct_decimal = max(base_risk_pct, 0.1) / 100.0
                kelly_multiplier = half_kelly / base_pct_decimal
                
                # Cap multiplier to prevent extreme explosive sizing [0.5x to 1.5x]
                kelly_multiplier = max(0.5, min(1.5, kelly_multiplier))
                risk_modifier *= kelly_multiplier
                risk_reason.append(f"Half-Kelly ({half_kelly*100:.1f}%)")
            else:
                # Negative Kelly indicates statistical disadvantage - slash risk severely
                risk_modifier *= 0.5
                risk_reason.append("Negative Kelly (Risk Cut)")

    # ─── 4.7 Risk Parity (Volatility-Targeting) ───
    if getattr(settings, 'risk_parity_enabled', True) and current_atr and baseline_atr:
        parity_multiplier = get_risk_parity_multiplier(current_atr, baseline_atr)
        if parity_multiplier < 1.0:
            risk_modifier *= parity_multiplier
            risk_reason.append(f"Risk Parity ({parity_multiplier:.2f}x)")

    # ─── 4.8 House Money / Portfolio Growth Sizing ───
    # คำนวณระยะห่างจาก Capital Floor (High Water Mark)
    if account.peak_equity > 0:
        base_balance = max(account.peak_equity, account.initial_balance)
        floor = base_balance * 0.90  # Default 90% floor
        distance_to_floor_pct = (account.equity - floor) / base_balance * 100
        
        # ถ้าร่มชูชีพเริ่มทำงาน (เข้าใกล้ Floor มากเกินไป) -> ลด Lot ทันที
        if distance_to_floor_pct < 2.0:
            risk_modifier *= 0.5
            risk_reason.append(f"Floor Shield (Dist {distance_to_floor_pct:.1f}%)")
        elif distance_to_floor_pct < 5.0:
            risk_modifier *= 0.75
            risk_reason.append(f"Floor Warning (Dist {distance_to_floor_pct:.1f}%)")
        
        # ถ้ามีกำไรของวันนี้ (House Money) -> ปั้นพอร์ต (Compounding) อัด Lot เพิ่มนิดหน่อย
        if account.daily_pl > 0 and distance_to_floor_pct > 5.0:
            risk_modifier = min(1.25, risk_modifier * 1.15)
            risk_reason.append(f"House Money (+{account.daily_pl:.2f})")

    # ─── 4.9 Daily Profit Lock (Target: 600 - 2,000 THB) ───
    # Target 1 (600 THB ≈ 18 USD): Reduce risk to lock in some profit.
    # Target 2 (2,000 THB ≈ 60 USD): Stop trading for the day to preserve capital.
    daily_target_usd = getattr(settings, 'daily_target_amount', 18.0)
    hard_cap_usd = getattr(settings, 'daily_hard_cap', 60.0)
    
    if account.daily_pl >= hard_cap_usd:
        logger.info("daily_hard_cap_reached", extra={
            "symbol": decision.symbol,
            "daily_pl": round(account.daily_pl, 2),
            "hard_cap": hard_cap_usd,
            "action": "BLOCK_TRADE"
        })
        return BlockReason.DAILY_PROFIT_TARGET_REACHED
        
    if account.daily_pl >= daily_target_usd:
        risk_modifier *= 0.2  # 80% reduction to "Protect the House"
        risk_reason.append(f"Profit Lock Triggered (+{account.daily_pl:.2f})")

    final_risk_pct = base_risk_pct * risk_modifier

    if risk_reason:
        logger.info("risk_dynamic_adjusted", extra={
            "symbol": decision.symbol,
            "base_pct": float(base_risk_pct),
            "final_pct": float(round(final_risk_pct, 2)),
            "modifier": float(round(risk_modifier, 2)),
            "reasons": risk_reason
        })
    
    # --- คำนวณความเสี่ยง USD ---
    max_risk_usd = account.equity * (final_risk_pct / 100)
    
    # --- คำนวณ SL distance ---
    # ใช้ entry_price จาก MT5 หรือประมาณจาก take_profit/SL ratio
    if entry_price:
        price = entry_price
    elif decision.take_profit and actual_sl:
        # ประมาณ entry จากจุดกึ่งกลาง SL-TP
        price = (actual_sl + decision.take_profit) / 2
    else:
        logger.error("no_entry_price", extra={"symbol": decision.symbol})
        return BlockReason.LOT_SIZE_INVALID  # ไม่สามารถคำนวณได้ถ้าไม่มี entry price
        
    raw_sl_distance = abs(price - actual_sl)
    
    # ─── 4.8.5 Minimum SL Distance Guard ───
    # If a strategy sends an extremely tight SL (e.g., 0.01) it will cause 
    # the lot size equation (Risk / SL_Distance) to explode.
    # We enforce a minimum SL distance of 1.0 full point (10 pips) for Gold 
    # and 0.001 (10 pips) for Forex to ensure math stability.
    min_sl_dist = 1.0 if "XAUUSD" in decision.symbol.upper() or "BTCUSD" in decision.symbol.upper() else 0.001
    
    if raw_sl_distance < min_sl_dist:
        logger.warning("tight_sl_adjusted", extra={
            "symbol": decision.symbol,
            "original_distance": raw_sl_distance,
            "new_distance": min_sl_dist
        })
        raw_sl_distance = min_sl_dist

    sl_distance = round(raw_sl_distance, profile.digits)
    
    if sl_distance <= 0:
        logger.error("sl_distance_zero", extra={"symbol": decision.symbol})
        return BlockReason.NO_STOP_LOSS

    # --- คำนวณ lot ---
    # lot = risk_usd / (sl_distance × contract_size)
    raw_lot = max_risk_usd / (sl_distance * profile.contract_size)

    # --- ตรวจว่า lot ที่ต้องการต่ำกว่า minimum ---
    # User Custom Minimum Lots (In Broker Lot terms, e.g. CENT lots)
    # XAUUSDc: 0.2 (High Conf: 0.8)
    # XAGUSDc: 0.1 (High Conf: 0.5)
    # BTCUSDc: 0.4 (High Conf: 1.0)
    adapter = get_adapter(settings)
    custom_min_broker = adapter.to_broker_lots(profile.volume_min)
    
    is_high_conf = getattr(decision, "confidence", 0.0) >= 0.85
    
    if "XAUUSD" in decision.symbol.upper():
        custom_min_broker = 0.01  # Hardcoded min 0.01 for XAU
    elif "XAGUSD" in decision.symbol.upper():
        custom_min_broker = 0.01  # Hardcoded min 0.01 for XAG
    elif "BTCUSD" in decision.symbol.upper():
        custom_min_broker = 0.01  # Hardcoded min 0.01 for BTC

    # แปลงกลับเป็น Standard Lot (internal) เพื่อเทียบกับ raw_lot
    custom_min_std = adapter.from_broker_lots(custom_min_broker)
    
    if raw_lot < custom_min_std:
        logger.info("lot_below_minimum", extra={
            "symbol": decision.symbol,
            "raw_lot": round(raw_lot, 8),
            "custom_min_std": custom_min_std,
            "custom_min_broker": custom_min_broker,
            "risk_usd": round(max_risk_usd, 2),
            "equity": round(account.equity, 2),
            "note": f"Account too small for minimum lot {custom_min_broker} at {final_risk_pct:.1f}% risk",
            "stage": "risk",
            "result": "blocked",
        })
        return BlockReason.LOT_SIZE_INVALID

    # --- ปัดเศษตาม volume step (ปัดลงเสมอ) ---
    lot = round_lot(raw_lot, profile)
    
    # --- ตรวจสอบ lot valid ---
    if not is_lot_valid(lot, profile):
        logger.warning("lot_size_invalid", extra={
            "symbol": decision.symbol,
            "raw_lot": raw_lot,
            "rounded_lot": lot,
            "min": profile.volume_min,
            "max": profile.volume_max,
        })
        return BlockReason.LOT_SIZE_INVALID

    # --- คำนวณความเสี่ยง USD จริง ---
    actual_risk_usd = lot * profile.contract_size * sl_distance
    actual_risk_pct = (actual_risk_usd / account.equity * 100) if account.equity > 0 else 0

    # --- ตรวจสอบความเสี่ยงไม่เกินลิมิต (ใช้ HARD CAP, ไม่ใช่ settings) ---
    effective_max = min(settings.max_risk_per_trade_pct, HARD_MAX_RISK_PCT)
    
    # ─── SMART SIZING: ลด Lot ลงอัตโนมัติถ้าความเสี่ยงเกิน (แทนที่จะบล็อกทิ้ง) ───
    if actual_risk_pct > effective_max:
        logger.info("risk_exceeded_auto_scale_down", extra={
            "symbol": decision.symbol,
            "original_risk_pct": round(actual_risk_pct, 2),
            "max_allowed_pct": effective_max,
            "action": "Stepping down lot size to fit risk limits"
        })
        
        # คำนวณ Lot ใหม่แบบ Reverse แบบเป๊ะๆ ไม่ให้เกิน % ที่กำหนด
        allowed_risk_usd = account.equity * (effective_max / 100)
        safe_raw_lot = allowed_risk_usd / (sl_distance * profile.contract_size)
        
        # ถ้า Safe Lot ต่ำกว่าขั้นต่ำที่ Broker รองรับ ให้บังคับบล็อกเลยอันนี้ช่วยไม่ได้
        if safe_raw_lot < custom_min_std:
            logger.warning("risk_exceeded_cannot_scale", extra={
                "symbol": decision.symbol,
                "safe_raw_lot": safe_raw_lot,
                "custom_min_std": custom_min_std,
            })
            return BlockReason.RISK_EXCEEDED
            
        # ปัดเศษลงตาม Volume Step อีกรอบเพื่อให้ชัวร์ว่าส่งคำสั่งผ่าน
        lot = round_lot(safe_raw_lot, profile)
        
        # คำนวณใหม่เพื่อไว้ส่งใน OrderPlan
        actual_risk_usd = lot * profile.contract_size * sl_distance
        actual_risk_pct = (actual_risk_usd / account.equity * 100) if account.equity > 0 else 0

    # --- Lot cap (retail safety) ---
    adapter = get_adapter(settings)
    MAX_LOT_CAP = adapter.get_max_lot_cap(decision.symbol)
    
    if lot > MAX_LOT_CAP:
        logger.warning("lot_capped", extra={
            "symbol": decision.symbol,
            "raw_lot": lot,
            "capped_to": MAX_LOT_CAP,
        })
        lot = MAX_LOT_CAP

    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 2: TIERED COMPOUNDING ENGINE (DYNAMIC CAP EVALUATOR)
    # ═══════════════════════════════════════════════════════════════════════════
    broker_lot = adapter.to_broker_lots(lot, decision.symbol) if adapter else lot
    
    # Evaluate Tiers
    dynamic_hard_cap = HARD_MAX_LOT_DEFAULT
    tier_level_str = "Fallback/Default"
    
    if _TIER_CFG.get("enabled", False) and isinstance(_TIER_CFG.get("tiers"), list):
        # Find the highest tier where equity >= min_equity
        valid_tiers = [t for t in cast(list, _TIER_CFG["tiers"]) if account.equity >= t.get("min_equity", 0)]
        if valid_tiers:
            active_tier = max(valid_tiers, key=lambda t: t.get("min_equity", 0))
            sym_key = decision.symbol.upper().rstrip("C")
            if "XAUUSD" in sym_key:
                dynamic_hard_cap = active_tier.get("max_lot_XAUUSD", HARD_MAX_LOT_DEFAULT)
                tier_level_str = f"Tier > ${active_tier.get('min_equity')}"
            elif "XAGUSD" in sym_key:
                dynamic_hard_cap = active_tier.get("max_lot_XAGUSD", HARD_MAX_LOT_DEFAULT)
                tier_level_str = f"Tier > ${active_tier.get('min_equity')}"
            elif "BTCUSD" in sym_key:
                dynamic_hard_cap = active_tier.get("max_lot_BTCUSD", HARD_MAX_LOT_DEFAULT)
                tier_level_str = f"Tier > ${active_tier.get('min_equity')}"

    if broker_lot > dynamic_hard_cap:
        logger.warning("TIER_CAP_TRIGGERED", extra={
            "symbol": decision.symbol,
            "broker_lot_before": broker_lot,
            "dynamic_hard_cap": dynamic_hard_cap,
            "tier_level": tier_level_str,
            "action": "Scaled down to fit Tier rules",
        })
        broker_lot = dynamic_hard_cap
        lot = adapter.from_broker_lots(broker_lot)
        # Recalculate actual risk
        actual_risk_usd = lot * profile.contract_size * sl_distance
        actual_risk_pct = (actual_risk_usd / account.equity * 100) if account.equity > 0 else 0

    logger.info("lot_calculated", extra={
        "symbol": decision.symbol,
        "lot_std": lot,
        "lot_broker": broker_lot,
        "equity": round(account.equity, 2),
        "risk_usd": round(actual_risk_usd, 2),
        "risk_pct": round(actual_risk_pct, 2),
        "sl_distance": round(sl_distance, profile.digits),
        "max_risk_usd": round(max_risk_usd, 2),
        "stage": "risk",
        "result": "ok",
    })

    return OrderPlan(
        symbol=decision.symbol,
        action=decision.action,
        lot_size=lot,
        stop_loss=actual_sl,
        take_profit=decision.take_profit,
        risk_usd=round(actual_risk_usd, 2),
        risk_pct=round(actual_risk_pct, 2),
        entry_price=entry_price,
        strategy_name=decision.strategy_name,
        comment=f"AG|{decision.strategy_name}|R{round(actual_risk_pct, 1)}%",
        magic=888888, # Bot Magic Number
    )
