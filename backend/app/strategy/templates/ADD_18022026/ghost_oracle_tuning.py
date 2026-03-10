"""
Ghost Oracle Tuning — Centralized per-symbol configuration.

Used by ghost_protocol.py and oracle_strategy.py for:
    - ATR-based SL/TP multipliers
    - Order Block / FVG detection thresholds
    - Session filters (when to trade)
    - Spread thresholds (reject high spread)
    - Risk overrides (BTCUSD → 0.5% risk)

To auto-tune, update values via SQLite optimized_settings table
and call get_tuning(symbol, override={}).
"""

from typing import Dict, Any, Optional

# ═══════════════════════════════════════════════════════════════════
# Per-Symbol Tuning Map
# ═══════════════════════════════════════════════════════════════════

GHOST_ORACLE_TUNING: Dict[str, Dict[str, Any]] = {
    "XAUUSD": {
        # ── SL/TP ──
        "atr_sl_mult": 2.75,       # Optimized (was 1.8)
        "atr_tp_mult": 1.25,       # Optimized (was 2.7)
        "min_rr": 1.0,             # Minimum R:R to accept signal
        # ── Partial TP ──
        "partial_tp_r": 0.7,       # Partial close at +0.7R
        "partial_tp_pct": 50,      # Close 50% at partial TP
        # ── Detection ──
        "ob_min_score": 65,
        "wick_ratio": 0.6,
        "lookback": 20,
        "volume_spike_mult": 1.5,
        "min_confidence": 70,      # Optimized (was 55)
        # ── Session ──
        "sessions": ["LONDON", "NY", "OVERLAP"],
        # ── Spread ──
        "max_spread_points": 30,   # reject if spread > 30 points
        # ── Risk Override ──
        "risk_pct": 1.0,           # % risk per trade
        # ── Hold Time ──
        "max_hold_candles": 48,    # Max ~4h on M5 before force-close flag
        # ── Regime ──
        "ranging_confidence_penalty": 20,  # reduce confidence in ranging
        # ── Contract ──
        "contract_size": 100.0,
        "point": 0.01,
        # ── Fibonacci Engine (Gold: standard structure) ──
        "fib_pivot_n": 3,
        "fib_atr_swing_mult": 2.5,
        "fib_wick_ratio": 0.25,              # lowered for metals
        "fib_zone_atr_mult": 0.20,           # wider zone for gold
        "fib_sl_buffer_atr": 0.40,
        "fib_partial_pct": 50,
        "fib_min_atr_pct": 0.03,
        "fib_cooldown_bars": 4,              # 1h on M15
        "fib_max_swing_age": 150,
        "min_confidence": 60,
    },

    "XAGUSD": {
        "atr_sl_mult": 2.0,        # Slightly wider for silver volatility
        "atr_tp_mult": 3.0,        # Targeting larger moves (1:1.5)
        "min_rr": 1.0,             # Minimum R:R
        "partial_tp_r": 1.0,       # Secure profit at 1R
        "partial_tp_pct": 50,
        "ob_min_score": 70,
        "wick_ratio": 0.5,
        "lookback": 20,            # Standard lookback
        "volume_spike_mult": 1.5,
        "min_confidence": 60,      # Stricter confidence (was 50)
        "sessions": ["LONDON", "NY"],
        "max_spread_points": 30,
        "risk_pct": 1.0,           # Standard risk
        "max_hold_candles": 48,    # Max ~4h on M5
        "ranging_confidence_penalty": 25,
        "contract_size": 5000.0,
        "point": 0.001,
        # ── Fibonacci Engine (Silver: wider zones for volatility) ──
        "fib_pivot_n": 3,
        "fib_atr_swing_mult": 2.0,
        "fib_wick_ratio": 0.20,              # lower for silver
        "fib_zone_atr_mult": 0.25,           # wider zone
        "fib_sl_buffer_atr": 0.45,
        "fib_partial_pct": 50,
        "fib_min_atr_pct": 0.04,
        "fib_cooldown_bars": 4,
        "fib_max_swing_age": 150,
        "min_confidence": 60,
    },

    "EURUSD": {
        "atr_sl_mult": 2.0,        # Optimized (was 1.5)
        "atr_tp_mult": 1.5,        # Optimized (was 2.5) — Secure profits
        "min_rr": 1.0,
        "partial_tp_r": 0.8,
        "partial_tp_pct": 50,
        "ob_min_score": 60,
        "wick_ratio": 0.5,
        "lookback": 30,
        "volume_spike_mult": 1.3,
        "min_confidence": 60,      # Optimized (was 50) — Higher quality
        "sessions": ["LONDON", "NY", "OVERLAP"],
        "max_spread_points": 15,
        "risk_pct": 1.0,
        "max_hold_candles": 60,    # Max ~5h on M5
        "ranging_confidence_penalty": 25,
        "contract_size": 100000.0,
        "point": 0.00001,
        # ── Fibonacci Engine (EUR: tighter zones, clean structure) ──
        "fib_pivot_n": 3,
        "fib_atr_swing_mult": 2.0,
        "fib_wick_ratio": 0.30,
        "fib_zone_atr_mult": 0.15,
        "fib_sl_buffer_atr": 0.30,
        "fib_partial_pct": 50,
        "fib_min_atr_pct": 0.02,
        "fib_cooldown_bars": 4,
        "fib_max_swing_age": 150,
        "min_confidence": 60,
    },

    "GBPUSD": {
        "atr_sl_mult": 1.8,
        "atr_tp_mult": 2.7,
        "min_rr": 1.0,
        "partial_tp_r": 0.8,
        "partial_tp_pct": 50,
        "ob_min_score": 65,
        "wick_ratio": 0.55,
        "lookback": 25,
        "volume_spike_mult": 1.4,
        "min_confidence": 55,
        "sessions": ["LONDON", "OVERLAP"],
        "max_spread_points": 20,
        "risk_pct": 1.0,
        "max_hold_candles": 48,    # Max ~4h on M5
        "ranging_confidence_penalty": 20,
        "contract_size": 100000.0,
        "point": 0.00001,
        # ── Fibonacci Engine (GBP: wider pivot for volatile structure) ──
        "fib_pivot_n": 3,                    # lowered from 4 for more swings
        "fib_atr_swing_mult": 2.0,           # lowered for more signals
        "fib_wick_ratio": 0.30,
        "fib_zone_atr_mult": 0.18,
        "fib_sl_buffer_atr": 0.40,
        "fib_partial_pct": 50,
        "fib_min_atr_pct": 0.03,
        "fib_cooldown_bars": 4,
        "fib_max_swing_age": 150,
        "min_confidence": 60,
    },

    "USDJPY": {
        "atr_sl_mult": 1.5,
        "atr_tp_mult": 2.5,
        "min_rr": 1.0,
        "partial_tp_r": 0.8,
        "partial_tp_pct": 50,
        "ob_min_score": 60,
        "wick_ratio": 0.5,
        "lookback": 30,
        "volume_spike_mult": 1.3,
        "min_confidence": 50,
        "sessions": ["TOKYO", "NY"],
        "max_spread_points": 15,
        "risk_pct": 1.0,
        "max_hold_candles": 60,    # Max ~5h on M5
        "ranging_confidence_penalty": 20,
        "contract_size": 100000.0,
        "point": 0.001,
        # ── Fibonacci Engine (JPY: tighter zones, clean trends) ──
        "fib_pivot_n": 3,
        "fib_atr_swing_mult": 2.0,
        "fib_wick_ratio": 0.30,
        "fib_zone_atr_mult": 0.15,
        "fib_sl_buffer_atr": 0.30,
        "fib_partial_pct": 50,
        "fib_min_atr_pct": 0.02,
        "fib_cooldown_bars": 4,
        "fib_max_swing_age": 150,
        "min_confidence": 60,
    },

    "BTCUSD": {
        # ── SL/TP — OPTIMIZED 2026-02-15 (SAFETY-FIRST) ──
        # BackTest: Oracle PF 1.28 R:R 5.47, WR Pro DD $6.9K (lowest)
        # Key: Wide SL anti-hunt, BUT cap risk for portfolio safety
        "atr_sl_mult": 3.0,        # Wide stop to prevent wicking
        "atr_tp_mult": 6.0,        # 1:2 R:R (3.0 * 2.0) — balanced
        "min_rr": 2.0,             # Min 1:2 reward
        # ── Partial TP (Lock Profits Fast) ──
        "partial_tp_r": 1.0,       # Lock 50% at +1.0R (was 1.5R → too greedy)
        "partial_tp_pct": 50,      # Close 50% at partial TP
        # ── Detection (Higher Quality) ──
        "ob_min_score": 75,
        "wick_ratio": 0.5,
        "lookback": 15,
        "volume_spike_mult": 1.5,
        "min_confidence": 70,      # ↑ from 65 → fewer but better trades
        # ── Session / Risk (SAFETY) ──
        # User trades BTC 17:00-08:00 Thai (10:00-01:00 UTC)
        # = London PM + NY full + Asian early
        "sessions": ["LONDON", "NY", "OVERLAP", "ASIA"],
        "max_spread_points": 500,   # Wide spread allowed for crypto
        "risk_pct": 1.0,            # ↓ from 2.0% → HALF risk for safety
        "max_hold_candles": 24,     # ↓ from 36 → exit faster (~2h)
        "ranging_confidence_penalty": 15,  # ↑ stricter ranging penalty
        "contract_size": 1.0,
        "point": 0.01,
        # ── Fibonacci Engine (BTC: wider thresholds) ──
        "fib_pivot_n": 5,
        "fib_atr_swing_mult": 3.5,
        "fib_wick_ratio": 0.30,
        "fib_zone_atr_mult": 0.25,
        "fib_sl_buffer_atr": 0.35,
        "fib_partial_pct": 50,
        # ── Fusion Engine (btc_fib_pro) ──
        "fusion_sniper_threshold": 60,
        "fusion_trade_threshold": 50,
        "ema_fast": 20,
        "ema_slow": 50,
        "ema_momentum": 9,
        "rsi_period": 14,
        "adx_min": 25.0,
        "rr_ratio": 1.5,
    },
}

# ─── Default fallback (unknown symbols) ─────────────────────────────
_DEFAULT_TUNING: Dict[str, Any] = {
    "atr_sl_mult": 2.0,
    "atr_tp_mult": 3.0,
    "min_rr": 1.0,                 # Minimum R:R to accept signal
    "partial_tp_r": 0.0,           # 0 = disabled by default
    "partial_tp_pct": 50,          # % to close at partial TP
    "ob_min_score": 70,
    "wick_ratio": 0.5,
    "lookback": 25,
    "volume_spike_mult": 1.5,
    "min_confidence": 55,
    "sessions": ["LONDON", "NY"],
    "max_spread_points": 30,
    "risk_pct": 1.0,
    "max_hold_candles": 48,        # Default max hold
    "ranging_confidence_penalty": 20,
    "contract_size": 100.0,
    "point": 0.01,
    # ── Fibonacci Engine defaults ──
    "fib_pivot_n": 3,              # Pivot left/right window size
    "fib_atr_swing_mult": 2.5,    # Swing significance: range >= mult * ATR
    "fib_wick_ratio": 0.35,       # Rejection wick confirmation threshold
    "fib_zone_atr_mult": 0.15,   # Zone width = ATR * mult
    "fib_sl_buffer_atr": 0.2,    # SL buffer beyond 0.786 zone
    "fib_partial_pct": 50,        # % to close at TP1 (1.272 ext)
    "fib_min_atr_pct": 0.03,     # Min ATR/price % to avoid dead markets
}


# ═══════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════

def get_tuning(symbol: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Get tuning parameters for a symbol, with optional overrides.

    Usage:
        tuning = get_tuning("XAUUSD")
        tuning = get_tuning("XAUUSD", {"atr_sl_mult": 2.2})
    """
    # Normalize symbol (strip suffix like 'm', 'c', etc.)
    base = _normalize_symbol(symbol)
    result = dict(_DEFAULT_TUNING)
    if base in GHOST_ORACLE_TUNING:
        result.update(GHOST_ORACLE_TUNING[base])
    if overrides:
        result.update(overrides)
    return result


def get_all_symbols() -> list:
    """Return list of all tuned symbols."""
    return list(GHOST_ORACLE_TUNING.keys())


def _normalize_symbol(symbol: str) -> str:
    """
    Strip broker suffixes to match tuning keys.
    Examples: XAUUSDm → XAUUSD, BTCUSDc → BTCUSD
    """
    s = symbol.upper()
    # Common suffixes: m, c, .i, _SB
    for suffix in ["M", "C", ".I", "_SB", ".E", ".R"]:
        if s.endswith(suffix) and len(s) > len(suffix):
            stripped = s[:-len(suffix)]
            if stripped in GHOST_ORACLE_TUNING:
                return stripped
    return s


def is_session_allowed(symbol: str, current_session: str) -> bool:
    """
    Check if current session is in the allowed sessions for symbol.

    Args:
        symbol: Trading symbol
        current_session: Current session name (TOKYO/LONDON/NY/OVERLAP/SYDNEY)

    Returns:
        True if trading is allowed in this session
    """
    tuning = get_tuning(symbol)
    allowed = tuning.get("sessions", ["ALL"])
    if "ALL" in allowed:
        return True
    return current_session.upper() in [s.upper() for s in allowed]


def get_risk_pct(symbol: str) -> float:
    """Get per-trade risk % for symbol (BTCUSD = 0.5%, others = 1.0%)."""
    return get_tuning(symbol).get("risk_pct", 1.0)


def get_contract_spec(symbol: str) -> tuple:
    """Get (contract_size, point) for symbol."""
    t = get_tuning(symbol)
    return t.get("contract_size", 100.0), t.get("point", 0.01)
