"""
PatternScorer V3 — Intelligent confidence boost/dampen จาก Candlestick Patterns.

Upgrades over V2:
    1. Context Awareness — pattern near S/R, after BOS/CHoCH, at OB zone → boost
    2. Pattern Sequence Learning — known high-WR sequences → bonus
    3. Per-Symbol Adaptive Weights — replace hardcoded STRENGTH_BOOST_MAP with learned weights
    4. Multi-TF weighting — H1 patterns weighted higher than M5

กฎสำคัญ:
    - Pattern ไม่ override Risk Engine — เป็นแค่ advisor
    - Max boost +0.40 / min -0.30
    - Confluence (2+ aligned) → bonus
    - Strong counter-signal → should_skip = True
"""

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.brain.pattern_detector import PatternSignal

logger = get_logger(__name__,)

# ── Configuration ──────────────────────────────────────────────────
MAX_BOOST = 0.40           # raised from 0.30 to allow more confluence
MAX_DAMPEN = -0.30         # สูงสุดที่ลดได้
CONFLUENCE_BONUS = 0.10    # bonus เมื่อ 2+ patterns ชี้ทิศเดียว
MTF_CONFLUENCE_BONUS = 0.08  # bonus when M5+H1 align
HWR_THRESHOLD = 0.60       # win_rate ≥ 60% ถือว่า High Win Rate
HWR_BOOST = 0.05           # boost เพิ่มเมื่อ pattern มี HWR
STRONG_COUNTER_THRESHOLD = 0.70  # strength > 0.7 ที่สวนทาง = flags conflict
SKIP_COUNTER_COUNT = 2     # 2+ strong counter-patterns → should_skip

# Context boost values
CONTEXT_SR_BOOST = 0.08     # pattern near S/R zone
CONTEXT_BOS_BOOST = 0.10    # pattern after BOS
CONTEXT_OB_BOOST = 0.08     # pattern at Order Block
CONTEXT_FVG_BOOST = 0.06    # pattern + FVG alignment
CONTEXT_CHOCH_BOOST = 0.10  # pattern after CHoCH (reversal)

# Sequence bonus
SEQUENCE_MIN_WR = 0.65      # min WR for sequence bonus
SEQUENCE_BONUS_MAX = 0.10   # max bonus from sequence matching

# Pattern base boost ตาม strength tier (fallback when no learned weights)
STRENGTH_BOOST_MAP = {
    0.80: 0.12,   # Head & shoulders, Double top/bottom, CHoCH, OB
    0.70: 0.10,   # Pin bar, BOS, FVG, Confirmed hammer
    0.60: 0.07,   # Marubozu, Three soldiers
    0.50: 0.05,   # Inside bar, Basic doji
    0.00: 0.03,   # Weak signals
}


@dataclass
class PatternScore:
    """ผลลัพธ์จาก PatternScorer."""
    confidence_boost: float = 0.0           # -0.3 ถึง +0.4
    aligned_patterns: list[str] = field(default_factory=list)
    conflicting_patterns: list[str] = field(default_factory=list)
    should_skip: bool = False
    reason: str = ""
    pattern_count: int = 0
    strongest_pattern: str = ""
    context_boost: float = 0.0      # boost from context awareness
    sequence_boost: float = 0.0     # boost from sequence matching
    adaptive_applied: bool = False  # True if per-symbol weights were used


class PatternScorer:
    """
    Intelligent confidence scorer using candlestick patterns, context, and learning.

    V3 Features:
        - Context Awareness (S/R, BOS, OB, FVG proximity)
        - Pattern Sequence matching from brain.db
        - Per-Symbol Adaptive Weights from pattern_performance
        - Multi-TF weighting (H1 patterns > M5)

    Usage:
        scorer = PatternScorer(memory_store=brain_memory)
        score = scorer.score(
            signals=pattern_signals,
            strategy_direction="BUY",
            symbol="XAUUSDc",
            regime="TRENDING_UP",
        )
        decision.confidence += score.confidence_boost
    """

    def __init__(self, memory_store=None):
        """
        Args:
            memory_store: MemoryStore สำหรับดึง historical win_rate (optional)
        """
        self.memory_store = memory_store
        self._adaptive_cache: dict[str, dict] = {}  # symbol → {weights, ts}
        self._cache_ttl = 300  # 5 minutes

    def score(
        self,
        signals: list,
        strategy_direction: str,
        symbol: str = "",
        regime: str = "UNKNOWN",
    ) -> PatternScore:
        """
        คำนวณ confidence boost จาก pattern signals.

        Scoring Pipeline:
            1. Per-pattern base boost (adaptive or hardcoded)
            2. HWR bonus from brain.db
            3. Context awareness boost
            4. Sequence match bonus
            5. Confluence bonus (M5+H1, multi-pattern)
            6. Conflicting pattern dampening
            7. Clamp to [MAX_DAMPEN, MAX_BOOST]
        """
        result = PatternScore()

        if not signals or strategy_direction == "HOLD":
            result.reason = "ไม่มี pattern หรือ HOLD"
            return result

        result.pattern_count = len(signals)

        # Map strategy direction → expected pattern direction
        expected_dir = "bullish" if strategy_direction == "BUY" else "bearish"
        counter_dir = "bearish" if strategy_direction == "BUY" else "bullish"

        # ── Load data from brain.db ──
        hwr_map = self._get_historical_win_rates(symbol, regime)
        adaptive_weights = self._get_adaptive_weights(symbol, regime)
        result.adaptive_applied = bool(adaptive_weights)

        # ── Classify signals ──
        total_boost = 0.0
        aligned = []
        conflicting = []
        strong_counters = 0
        has_m5 = False
        has_h1 = False

        # Build context map from signals themselves
        context = self._extract_context(signals, expected_dir)

        for sig in signals:
            name = sig.name
            direction = sig.direction
            strength = sig.strength
            tf = getattr(sig, "timeframe", "M5")

            # Track TF alignment
            if tf == "M5" and direction == expected_dir:
                has_m5 = True
            elif tf == "H1" and direction == expected_dir:
                has_h1 = True

            # ── Compute base boost (adaptive or hardcoded) ──
            if adaptive_weights and name in adaptive_weights:
                # Per-symbol learned weight × base boost
                base_boost = self._strength_to_boost(strength)
                base_boost *= adaptive_weights[name]
            else:
                base_boost = self._strength_to_boost(strength)

            # ── Aligned: pattern ชี้ทิศเดียวกับ signal ──
            if direction == expected_dir:
                boost = base_boost

                # HWR bonus: pattern นี้เคยชนะบ่อย
                hist_wr = hwr_map.get(name, 0)
                if hist_wr >= HWR_THRESHOLD:
                    boost += HWR_BOOST

                # Context boost
                ctx_boost = self._compute_context_boost(sig, context, expected_dir)
                boost += ctx_boost
                result.context_boost += ctx_boost

                total_boost += boost
                aligned.append(name)

            # ── Conflicting: pattern สวนทาง ──
            elif direction == counter_dir:
                total_boost -= base_boost * 0.7
                conflicting.append(name)

                if strength >= STRONG_COUNTER_THRESHOLD:
                    strong_counters += 1

            # neutral + strong → slight dampen
            elif direction == "neutral" and strength >= 0.5:
                total_boost -= 0.02

        # ── Confluence bonuses ──
        if len(aligned) >= 2:
            total_boost += CONFLUENCE_BONUS

        # MTF confluence: M5 + H1 aligned
        if has_m5 and has_h1:
            total_boost += MTF_CONFLUENCE_BONUS

        # ── Sequence bonus ──
        seq_bonus = self._compute_sequence_bonus(signals, symbol, regime)
        total_boost += seq_bonus
        result.sequence_boost = seq_bonus

        # ── Should skip? ──
        should_skip = strong_counters >= SKIP_COUNTER_COUNT

        # ── Clamp ──
        confidence_boost = max(MAX_DAMPEN, min(MAX_BOOST, total_boost))

        # ── Build result ──
        result.confidence_boost = round(confidence_boost, 4)
        result.aligned_patterns = aligned
        result.conflicting_patterns = conflicting
        result.should_skip = should_skip
        result.strongest_pattern = signals[0].name if signals else ""

        # ── Reason text ──
        parts = []
        if aligned:
            parts.append(f"สนับสนุน: {', '.join(aligned[:3])}")
        if conflicting:
            parts.append(f"ขัดแย้ง: {', '.join(conflicting[:3])}")
        if result.context_boost > 0:
            parts.append(f"context+{result.context_boost:.2f}")
        if seq_bonus > 0:
            parts.append(f"seq+{seq_bonus:.2f}")
        if should_skip:
            parts.append("⚠️ SKIP — pattern สวนทางแรง")
        if not parts:
            parts.append("ไม่มี pattern ที่เกี่ยวข้อง")
        result.reason = " | ".join(parts)

        return result

    # ================================================================
    # Context Awareness (#5)
    # ================================================================

    def _extract_context(self, signals: list, expected_dir: str) -> dict:
        """Extract contextual information from pattern signals."""
        ctx = {
            "has_sr": False,
            "has_bos": False,
            "has_choch": False,
            "has_ob": False,
            "has_fvg": False,
            "sr_level": 0.0,
            "ob_zone": None,
            "fvg_zone": None,
        }

        for sig in signals:
            name = sig.name
            details = sig.details if hasattr(sig, "details") else {}

            if name in ("near_support", "near_resistance"):
                ctx["has_sr"] = True
                ctx["sr_level"] = details.get("level", 0)

            elif "bos" in name:
                ctx["has_bos"] = True

            elif "choch" in name:
                ctx["has_choch"] = True

            elif "order_block" in name:
                ctx["has_ob"] = True
                ctx["ob_zone"] = (
                    details.get("ob_low", 0),
                    details.get("ob_high", 0),
                )

            elif "fvg" in name:
                ctx["has_fvg"] = True
                ctx["fvg_zone"] = (
                    details.get("fvg_bottom", 0),
                    details.get("fvg_top", 0),
                )

        return ctx

    def _compute_context_boost(self, sig, context: dict, expected_dir: str) -> float:
        """Compute additional boost based on pattern context."""
        boost = 0.0
        direction = sig.direction

        if direction != expected_dir:
            return 0.0

        # Pattern near S/R zone
        if context["has_sr"]:
            boost += CONTEXT_SR_BOOST

        # Pattern after BOS (trend continuation confirmed)
        if context["has_bos"]:
            boost += CONTEXT_BOS_BOOST

        # Pattern after CHoCH (reversal confirmed)
        if context["has_choch"]:
            boost += CONTEXT_CHOCH_BOOST

        # Pattern at Order Block zone
        if context["has_ob"]:
            boost += CONTEXT_OB_BOOST

        # Pattern + FVG alignment
        if context["has_fvg"]:
            boost += CONTEXT_FVG_BOOST

        return round(boost, 4)

    # ================================================================
    # Pattern Sequence Learning (#3)
    # ================================================================

    def _compute_sequence_bonus(
        self, signals: list, symbol: str, regime: str,
    ) -> float:
        """Check if current pattern combo matches a high-WR sequence."""
        if not self.memory_store or not symbol or len(signals) < 2:
            return 0.0

        try:
            # Build current sequence from aligned patterns (top 3 by strength)
            sorted_sigs = sorted(signals, key=lambda s: s.strength, reverse=True)
            names = [s.name for s in sorted_sigs[:3]]
            current_seq = "→".join(names)

            best_seqs = self.memory_store.get_best_sequences(
                symbol=symbol, regime=regime, min_trades=3, limit=20,
            )
            if not best_seqs:
                return 0.0

            # Check for exact or partial match
            for seq_data in best_seqs:
                stored_seq = seq_data.get("sequence", "")
                wr = seq_data.get("win_rate", 0)

                if wr < SEQUENCE_MIN_WR:
                    continue

                # Exact match
                if current_seq == stored_seq:
                    bonus = min(SEQUENCE_BONUS_MAX, (wr - 0.5) * 0.4)
                    return round(bonus, 4)

                # Partial match (2 of 3 patterns match)
                stored_parts = set(stored_seq.split("→"))
                current_parts = set(names)
                overlap = len(stored_parts & current_parts)
                if overlap >= 2 and wr >= 0.70:
                    bonus = min(SEQUENCE_BONUS_MAX * 0.6, (wr - 0.5) * 0.25)
                    return round(bonus, 4)

            return 0.0
        except Exception:
            return 0.0

    # ================================================================
    # Core helpers
    # ================================================================

    def _strength_to_boost(self, strength: float) -> float:
        """แปลง strength → base boost."""
        for threshold, boost in sorted(STRENGTH_BOOST_MAP.items(), reverse=True):
            if strength >= threshold:
                return boost
        return 0.03

    def _get_historical_win_rates(self, symbol: str, regime: str) -> dict[str, float]:
        """ดึง win_rate ของแต่ละ pattern จาก brain.db."""
        if not self.memory_store or not symbol:
            return {}

        try:
            patterns = self.memory_store.get_best_patterns(
                symbol=symbol,
                regime=regime,
                min_trades=5,
                top_n=20,
            )
            return {p["pattern_name"]: p.get("win_rate", 0) for p in patterns}
        except Exception:
            return {}

    def _get_adaptive_weights(self, symbol: str, regime: str) -> dict[str, float]:
        """
        Get per-symbol adaptive weights from brain.db (#6).
        Uses cache with 5-min TTL.
        """
        if not self.memory_store or not symbol:
            return {}

        import time
        cache_key = f"{symbol}_{regime}"
        cached = self._adaptive_cache.get(cache_key)
        if cached and (time.time() - cached.get("ts", 0)) < self._cache_ttl:
            return cached.get("weights", {})

        try:
            weights = self.memory_store.get_pattern_weights_for_symbol(
                symbol=symbol, regime=regime, min_trades=5,
            )
            self._adaptive_cache[cache_key] = {
                "weights": weights, "ts": time.time(),
            }
            return weights
        except Exception:
            return {}
