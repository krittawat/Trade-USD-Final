"""
Regime Risk Profile — แมป regime → risk profile.

แต่ละ regime มีค่า:
    - lot_multiplier: ตัวคูณ lot (0.0 = ห้ามเทรด)
    - sl_atr: SL เป็นเท่าของ ATR
    - tp_rr: Risk:Reward target
    - strategy_hint: ประเภท strategy ที่แนะนำ

กฎเหล็ก:
    - lot_multiplier <= 0 → BLOCK trade
    - ค่าเหล่านี้ถูก override ได้โดย best_params จาก learning loop
"""

from app.domain.enums import RegimeType
from app.core.logging import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Default Risk Profile per Regime
# ═══════════════════════════════════════════════════════════════════

REGIME_RISK_PROFILES: dict[str, dict] = {
    # ── Strong Trend: เทรดเต็มที่, SL 1.5×ATR, RR 2.0 ──
    RegimeType.STRONG_TREND.value: {
        "lot_multiplier": 1.0,
        "sl_atr": 1.5,
        "tp_rr": 2.0,
        "strategy_hint": "trend_following",
    },
    # ── Trending Up/Down: เหมือน strong trend (backward compat) ──
    RegimeType.TRENDING_UP.value: {
        "lot_multiplier": 1.0,
        "sl_atr": 1.5,
        "tp_rr": 2.0,
        "strategy_hint": "trend_following",
    },
    RegimeType.TRENDING_DOWN.value: {
        "lot_multiplier": 1.0,
        "sl_atr": 1.5,
        "tp_rr": 2.0,
        "strategy_hint": "trend_following",
    },
    # ── Weak Trend: ลด lot 30% ──
    RegimeType.WEAK_TREND.value: {
        "lot_multiplier": 0.7,
        "sl_atr": 1.5,
        "tp_rr": 1.5,
        "strategy_hint": "trend_following",
    },
    # ── Ranging: Mean reversion, lot 50% ──
    RegimeType.RANGING.value: {
        "lot_multiplier": 0.5,
        "sl_atr": 1.0,
        "tp_rr": 1.0,
        "strategy_hint": "mean_reversion",
    },
    # ── Breakout: Momentum entry, กว้าง SL ──
    RegimeType.BREAKOUT.value: {
        "lot_multiplier": 0.8,
        "sl_atr": 2.0,
        "tp_rr": 2.5,
        "strategy_hint": "momentum",
    },
    # ── Fakeout: ไม้เล็ก, fade breakout ──
    RegimeType.FAKEOUT.value: {
        "lot_multiplier": 0.3,
        "sl_atr": 1.0,
        "tp_rr": 1.5,
        "strategy_hint": "fade_breakout",
    },
    # ── High Volatility: ลด 50%, SL กว้าง ──
    RegimeType.HIGH_VOLATILITY.value: {
        "lot_multiplier": 0.5,
        "sl_atr": 2.5,
        "tp_rr": 2.0,
        "strategy_hint": "reduced_size",
    },
    # ── Low Volatility: ไม้เล็ก, mean reversion ──
    RegimeType.LOW_VOLATILITY.value: {
        "lot_multiplier": 0.3,
        "sl_atr": 1.0,
        "tp_rr": 1.0,
        "strategy_hint": "mean_reversion",
    },
    # ── News Spike: ลด lot ──
    RegimeType.NEWS_SPIKE.value: {
        "lot_multiplier": 0.4,
        "sl_atr": 3.0,
        "tp_rr": 2.0,
        "strategy_hint": "reduced_size",
    },
    # ── Liquidity Sweep / Smart Money Trap: รอ reversal ──
    RegimeType.LIQUIDITY_SWEEP.value: {
        "lot_multiplier": 0.3,
        "sl_atr": 1.5,
        "tp_rr": 2.0,
        "strategy_hint": "reversal",
    },
    # ── Accumulation: ไม่เทรด (รอ breakout) ──
    RegimeType.ACCUMULATION.value: {
        "lot_multiplier": 0.0,
        "sl_atr": 0.0,
        "tp_rr": 0.0,
        "strategy_hint": "no_trade",
    },
    # ── Unknown: ปลอดภัยไว้ก่อน ──
    RegimeType.UNKNOWN.value: {
        "lot_multiplier": 0.0,
        "sl_atr": 0.0,
        "tp_rr": 0.0,
        "strategy_hint": "no_trade",
    },
}


def get_risk_profile(regime: RegimeType | str) -> dict:
    """
    ดึง risk profile สำหรับ regime.

    Args:
        regime: RegimeType enum หรือ string

    Returns:
        dict: {lot_multiplier, sl_atr, tp_rr, strategy_hint}
    """
    key = regime.value if isinstance(regime, RegimeType) else regime
    profile = REGIME_RISK_PROFILES.get(key)

    if profile is None:
        logger.warning("regime_risk_profile_missing", extra={"regime": key})
        return REGIME_RISK_PROFILES[RegimeType.UNKNOWN.value]

    return profile.copy()


def is_tradable(regime: RegimeType | str) -> bool:
    """regime นี้เทรดได้ไหม? (lot_multiplier > 0)"""
    profile = get_risk_profile(regime)
    return profile.get("lot_multiplier", 0.0) > 0
